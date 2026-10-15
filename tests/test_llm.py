import json

import httpx
import pytest

from app.config import Settings
from app.llm import LLMClient, LLMError, Provider, parse_json_content, providers_from_settings

A = Provider("a", "https://a.test/v1", "key-a", "model-a")
B = Provider("b", "https://b.test/v1", "key-b", "model-b")


def completion(content: str) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


def make(handler, providers=(A, B)) -> tuple[LLMClient, list[float]]:
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    return LLMClient(list(providers), transport=httpx.MockTransport(handler), sleep=fake_sleep), sleeps


async def test_returns_json_and_model():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert request.url.host == "a.test" and request.headers["authorization"] == "Bearer key-a"
        assert body["model"] == "model-a" and body["response_format"] == {"type": "json_object"}
        return completion('{"ok": true}')

    llm, _ = make(handler)
    assert await llm.complete_json("sys", "user") == ({"ok": True}, "a/model-a")


async def test_daily_limit_falls_back_to_next_provider():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "a.test":
            return httpx.Response(429, headers={"retry-after": "3600"})
        return completion('{"from": "b"}')

    llm, sleeps = make(handler)
    assert await llm.complete_json("s", "u") == ({"from": "b"}, "b/model-b")
    assert sleeps == []
    # Исчерпанный провайдер больше не дёргаем.
    assert (await llm.complete_json("s", "u"))[1] == "b/model-b"


async def test_short_rate_limit_waits_and_retries_same_provider():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"retry-after": "7"})
        return completion('{"ok": 1}')

    llm, sleeps = make(handler)
    assert (await llm.complete_json("s", "u"))[1] == "a/model-a"
    assert sleeps == [7.5]


async def test_all_exhausted_raises_and_reports_unavailable():
    llm, _ = make(lambda r: httpx.Response(401, text="bad key"))
    with pytest.raises(LLMError, match="HTTP 401"):
        await llm.complete_json("s", "u")
    assert not llm.available


def test_parse_json_content_strips_fences():
    assert parse_json_content('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json_content(' {"a": 2} ') == {"a": 2}
    with pytest.raises(LLMError):
        parse_json_content("[1, 2]")


def test_providers_from_settings_order():
    settings = Settings(_env_file=None, groq_api_key="g", gemini_api_key="m")
    assert [p.name for p in providers_from_settings(settings)] == ["groq", "gemini"]
    assert providers_from_settings(Settings(_env_file=None)) == []


def test_gpt_oss_gets_low_reasoning_effort():
    [groq] = providers_from_settings(Settings(_env_file=None, groq_api_key="g", groq_model="openai/gpt-oss-120b"))
    assert groq.extra == {"reasoning_effort": "low"}
    [other] = providers_from_settings(Settings(_env_file=None, groq_api_key="g", groq_model="qwen/qwen3.8-27b"))
    assert other.extra == {}


async def test_model_extra_params_are_sent():
    provider = Provider("a", "https://a.test/v1", "k", "m", {"reasoning_effort": "low"})

    def handler(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content)["reasoning_effort"] == "low"
        return completion("{}")

    llm, _ = make(handler, providers=(provider,))
    assert await llm.complete_json("s", "u") == ({}, "a/m")
