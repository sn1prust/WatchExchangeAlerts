#!/usr/bin/env python3
"""
Watch a subreddit (default r/Watchexchange) for new posts whose TITLE matches
one of your keywords and send you a notification.

Uses Reddit's official API when REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET are set,
otherwise the subreddit's public RSS feed. Personal, low-volume use only.

Standard library only -- no pip install needed. Configure via a .env file next
to this script (see .env.example).

Usage:
  python3 watchbot.py                 run forever
  python3 watchbot.py --once          one check, then exit
  python3 watchbot.py --dry-run       print matches, don't notify
  python3 watchbot.py --test-notify   send a test notification and exit
"""
import argparse
import base64
import json
import logging
import os
import random
import re
import smtplib
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from email.message import EmailMessage
from pathlib import Path

BASE = Path(__file__).resolve().parent


ENV_FILE = BASE / ".env"


def read_env_file(path):
    values = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


for _key, _value in read_env_file(ENV_FILE).items():
    os.environ.setdefault(_key, _value)

SUBREDDIT = os.getenv("SUBREDDIT", "Watchexchange")
MIN_POLL_SECONDS = 60  # be polite to Reddit; anything lower is raised to this
POLL_SECONDS = max(MIN_POLL_SECONDS, int(os.getenv("POLL_SECONDS", "120")))
USER_AGENT = os.getenv("USER_AGENT", "python:watchexchange-alerts:v1.0 (by /u/YOUR_REDDIT_USERNAME)")
STATE_FILE = Path(os.getenv("STATE_FILE", str(BASE / "seen.json")))

# Reddit API app credentials (recommended): https://www.reddit.com/prefs/apps
REDDIT_CLIENT_ID = os.getenv("REDDIT_CLIENT_ID", "")
REDDIT_CLIENT_SECRET = os.getenv("REDDIT_CLIENT_SECRET", "")

# Notification channels -- fill in any (or several) of these
NTFY_TOPIC = os.getenv("NTFY_TOPIC", "")
NTFY_SERVER = os.getenv("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
EMAIL_TO = os.getenv("EMAIL_TO", "")  # a normal email, or an SMS gateway like 5551234567@vtext.com

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("watchbot")

# "tudor prince" -> matches "Tudor Prince", "TUDOR  PRINCE", "tudor-prince", etc.
# "74033" -> matches "74033", "ref74033", "Ref.74033" but not "740331"
def phrase(text):
    text = text.strip()
    if text.isdigit():
        return re.compile(rf"(?<!\d){text}(?!\d)")
    return re.compile(r"\b" + r"[\s\-_]+".join(map(re.escape, text.split())) + r"\b", re.IGNORECASE)


# KEYWORDS / EXCLUDE are re-read from .env whenever it changes, so edits apply without a restart.
FILTERS = {"mtime": "unloaded", "keywords": [], "exclude": [], "patterns": []}


def load_filters():
    mtime = ENV_FILE.stat().st_mtime if ENV_FILE.exists() else None
    if mtime == FILTERS["mtime"]:
        return
    values = read_env_file(ENV_FILE)
    keywords = [k.strip() for k in values.get("KEYWORDS", os.getenv("KEYWORDS", "tudor prince")).split(",") if k.strip()]
    exclude = [k.strip().lower() for k in values.get("EXCLUDE", os.getenv("EXCLUDE", "")).split(",") if k.strip()]
    reloaded = FILTERS["mtime"] != "unloaded"
    # A keyword with "+" needs every part somewhere in the title: "tudor prince + two tone"
    FILTERS.update(mtime=mtime, keywords=keywords, exclude=exclude,
                   patterns=[[phrase(p) for p in kw.split("+") if p.strip()] for kw in keywords])
    log.info("%s filters: keywords=%s exclude=%s", "reloaded" if reloaded else "loaded", keywords, exclude)


# ---------------------------------------------------------------- HTTP helpers

def http(url, data=None, headers=None, timeout=20):
    req = urllib.request.Request(url, data=data, headers={"User-Agent": USER_AGENT, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def post_json(url, payload, headers=None):
    return http(url, json.dumps(payload).encode(), {"Content-Type": "application/json", **(headers or {})})


# ---------------------------------------------------------------- Reddit

_token = {"value": None, "expires": 0}


def oauth_token():
    if _token["value"] and time.time() < _token["expires"] - 60:
        return _token["value"]
    auth = base64.b64encode(f"{REDDIT_CLIENT_ID}:{REDDIT_CLIENT_SECRET}".encode()).decode()
    body = urllib.parse.urlencode({"grant_type": "client_credentials"}).encode()
    data = json.loads(http("https://www.reddit.com/api/v1/access_token", body, {"Authorization": f"Basic {auth}"}))
    _token["value"] = data["access_token"]
    _token["expires"] = time.time() + data.get("expires_in", 3600)
    return _token["value"]


def parse_listing(raw):
    posts = []
    for child in json.loads(raw)["data"]["children"]:
        d = child["data"]
        posts.append({
            "id": d["name"],
            "title": d["title"],
            "url": "https://www.reddit.com" + d["permalink"],
            "author": d.get("author", ""),
        })
    return posts


def fetch_oauth():
    raw = http(f"https://oauth.reddit.com/r/{SUBREDDIT}/new?limit=100&raw_json=1",
               headers={"Authorization": f"Bearer {oauth_token()}"})
    return parse_listing(raw)


def fetch_rss():
    ns = {"a": "http://www.w3.org/2005/Atom"}
    root = ET.fromstring(http(f"https://www.reddit.com/r/{SUBREDDIT}/new/.rss?limit=100"))
    posts = []
    for entry in root.findall("a:entry", ns):
        link = entry.find("a:link", ns)
        author = entry.find("a:author/a:name", ns)
        posts.append({
            "id": entry.findtext("a:id", "", ns),
            "title": entry.findtext("a:title", "", ns),
            "url": link.get("href") if link is not None else "",
            "author": (author.text or "").removeprefix("/u/") if author is not None else "",
        })
    return posts


def fetch_posts():
    if REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET:
        return fetch_oauth()
    return fetch_rss()


# ---------------------------------------------------------------- Notifications

def notify_ntfy(title, body, url):
    post_json(NTFY_SERVER + "/", {"topic": NTFY_TOPIC, "title": title, "message": body,
                                  "click": url, "tags": ["watch"], "priority": 4})


def notify_telegram(title, body, url):
    post_json(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
              {"chat_id": TELEGRAM_CHAT_ID, "text": f"{title}\n\n{body}\n{url}"})


def notify_discord(title, body, url):
    post_json(DISCORD_WEBHOOK_URL, {"content": f"**{title}**\n{body}\n{url}"})


def notify_email(title, body, url):
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = SMTP_USER, EMAIL_TO, title
    msg.set_content(f"{body}\n{url}")
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as s:
        s.starttls()
        s.login(SMTP_USER, SMTP_PASSWORD)
        s.send_message(msg)


def channels():
    out = []
    if NTFY_TOPIC:
        out.append(notify_ntfy)
    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        out.append(notify_telegram)
    if DISCORD_WEBHOOK_URL:
        out.append(notify_discord)
    if SMTP_USER and SMTP_PASSWORD and EMAIL_TO:
        out.append(notify_email)
    return out


def notify(title, body, url):
    sent = False
    for send in channels():
        try:
            send(title, body, url)
            sent = True
        except Exception as e:
            log.error("%s failed: %s", send.__name__, e)
    return sent


# ---------------------------------------------------------------- State

def load_seen():
    if not STATE_FILE.exists():
        return None
    return json.loads(STATE_FILE.read_text())


def save_seen(seen):
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(seen[-5000:]))
    tmp.replace(STATE_FILE)


# ---------------------------------------------------------------- Main loop

def matches(text):
    if any(x in text.lower() for x in FILTERS["exclude"]):
        return False
    return any(all(p.search(text) for p in parts) for parts in FILTERS["patterns"])


def check(seen, dry_run, first_run):
    load_filters()
    posts = fetch_posts()
    seen_set = set(seen)
    new_hits = [p for p in reversed(posts) if p["id"] not in seen_set and matches(p["title"])]
    for post in new_hits:
        log.info("MATCH: %s  %s", post["title"], post["url"])
        if dry_run:
            pass
        elif first_run:
            log.info("  (first run: recording without notifying)")
        elif not notify(f"r/{SUBREDDIT}: keyword match", post["title"], post["url"]):
            log.error("  no notification delivered; will retry next check")
            continue
        seen.append(post["id"])
    if not dry_run and (new_hits or first_run):
        save_seen(seen)
    log.info("checked %d posts, %d new match(es)", len(posts), len(new_hits))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--once", action="store_true", help="check once and exit")
    ap.add_argument("--dry-run", action="store_true", help="print matches, don't notify or save state")
    ap.add_argument("--test-notify", action="store_true", help="send a test notification and exit")
    args = ap.parse_args()

    if args.test_notify:
        if not channels():
            sys.exit("No notification channel configured -- edit .env first.")
        ok = notify("watchbot test", "If you can read this, notifications work.", f"https://www.reddit.com/r/{SUBREDDIT}/new")
        sys.exit(0 if ok else 1)

    if not args.dry_run and not channels():
        sys.exit("No notification channel configured -- edit .env (or use --dry-run).")

    if "YOUR_REDDIT_USERNAME" in USER_AGENT:
        log.warning("set USER_AGENT in .env to include your Reddit username, as Reddit's API rules ask")

    seen = load_seen()
    first_run = seen is None and os.getenv("NOTIFY_ON_FIRST_RUN", "0") != "1"
    seen = seen or []
    log.info("watching r/%s every %ss using %s, notifying via %s", SUBREDDIT, POLL_SECONDS,
             "Reddit API" if REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET else "RSS feed",
             [c.__name__ for c in channels()] or "dry-run")

    backoff = POLL_SECONDS
    while True:
        try:
            check(seen, args.dry_run, first_run)
            first_run = False
            backoff = POLL_SECONDS
        except Exception as e:
            log.error("check failed: %s", e)
            backoff = min(backoff * 2, 1800)
        if args.once:
            break
        time.sleep(backoff + random.uniform(0, 15))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
