import asyncio
import html
import json
import logging
import os
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
from dotenv import load_dotenv
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

load_dotenv()

MCP_URL = "https://mcp.scholar-inbox.com/mcp"
TELEGRAM_LIMIT = 4000  # Telegram max is 4096 chars per message

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
# HTTPX logs full request URLs at INFO; Telegram URLs contain the bot token.
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("digest")


def pick_tool(tools) -> str:
    """Use DIGEST_TOOL if set, otherwise find a tool with 'digest' in its name."""
    if os.getenv("DIGEST_TOOL"):
        return os.environ["DIGEST_TOOL"]
    matches = [t.name for t in tools if "digest" in t.name.lower()]
    if matches:
        return matches[0]
    available = ", ".join(t.name for t in tools) or "(none)"
    raise RuntimeError(
        f"No digest tool found. Available tools: {available}. "
        "Set DIGEST_TOOL in .env to the correct name."
    )


async def fetch_digest() -> str:
    token = os.getenv("SCHOLAR_INBOX_MCP_TOKEN") or os.environ["SCHOLAR_INBOX_TOKEN"]
    headers = {"Authorization": f"Bearer {token}"}

    async with httpx.AsyncClient(headers=headers, timeout=60) as http_client:
        async with streamable_http_client(MCP_URL, http_client=http_client) as (
            read,
            write,
        ):
            async with ClientSession(read, write) as session:
                await session.initialize()

                tools = (await session.list_tools()).tools
                tool_name = pick_tool(tools)
                log.info("Calling MCP tool: %s", tool_name)

                result = await session.call_tool(
                    tool_name,
                    arguments={"start_date": None, "end_date": None, "top_k": 10},
                )
                if result.is_error:
                    raise RuntimeError(f"MCP tool returned an error: {result.content}")

                text = "\n".join(
                    c.text for c in result.content if getattr(c, "type", "") == "text"
                ).strip()
                if not text:
                    raise RuntimeError("MCP tool returned no text content.")
                return text


def split_message(text: str, limit: int = TELEGRAM_LIMIT) -> list[str]:
    """Split on newlines so messages don't cut sentences in half."""
    chunks, current = [], ""
    for line in text.splitlines(keepends=True):
        while len(line) > limit:  # very long single line
            chunks.append(line[:limit])
            line = line[limit:]
        if len(current) + len(line) > limit:
            chunks.append(current)
            current = ""
        current += line
    if current:
        chunks.append(current)
    return chunks


def format_paper(p: dict, rank: int) -> str:
    """One paper -> one Telegram HTML message: title, authors, year, abstract."""

    def esc(s) -> str:
        return html.escape(str(s or "").strip(), quote=False)

    title = esc(p.get("title")) or "Untitled"
    url = p.get("url") or (
        f"https://arxiv.org/abs/{p['arxiv_id']}" if p.get("arxiv_id") else ""
    )
    title_html = (
        f'<a href="{html.escape(url, quote=True)}">{title}</a>' if url else title
    )

    year = (p.get("publication_date") or "")[:4]

    lines = [f"<b>{rank}. {title_html}</b>", ""]
    lines.append(f"<b>Authors:</b> {esc(p.get('authors'))}")
    if year:
        lines.append(f"<b>Year:</b> {year}")

    prefix = "\n".join(lines) + "\n\n<b>Abstract:</b> "
    abstract = esc(p.get("abstract"))
    room = 3800 - len(prefix)  # stay under Telegram's 4096 limit
    if len(abstract) > room:
        abstract = abstract[: room - 1].rsplit(" ", 1)[0] + "…"
    return prefix + abstract


def build_messages(raw: str) -> list[str]:
    """Turn the MCP JSON into one message per paper. Falls back to raw text."""
    try:
        data = json.loads(raw)
        papers = data["papers"]
    except (ValueError, KeyError, TypeError):
        return [html.escape(c, quote=False) for c in split_message(raw)]

    date = data.get("to_date", "")
    msgs = [f"<b>📚 Scholar Inbox digest — {date}</b>"]
    msgs += [format_paper(p, i) for i, p in enumerate(papers, 1)]
    return msgs


async def send_telegram(messages: list[str]) -> None:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]
    url = f"https://api.telegram.org/bot{token}/sendMessage"

    async with httpx.AsyncClient(timeout=30) as client:
        for msg in messages:
            resp = await client.post(
                url,
                json={
                    "chat_id": chat_id,
                    "text": msg,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                },
            )
            if resp.is_error:
                try:
                    description = resp.json().get("description", "No error description")
                except (ValueError, AttributeError):
                    description = "No error description"
                description = str(description).replace(token, "[REDACTED]")
                # Never raise HTTPStatusError here: it includes the request URL,
                # which contains the Telegram bot token.
                raise RuntimeError(
                    f"Telegram send failed (HTTP {resp.status_code}): {description}"
                )
            await asyncio.sleep(0.5)  # stay under Telegram's rate limit


async def run_digest_job() -> bool:
    try:
        digest = await fetch_digest()
        await send_telegram(build_messages(digest))
        log.info("Digest sent to Telegram.")
        return True
    except Exception:
        log.exception("Failed to send digest")
        return False


def next_run_time(now: datetime, hour: int, minute: int) -> datetime:
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return target


async def scheduler() -> None:
    tz = ZoneInfo(os.getenv("TIMEZONE", "UTC"))
    hour, minute = map(int, os.getenv("DIGEST_TIME", "08:00").split(":"))

    while True:
        now = datetime.now(tz)
        target = next_run_time(now, hour, minute)
        log.info("Next digest at %s", target.isoformat())
        await asyncio.sleep((target - now).total_seconds())
        await run_digest_job()


if __name__ == "__main__":
    if "--now" in sys.argv:
        # Send one digest immediately, for testing
        if not asyncio.run(run_digest_job()):
            sys.exit(1)
    else:
        try:
            asyncio.run(scheduler())
        except KeyboardInterrupt:
            log.info("Stopped.")