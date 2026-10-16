"""Однократная настройка бота: вебхук, команды, описания (тексты до «Старт») и кнопка меню.

TELEGRAM_BOT_TOKEN=... TELEGRAM_WEBHOOK_SECRET=... python -m scripts.telegram_setup
"""

import os
import sys

import httpx

SITE_URL = os.environ.get("SITE_URL", "https://skytec-metacritic.vercel.app/")
DESCRIPTION = (
    "🎮 Каталог новых игр с Metacritic.\n\n"
    "Оценки критиков и игроков по платформам, выжимки отзывов, летсплеи с выводами блогеров, "
    "трейлеры, скриншоты и похожие игры. Обновляется каждый час.\n\n"
    "Нажмите «Старт», чтобы открыть каталог."
)
SHORT_DESCRIPTION = "Новые игры с Metacritic: оценки, выжимки отзывов, летсплеи и похожие игры."


def main() -> int:
    token, secret = os.environ["TELEGRAM_BOT_TOKEN"], os.environ["TELEGRAM_WEBHOOK_SECRET"]
    api = f"https://api.telegram.org/bot{token}"
    calls = {
        "setWebhook": {
            "url": SITE_URL.rstrip("/") + "/api/telegram/webhook",
            "secret_token": secret,
            "allowed_updates": ["message"],
            "drop_pending_updates": True,
        },
        "setMyCommands": {"commands": [{"command": "start", "description": "Открыть каталог игр"}]},
        "setMyDescription": {"description": DESCRIPTION},
        "setMyShortDescription": {"short_description": SHORT_DESCRIPTION},
        "setChatMenuButton": {"menu_button": {"type": "web_app", "text": "Каталог", "web_app": {"url": SITE_URL}}},
    }
    ok = True
    for method, body in calls.items():
        result = httpx.post(f"{api}/{method}", json=body, timeout=30).json()
        print(f"{method}: {'ok' if result.get('ok') else result.get('description')}")
        ok &= bool(result.get("ok"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
