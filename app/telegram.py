"""Telegram-бот: приветствие на /start с кнопкой, открывающей каталог как Mini App.

Обновления приходят вебхуком на /api/telegram/webhook (Vercel); Telegram подписывает их
заголовком X-Telegram-Bot-Api-Secret-Token — значением TELEGRAM_WEBHOOK_SECRET.
"""

import html
import logging

import httpx

log = logging.getLogger(__name__)

API_URL = "https://api.telegram.org"


def greeting(first_name: str | None) -> str:
    name = f", {html.escape(first_name)}" if first_name else ""
    return (
        f"👋 Привет{name}!\n\n"
        "Это <b>Metacritic Games</b> — свежие игры с Metacritic в одном месте:\n"
        "• оценки критиков и игроков по каждой платформе;\n"
        "• короткие выжимки отзывов: что хвалят и что ругают;\n"
        "• летсплеи с выводами блогеров, трейлеры и скриншоты;\n"
        "• похожие игры.\n\n"
        "Каталог обновляется каждый час. Открыть его — кнопкой ниже 👇"
    )


HINT = "Каталог открывается кнопкой ниже или кнопкой меню слева от поля ввода 👇"


def open_catalog_markup(site_url: str) -> dict:
    return {"inline_keyboard": [[{"text": "🎮 Открыть каталог", "web_app": {"url": site_url}}]]}


async def send_message(
    token: str,
    chat_id: int,
    text: str,
    markup: dict | None = None,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> bool:
    body = {"chat_id": chat_id, "text": text, "parse_mode": "HTML", "link_preview_options": {"is_disabled": True}}
    if markup:
        body["reply_markup"] = markup
    async with httpx.AsyncClient(timeout=15, transport=transport) as client:
        response = await client.post(f"{API_URL}/bot{token}/sendMessage", json=body)
    ok = response.status_code == 200 and response.json().get("ok")
    if not ok:
        log.warning("telegram sendMessage failed: HTTP %s %s", response.status_code, response.text[:200])
    return bool(ok)


async def handle_update(update: dict, token: str, site_url: str, **kwargs) -> str | None:
    """Ответ на сообщение из апдейта; возвращает, что сделали (для логов и тестов)."""
    message = update.get("message") or {}
    chat_id = (message.get("chat") or {}).get("id")
    text = (message.get("text") or "").strip()
    if not chat_id or not text:
        return None
    if text.split("@")[0].split()[0] in ("/start", "/help"):
        first_name = (message.get("from") or {}).get("first_name")
        await send_message(token, chat_id, greeting(first_name), open_catalog_markup(site_url), **kwargs)
        return "greeting"
    await send_message(token, chat_id, HINT, open_catalog_markup(site_url), **kwargs)
    return "hint"
