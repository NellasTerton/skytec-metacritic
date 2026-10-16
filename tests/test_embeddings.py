import httpx
import pytest

from app.embeddings import EmbeddingRateLimited, embed_texts

QUOTA_429 = {
    "error": {
        "code": 429,
        "message": "You exceeded your current quota",
        "details": [
            {"violations": [{"quotaMetric": "embed_content_requests", "quotaId": "PerMinutePerProject"}]},
            {"retryDelay": "17s"},
        ],
    }
}


def transport(responses):
    calls = []

    def handler(request):
        calls.append(request)
        status, body = responses[min(len(calls), len(responses)) - 1]
        return httpx.Response(status, json=body)

    return httpx.MockTransport(handler), calls


async def test_waits_retry_delay_from_gemini_then_succeeds():
    ok = {"embeddings": [{"values": [0.1, 0.2]}]}
    t, calls = transport([(429, QUOTA_429), (200, ok)])
    waits = []

    async def sleep(s):
        waits.append(s)

    async with httpx.AsyncClient(transport=t) as client:
        vectors = await embed_texts(client, "key", "m", ["text"], sleep=sleep)
    assert vectors == [[0.1, 0.2]] and waits == [18.0] and len(calls) == 2
    assert calls[0].headers["x-goog-api-key"] == "key" and "key=" not in str(calls[0].url)


async def test_persistent_rate_limit_raises_rate_limited_with_quota_name():
    t, calls = transport([(429, QUOTA_429)])

    async def sleep(s):
        pass

    async with httpx.AsyncClient(transport=t) as client:
        with pytest.raises(EmbeddingRateLimited, match="embed_content_requests"):
            await embed_texts(client, "key", "m", ["text"], sleep=sleep)
    assert len(calls) == 3
