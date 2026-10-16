import json
import logging

import httpx
from fastapi.testclient import TestClient

from app import main
from app.telegram import greeting, handle_update


def recorder():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"ok": True, "result": {}})

    return httpx.MockTransport(handler), calls


def update(text, first_name="Егор"):
    return {"message": {"chat": {"id": 42}, "from": {"first_name": first_name}, "text": text}}


async def test_start_sends_greeting_with_web_app_button():
    transport, calls = recorder()
    action = await handle_update(update("/start"), "T", "https://site.test/", transport=transport)
    body = json.loads(calls[0].content)
    assert action == "greeting" and calls[0].url.path == "/botT/sendMessage"
    assert body["chat_id"] == 42 and "Привет, Егор" in body["text"] and body["parse_mode"] == "HTML"
    assert body["reply_markup"]["inline_keyboard"][0][0]["web_app"] == {"url": "https://site.test/"}


async def test_other_text_gets_hint_and_name_is_escaped():
    transport, _ = recorder()
    assert await handle_update(update("привет"), "T", "https://site.test/", transport=transport) == "hint"
    assert await handle_update(update("/start@skytecmetacriticbot"), "T", "u", transport=transport) == "greeting"
    assert await handle_update({"edited_message": {}}, "T", "https://site.test/", transport=transport) is None
    assert "&lt;b&gt;" in greeting("<b>")


def test_webhook_checks_secret(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "T")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "s3cret")
    handled = []

    async def fake_handle(upd, token, site_url):
        handled.append((upd["message"]["text"], token))
        return "greeting"

    monkeypatch.setattr(main, "handle_update", fake_handle)
    client = TestClient(main.app)
    assert client.post("/api/telegram/webhook", json=update("/start")).status_code == 401
    bad = {"X-Telegram-Bot-Api-Secret-Token": "nope"}
    assert client.post("/api/telegram/webhook", json=update("/start"), headers=bad).status_code == 401
    good = {"X-Telegram-Bot-Api-Secret-Token": "s3cret"}
    response = client.post("/api/telegram/webhook", json=update("/start"), headers=good)
    assert response.json() == {"ok": True, "action": "greeting"} and handled == [("/start", "T")]


def test_webhook_not_configured(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    assert TestClient(main.app).post("/api/telegram/webhook", json={}).status_code == 503


def test_httpx_does_not_log_urls_with_token():
    # URL api.telegram.org/bot<токен>/… на INFO попал бы в runtime-логи Vercel.
    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING


def test_pages_load_telegram_web_app_script():
    html = TestClient(main.app).get("/monitor").text
    assert "telegram-web-app.js" in html and "tg.BackButton" in html
