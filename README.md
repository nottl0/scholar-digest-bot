# Scholar Inbox Digest

Fetches the latest papers from Scholar Inbox and sends them to Telegram. Each paper is sent as a formatted message.

## Run locally

Requires Python 3.12 or newer.

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Create a local `.env` file with these values:

```dotenv
SCHOLAR_INBOX_MCP_TOKEN=your_scholar_inbox_token
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
TELEGRAM_CHAT_ID=your_telegram_chat_id
```

Send one digest immediately:

```sh
python digest_bot.py --now
```

Without `--now`, the script stays running and sends a digest daily. The default time is 08:00 UTC; set `DIGEST_TIME=HH:MM` and `TIMEZONE=Area/City` in `.env` to change it.

## GitHub Actions

The included workflow sends a digest daily at 08:00 UTC and can also be started manually from the repository's **Actions** tab. In **Settings → Secrets and variables → Actions**, add these repository secrets:

- `SCHOLAR_INBOX_MCP_TOKEN`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

The workflow runs `digest_bot.py --now`; failures produce a nonzero exit status. Keep `.env` and all credentials out of Git.
