"""Эмбеддинги описаний игр (Gemini, бесплатный тариф) для «похожих игр».

Вектор строится по названию, жанрам, разработчику и описанию; хранится в Neon (pgvector),
ближайшие ищутся запросом к БД. Запросы пакетные — до 100 текстов за вызов.
"""

import asyncio
import logging

import httpx

log = logging.getLogger(__name__)

GEMINI_API = "https://generativelanguage.googleapis.com/v1beta"
DIMENSIONS = 768
BATCH_SIZE = 100
MAX_DESCRIPTION_CHARS = 2000


class EmbeddingError(RuntimeError):
    pass


def game_text(title: str, genres: list[str], developers: list[str], description: str | None) -> str:
    parts = [title]
    if genres:
        parts.append("Genres: " + ", ".join(genres))
    if developers:
        parts.append("Developer: " + ", ".join(developers))
    if description:
        parts.append(description[:MAX_DESCRIPTION_CHARS])
    return ". ".join(parts)


async def embed_texts(
    client: httpx.AsyncClient, api_key: str, model: str, texts: list[str], *, sleep=asyncio.sleep
) -> list[list[float]]:
    """batchEmbedContents порциями по 100; при 429 ждём минуту (лимит токенов в минуту на free tier)."""
    vectors: list[list[float]] = []
    for start in range(0, len(texts), BATCH_SIZE):
        chunk = texts[start : start + BATCH_SIZE]
        body = {
            "requests": [
                {
                    "model": f"models/{model}",
                    "content": {"parts": [{"text": text}]},
                    "taskType": "SEMANTIC_SIMILARITY",
                    "outputDimensionality": DIMENSIONS,
                }
                for text in chunk
            ]
        }
        for attempt in range(3):
            response = await client.post(
                f"{GEMINI_API}/models/{model}:batchEmbedContents", headers={"x-goog-api-key": api_key}, json=body
            )
            if response.status_code in (429, 503) and attempt < 2:
                log.warning("embeddings HTTP %s, waiting a minute", response.status_code)
                await sleep(60)
                continue
            if response.status_code != 200:
                raise EmbeddingError(f"Gemini embeddings HTTP {response.status_code}: {response.text[:300]}")
            embeddings = response.json().get("embeddings") or []
            if len(embeddings) != len(chunk):
                raise EmbeddingError(f"expected {len(chunk)} embeddings, got {len(embeddings)}")
            vectors.extend(e["values"] for e in embeddings)
            break
    return vectors
