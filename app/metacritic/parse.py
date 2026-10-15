"""Чистые функции: JSON backend.metacritic.com -> схемы. Без сети, тестируются на фикстурах."""

import html
from datetime import date
from typing import Any

from app.metacritic.schemas import (
    GameDetails,
    GameListItem,
    Page,
    PlatformInfo,
    Review,
    ReviewKind,
    ScoreStats,
    Video,
)

SITE_URL = "https://www.metacritic.com"
IMAGE_BASE_URL = f"{SITE_URL}/a/img/catalog"

# Порядок важен: при дедупликации первыми остаются отзывы из явных корзин.
_SAMPLE_BUCKETS = ("positive", "neutral", "negative", "default")


class ParseError(ValueError):
    pass


def _date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def image_url(image: dict | None) -> str | None:
    # Ресайз /a/img/resize/... без подписи отдаёт 403 — берём оригинал по bucketPath.
    if not image or not image.get("bucketPath"):
        return None
    return IMAGE_BASE_URL + image["bucketPath"]


def _int(value: Any) -> int | None:
    return None if value is None else int(value)


def parse_score_stats(item: dict | None) -> ScoreStats | None:
    if not item:
        return None
    count = item.get("reviewCount") or 0
    score = item.get("score")
    # Пока отзывов мало, Metacritic оценку не публикует («tbd»): API отдаёт score 0 или null
    # и sentiment null. Ноль при этом — не настоящая оценка.
    published = score is not None and count > 0 and item.get("sentiment") is not None
    return ScoreStats(
        score=float(score) if published else None,
        max=item.get("max") or 100,
        review_count=count,
        positive=item.get("positiveCount") or 0,
        neutral=item.get("neutralCount") or 0,
        negative=item.get("negativeCount") or 0,
        sentiment=item.get("sentiment"),
    )


def parse_stats_response(data: dict) -> ScoreStats:
    stats = parse_score_stats((data.get("data") or {}).get("item"))
    if stats is None:
        raise ParseError("stats response without data.item")
    return stats


def parse_list_item(item: dict) -> GameListItem:
    critic = item.get("criticScoreSummary") or {}
    platforms = item.get("platforms") or []
    return GameListItem(
        slug=item["slug"],
        title=item["title"],
        release_date=_date(item.get("releaseDate")),
        metascore=_int(critic.get("score")),
        critic_review_count=critic.get("reviewCount"),
        platform=platforms[0]["name"] if platforms else None,
        genres=[g["name"] for g in item.get("genres") or [] if g.get("name")],
        cover_url=image_url(item.get("image")),
        description=(item.get("description") or "").strip() or None,
    )


def parse_finder(data: dict, *, offset: int, limit: int) -> Page[GameListItem]:
    body = data.get("data")
    if body is None:
        raise ParseError(f"finder response without data: {data.get('errors')}")
    return Page[GameListItem](
        total=body.get("totalResults") or 0,
        offset=offset,
        limit=limit,
        items=[parse_list_item(x) for x in body.get("items") or []],
    )


def parse_review(item: dict, kind: ReviewKind) -> Review | None:
    quote = (item.get("quote") or "").strip()
    if not quote:
        return None
    return Review(
        kind=kind,
        quote=quote,
        score=_int(item.get("score")),
        date=_date(item.get("date")),
        author=item.get("author") or None,
        publication=item.get("publicationName") if kind == "critic" else None,
        url=item.get("url") if kind == "critic" else None,
        platform=item.get("platform"),
        spoiler=bool(item.get("spoiler")),
    )


def parse_reviews(data: dict, kind: ReviewKind, *, offset: int, limit: int) -> Page[Review]:
    body = data.get("data")
    if body is None:
        raise ParseError(f"reviews response without data: {data.get('errors')}")
    reviews = [r for r in (parse_review(x, kind) for x in body.get("items") or []) if r]
    return Page[Review](total=body.get("totalResults") or 0, offset=offset, limit=limit, items=reviews)


def _review_sample(component: dict | None, kind: ReviewKind) -> tuple[list[Review], int]:
    if not component:
        return [], 0
    data = component.get("data") or {}
    buckets = data.get("item") or {}
    seen: set[tuple[str | None, str]] = set()
    out: list[Review] = []
    for bucket in _SAMPLE_BUCKETS:
        for raw in buckets.get(bucket) or []:
            review = parse_review(raw, kind)
            if review is None:
                continue
            key = (review.author or review.publication, review.quote)
            if key not in seen:
                seen.add(key)
                out.append(review)
    return out, data.get("totalResults") or 0


def _components(data: dict) -> dict[str, dict]:
    return {c.get("meta", {}).get("componentName"): c for c in data.get("components") or []}


def composer_product_status(data: dict) -> int:
    """composer отвечает HTTP 200 даже для несуществующей игры; статус — внутри компонента."""
    product = _components(data).get("product") or {}
    if product.get("status"):
        return int(product["status"])
    return 200 if (product.get("data") or {}).get("item") else 404


def parse_composer(data: dict) -> GameDetails:
    comps = _components(data)
    product = ((comps.get("product") or {}).get("data") or {}).get("item")
    if not product:
        raise ParseError("composer response without product")

    companies = (product.get("production") or {}).get("companies") or []
    images = {img.get("typeName"): img for img in product.get("images") or []}
    video_raw = product.get("video") or None
    video = None
    if video_raw and (video_raw.get("embedUrl") or video_raw.get("manifestUrl")):
        video = Video(
            # В названиях видео Metacritic встречаются HTML-сущности: «&amp;», «&#8217;».
            title=html.unescape(video_raw.get("title") or video_raw.get("videoTitle") or "") or None,
            embed_url=video_raw.get("embedUrl"),
            manifest_url=video_raw.get("manifestUrl"),
        )

    platforms = []
    for p in product.get("platforms") or []:
        critic = parse_score_stats(p.get("criticScoreSummary"))
        platforms.append(
            PlatformInfo(
                slug=p["slug"],
                name=p["name"],
                is_lead=bool(p.get("isLeadPlatform")),
                release_date=_date(p.get("releaseDate")),
                metascore=_int(critic.score) if critic else None,
                critic_review_count=critic.review_count if critic else None,
            )
        )

    platforms.sort(key=lambda p: not p.is_lead)  # стабильная сортировка: lead первой, дальше порядок API

    critic_score = parse_score_stats(((comps.get("critic-score-summary") or {}).get("data") or {}).get("item"))
    user_score = parse_score_stats(((comps.get("user-score-summary") or {}).get("data") or {}).get("item"))
    # Userscore в composer есть только для lead-платформы; остальные дозапрашивает клиент.
    lead = next((p for p in platforms if p.is_lead), None)
    if lead and user_score:
        lead.userscore = user_score.score
        lead.user_review_count = user_score.review_count

    critic_sample, critic_total = _review_sample(comps.get("critic-reviews"), "critic")
    user_sample, user_total = _review_sample(comps.get("user-reviews"), "user")

    slug = product["slug"]
    return GameDetails(
        slug=slug,
        title=product["title"],
        mc_title_id=_int(product.get("id")),
        url=f"{SITE_URL}/game/{slug}/",
        description=(product.get("description") or "").strip() or None,
        release_date=_date(product.get("releaseDate")),
        rating=product.get("rating"),
        genres=[g["name"] for g in product.get("genres") or [] if g.get("name")],
        developers=[c["name"] for c in companies if c.get("typeName") == "Developer"],
        publishers=[c["name"] for c in companies if c.get("typeName") == "Publisher"],
        cover_url=image_url(images.get("cardImage") or next(iter(images.values()), None)),
        banner_url=image_url(images.get("mainImage")),
        video=video,
        platforms=platforms,
        critic_score=critic_score,
        user_score=user_score,
        critic_reviews_sample=critic_sample,
        user_reviews_sample=user_sample,
        critic_reviews_total=critic_total,
        user_reviews_total=user_total,
    )
