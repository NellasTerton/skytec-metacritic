"""Минимальный клиент OpenAI-совместимых chat/completions с фолбэком между провайдерами.

Groq, Gemini (OpenAI-эндпоинт) и большинство бесплатных провайдеров говорят на этом протоколе,
поэтому SDK не нужен. При 429 с коротким Retry-After ждём и повторяем, с длинным (суточный
лимит) — отключаем провайдера до конца прогона и идём к следующему.
"""

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from types import TracebackType
from typing import Self

import httpx

from app.config import Settings

log = logging.getLogger(__name__)

# Минутные лимиты токенов (TPM) сбрасываются меньше чем за минуту — такое ждём.
MAX_SHORT_WAIT = 65.0


@dataclass
class Provider:
    name: str
    base_url: str
    api_key: str = field(repr=False)
    model: str
    extra: dict = field(default_factory=dict)  # параметры запроса, специфичные для модели


class LLMError(Exception):
    pass


def providers_from_settings(settings: Settings) -> list[Provider]:
    providers = []
    if settings.groq_api_key:
        # Reasoning-модели gpt-oss: низкое усилие хватает для резюме и экономит минутный лимит токенов.
        extra = {"reasoning_effort": "low"} if "gpt-oss" in settings.groq_model else {}
        providers.append(
            Provider("groq", "https://api.groq.com/openai/v1", settings.groq_api_key, settings.groq_model, extra)
        )
    if settings.gemini_api_key:
        providers.append(
            Provider(
                "gemini",
                "https://generativelanguage.googleapis.com/v1beta/openai",
                settings.gemini_api_key,
                settings.gemini_model,
            )
        )
    if settings.llm_api_key and settings.llm_base_url and settings.llm_model:
        base_url = settings.llm_base_url.rstrip("/")
        providers.append(Provider("custom", base_url, settings.llm_api_key, settings.llm_model))
    return providers


def parse_json_content(content: str) -> dict:
    """Модели иногда оборачивают JSON в ```json ... ``` даже в json-режиме."""
    text = content.strip()
    fenced = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)
    data = json.loads(text)
    if not isinstance(data, dict):
        raise LLMError("model returned non-object JSON")
    return data


def _retry_after(response: httpx.Response) -> float | None:
    value = response.headers.get("retry-after")
    try:
        return float(value) if value is not None else None
    except ValueError:
        return None


class LLMClient:
    def __init__(
        self,
        providers: list[Provider],
        *,
        timeout: float = 90.0,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep=asyncio.sleep,
    ) -> None:
        if not providers:
            raise ValueError("no LLM providers configured")
        self.providers = providers
        self._exhausted: set[str] = set()
        self._http = httpx.AsyncClient(timeout=timeout, transport=transport)
        self._sleep = sleep

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()

    @property
    def available(self) -> bool:
        return any(p.name not in self._exhausted for p in self.providers)

    async def complete_json(self, system: str, user: str, *, max_tokens: int = 1500) -> tuple[dict, str]:
        """Возвращает (JSON-ответ, "провайдер/модель")."""
        errors = []
        for provider in self.providers:
            if provider.name in self._exhausted:
                continue
            try:
                return await self._call(provider, system, user, max_tokens), f"{provider.name}/{provider.model}"
            except LLMError as e:
                errors.append(f"{provider.name}: {e}")
                log.warning("LLM provider %s failed: %s", provider.name, e)
        raise LLMError("; ".join(errors) or "all LLM providers are exhausted")

    async def _call(self, provider: Provider, system: str, user: str, max_tokens: int) -> dict:
        payload = {
            "model": provider.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "response_format": {"type": "json_object"},
            "temperature": 0.3,
            "max_tokens": max_tokens,
            **provider.extra,
        }
        headers = {"Authorization": f"Bearer {provider.api_key}"}
        for attempt in range(4):
            try:
                response = await self._http.post(f"{provider.base_url}/chat/completions", json=payload, headers=headers)
            except httpx.TransportError as e:
                if attempt == 3:
                    raise LLMError(f"{type(e).__name__}: {e}") from e
                await self._sleep(2**attempt)
                continue

            if response.status_code == 429:
                wait = _retry_after(response)
                if wait is not None and wait <= MAX_SHORT_WAIT and attempt < 3:
                    await self._sleep(wait + 0.5)
                    continue
                self._exhausted.add(provider.name)
                raise LLMError(f"rate limited (retry-after={wait})")
            if response.status_code >= 500 and attempt < 3:
                await self._sleep(2**attempt)
                continue
            if response.status_code >= 400:
                if response.status_code in (401, 403):
                    self._exhausted.add(provider.name)
                raise LLMError(f"HTTP {response.status_code}: {response.text[:300]}")

            try:
                content = response.json()["choices"][0]["message"]["content"] or ""
                return parse_json_content(content)
            except (KeyError, IndexError, ValueError) as e:
                if attempt == 3:
                    raise LLMError(f"bad response: {e}") from e
        raise LLMError("unreachable")
