"""Envoi de messages Telegram (format HTML)."""
import html

from heimdall import config
from heimdall.http import get_json, post_json

MAX_LEN = 4000  # limite Telegram : 4096 caractères par message


def _api(method: str) -> str:
    if not config.TELEGRAM_BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN manquant dans .env")
    return f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/{method}"


def escape(text: str) -> str:
    return html.escape(text, quote=False)


def _split(text: str) -> list[str]:
    """Découpe sur les lignes pour ne jamais couper une balise HTML au milieu."""
    parts, current = [], ""
    for line in text.split("\n"):
        if len(current) + len(line) + 1 > MAX_LEN and current:
            parts.append(current)
            current = ""
        current += line + "\n"
    if current.strip():
        parts.append(current)
    return parts


def send(text: str, chat_id: str | None = None, silent: bool = False) -> None:
    chat_id = chat_id or config.TELEGRAM_CHAT_ID
    if not chat_id:
        raise RuntimeError("TELEGRAM_CHAT_ID manquant : lance python -m tools.setup_telegram")
    for part in _split(text):
        post_json(_api("sendMessage"), {
            "chat_id": chat_id,
            "text": part,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
            "disable_notification": silent,
        })


def recent_chats() -> list[dict]:
    d = get_json(_api("getUpdates"))
    chats = {}
    for u in d.get("result", []):
        msg = u.get("message") or u.get("edited_message") or {}
        chat = msg.get("chat")
        if chat:
            chats[chat["id"]] = chat
    return list(chats.values())
