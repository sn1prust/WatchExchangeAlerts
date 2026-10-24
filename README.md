# WatchExchangeAlerts

Get a phone notification the moment someone posts the watch you're hunting on
r/Watchexchange.

The bot checks the subreddit's newest posts every couple of minutes and alerts
you when a title contains one of your keywords or reference numbers. It's a
single Python file with no dependencies, and it's meant to run on a small VPS.

- Match reference numbers (`74033`) or phrases (`tudor prince`)
- Require several terms together: `tudor prince + two tone`
- Exclude terms you don't want
- Edit your keywords without restarting
- Notifications through [ntfy](https://ntfy.sh) (free push notifications), Telegram, Discord, or email

## Quick start

Requires Python 3.9+.

```bash
git clone https://github.com/sn1prust/WatchExchangeAlerts.git
cd WatchExchangeAlerts
cp .env.example .env
nano .env        # set KEYWORDS, USER_AGENT, and at least one notification channel

python3 watchbot.py --test-notify      # sends a test notification
python3 watchbot.py --dry-run --once   # shows what currently matches
python3 watchbot.py                    # runs forever
```

### Phone notifications with ntfy

1. Install the **ntfy** app (iOS / Android).
2. Subscribe to a long, random topic name, e.g. `watch-alerts-7f3k9q2x`.
3. Put the same name in `.env` as `NTFY_TOPIC=...`.

Anyone who knows your topic name can read your alerts, so keep it hard to guess.

### Reddit API credentials (recommended)

Create a **script** app at <https://www.reddit.com/prefs/apps> and put its id and secret in
`REDDIT_CLIENT_ID` / `REDDIT_CLIENT_SECRET`. The bot then uses Reddit's official API.
Without them it falls back to the subreddit's public RSS feed.

Also set `USER_AGENT` to include your Reddit username, as Reddit asks of all clients.

## Running on a VPS (systemd)

```bash
sudo useradd --system --no-create-home --shell /usr/sbin/nologin watchbot
sudo cp -r WatchExchangeAlerts /opt/watchbot
sudo chown -R watchbot:watchbot /opt/watchbot
sudo chmod 600 /opt/watchbot/.env

sudo cp /opt/watchbot/watchbot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now watchbot
```

Useful commands:

```bash
sudo journalctl -u watchbot -n 20 --no-pager   # recent log
sudo journalctl -u watchbot -f                 # follow the log live (Ctrl+C to exit)
sudo nano /opt/watchbot/.env                   # change keywords; applies on the next check
sudo systemctl restart watchbot                # needed only after changing code or non-filter settings
```

On first start the bot records matches that are already on the subreddit without
alerting you, so you only hear about new posts. Set `NOTIFY_ON_FIRST_RUN=1` to change that.

## Responsible use

This is a personal, low-volume notification tool. It does not collect, store, or
republish Reddit content beyond the post IDs it has already alerted you about.

- You are responsible for following [Reddit's User Agreement](https://redditinc.com/policies/user-agreement)
  and [Data API Terms](https://redditinc.com/policies/data-api-terms) when you run it.
- Keep the poll interval reasonable. The bot enforces a 60-second minimum, and 120+ is recommended.
- Use your own User-Agent and, ideally, your own Reddit API app.

This project is not affiliated with or endorsed by Reddit, Inc. or the r/Watchexchange moderators.

## License

[MIT](LICENSE)
