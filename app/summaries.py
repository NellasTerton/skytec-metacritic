"""Резюме отзывов: что игрокам и критикам нравится и что нет.

Один вызов LLM на игру сразу для обоих видов отзывов. Вход — сбалансированная выборка из
composer (до 7 отзывов в корзинах positive / neutral / negative). Если набор отзывов не
изменился с прошлого раза (хэш), LLM не вызываем.
"""

import hashlib
import json
from dataclasses import dataclass

from app.metacritic import GameDetails, Review
from app.metacritic.schemas import ReviewKind

MIN_REVIEWS = 3
MAX_QUOTE_CHARS = 600
MAX_ITEMS = 5

SYSTEM_PROMPT = (
    "Ты аналитик отзывов на видеоигры. Пиши по-русски, коротко и по делу, опираясь только на "
    "переданные отзывы: ничего не придумывай. Отвечай строго JSON-объектом без пояснений."
)

KIND_TITLES: dict[ReviewKind, str] = {
    "critic": "Отзывы критиков (оценка из 100)",
    "user": "Отзывы игроков (оценка из 10)",
}


@dataclass
class SummaryResult:
    kind: ReviewKind
    summary: str
    pros: list[str]
    cons: list[str]
    review_count: int
    source_hash: str
    model: str


def reviews_for(details: GameDetails, kind: ReviewKind) -> list[Review]:
    return details.critic_reviews_sample if kind == "critic" else details.user_reviews_sample


def review_hash(reviews: list[Review]) -> str:
    keys = sorted(f"{r.score}|{r.quote}" for r in reviews)
    return hashlib.sha256("\n".join(keys).encode()).hexdigest()


def kinds_to_update(details: GameDetails, known_hashes: dict[str, str | None]) -> dict[ReviewKind, list[Review]]:
    """Виды отзывов, для которых есть что резюмировать и набор отзывов изменился."""
    out: dict[ReviewKind, list[Review]] = {}
    for kind in ("critic", "user"):
        reviews = reviews_for(details, kind)
        if len(reviews) >= MIN_REVIEWS and review_hash(reviews) != known_hashes.get(kind):
            out[kind] = reviews
    return out


def build_prompt(details: GameDetails, kinds: dict[ReviewKind, list[Review]]) -> str:
    lines = [f"Игра: {details.title}"]
    if details.genres:
        lines.append(f"Жанр: {', '.join(details.genres)}")
    for kind, reviews in kinds.items():
        lines += ["", f"## {KIND_TITLES[kind]}, {len(reviews)} шт."]
        for r in reviews:
            quote = " ".join(r.quote.split())[:MAX_QUOTE_CHARS]
            score = "?" if r.score is None else r.score
            lines.append(f"- [{score}] {quote}")

    schema = {
        kind: {
            "summary": "2–3 предложения: общее впечатление",
            "pros": [f"что нравится, до {MAX_ITEMS} коротких пунктов"],
            "cons": [f"что не нравится, до {MAX_ITEMS} коротких пунктов"],
        }
        for kind in kinds
    }
    lines += [
        "",
        "Отдельно для каждого раздела выше опиши, что авторам отзывов нравится в игре и что нет.",
        "Если минусов (или плюсов) в отзывах нет, верни пустой список.",
        f"Формат ответа: {json.dumps(schema, ensure_ascii=False)}",
    ]
    return "\n".join(lines)


def _clean_list(value) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(x).strip() for x in value if str(x).strip()][:MAX_ITEMS]


def parse_summaries(data: dict, kinds: dict[ReviewKind, list[Review]], model: str) -> list[SummaryResult]:
    results = []
    for kind, reviews in kinds.items():
        part = data.get(kind)
        if not isinstance(part, dict) or not str(part.get("summary", "")).strip():
            continue
        results.append(
            SummaryResult(
                kind=kind,
                summary=str(part["summary"]).strip(),
                pros=_clean_list(part.get("pros")),
                cons=_clean_list(part.get("cons")),
                review_count=len(reviews),
                source_hash=review_hash(reviews),
                model=model,
            )
        )
    return results


async def summarize(llm, details: GameDetails, kinds: dict[ReviewKind, list[Review]]) -> list[SummaryResult]:
    data, model = await llm.complete_json(SYSTEM_PROMPT, build_prompt(details, kinds))
    return parse_summaries(data, kinds, model)
