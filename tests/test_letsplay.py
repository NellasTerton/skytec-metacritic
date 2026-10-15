import json

import httpx
import pytest

from app import letsplay
from app.letsplay import (
    LetsplayError,
    QuotaExceeded,
    Video,
    analyze,
    find_letsplay,
    gemini_json,
    iso_duration_seconds,
    pick_letsplay,
    transcript_from_json3,
    video_clips,
)
from app.queries import format_duration, format_views


def v(video_id, title, views, minutes, lang="en"):
    return Video(video_id, title, "Channel", views, minutes * 60, lang)


# Реальная выдача YouTube Data API для «Silver Pines let's play» (order=viewCount), сокращённо.
SILVER_PINES = [
    v("wiM8ZLMHIYM", "I've Never Played Anything Like This Horror Game Before... (SILVER PINES)", 444000, 91),
    v("INvwzryTXAw", "Silver Pines | Launch Trailer", 341141, 2, "en-GB"),
    v("GTCqAKFyAy8", "Is Silver Pines 2026's Best Survival Horror Game?", 340796, 24),
    v("u4E-ia892M4", "Silver Pines: 12 Minutes of Survival Horror Gameplay", 169856, 12, None),
    v("LexmoXFAqEM", "Silver Pines PS5 Review - This Awesome Game Came Out of Nowhere", 138456, 9, None),
    v("oqkqfmYAbYo", "Silver Pines - Full Game Walkthrough", 20386, 423),
    v("_VdUUv3h854", "Endlich mal wieder ein 10/10 Horror Game! Silver Pines Demo", 900000, 76, "de"),
]


def test_pick_letsplay_takes_most_viewed_real_letsplay():
    picked = pick_letsplay("Silver Pines", SILVER_PINES)
    # Трейлер, обзор, 12-минутный геймплей, 7-часовое прохождение и немецкий ролик отсеяны.
    assert picked.id == "wiM8ZLMHIYM"


def test_iso_duration():
    assert iso_duration_seconds("PT1H31M5S") == 5465
    assert iso_duration_seconds("PT38M") == 2280
    assert iso_duration_seconds("P1DT2H") == 93600
    assert iso_duration_seconds("") == 0


def test_transcript_and_caption_track_priority():
    segs = [{"segs": [{"utf8": "All right, so"}, {"utf8": " I've played"}]}, {"segs": [{"utf8": "\n a lot"}]}]
    data = {"events": segs}
    assert transcript_from_json3(data) == "All right, so I've played a lot"
    info = {
        "subtitles": {},
        "automatic_captions": {
            "en": [{"ext": "vtt", "url": "vtt"}, {"ext": "json3", "url": "translated"}],
            "en-orig": [{"ext": "json3", "url": "original"}],
        },
    }
    assert letsplay._caption_track_url(info) == "original"
    info["subtitles"] = {"en": [{"ext": "json3", "url": "manual"}]}
    assert letsplay._caption_track_url(info) == "manual"


def youtube_transport(results: dict[str, list[dict]], *, quota: bool = False):
    """search → id; videos → детали. results: запрос → список видео-словарей API."""
    searches = []

    def handler(request: httpx.Request) -> httpx.Response:
        if quota:
            return httpx.Response(403, json={"error": {"errors": [{"reason": "quotaExceeded"}]}})
        if request.url.path.endswith("/search"):
            q = request.url.params["q"]
            searches.append(q)
            return httpx.Response(200, json={"items": [{"id": {"videoId": item["id"]}} for item in results.get(q, [])]})
        ids = request.url.params["id"].split(",")
        items = [item for found in results.values() for item in found if item["id"] in ids]
        return httpx.Response(200, json={"items": items})

    transport = httpx.MockTransport(handler)
    transport.searches = searches  # type: ignore[attr-defined]
    return transport


def api_video(video_id, title, views, duration):
    return {
        "id": video_id,
        "snippet": {"title": title, "channelTitle": "Ch", "defaultAudioLanguage": "en"},
        "contentDetails": {"duration": duration},
        "statistics": {"viewCount": str(views)},
    }


async def test_find_letsplay_falls_back_to_title_before_colon():
    transport = youtube_transport(
        {
            "Dragon's Dogma 2: Dark Arisen let's play": [
                api_video("t1", "Dragon's Dogma 2 Dark Arisen Trailer", 9, "PT2M")
            ],
            "Dragon's Dogma 2 let's play": [api_video("lp", "Dragon's Dogma 2 - Let's Play Part 1", 5000, "PT1H5M")],
        }
    )
    async with httpx.AsyncClient(transport=transport) as client:
        video = await find_letsplay(client, "key", "Dragon's Dogma 2: Dark Arisen")
    assert video.id == "lp" and video.duration_s == 3900
    assert transport.searches == ["Dragon's Dogma 2: Dark Arisen let's play", "Dragon's Dogma 2 let's play"]


async def test_find_letsplay_quota():
    async with httpx.AsyncClient(transport=youtube_transport({}, quota=True)) as client:
        with pytest.raises(QuotaExceeded):
            await find_letsplay(client, "key", "Silver Pines")


def gemini_transport(responses: dict[str, httpx.Response], seen: list):
    def handler(request: httpx.Request) -> httpx.Response:
        model = request.url.path.rsplit("/", 1)[-1].split(":")[0]
        seen.append((model, json.loads(request.content)))
        return responses[model]

    return httpx.MockTransport(handler)


GOOD = {
    "retelling": "Блогер в восторге.",
    "verdict": "Понравилась.",
    "quotes": [
        {"text": "This might be the most unique concept", "ru": "Возможно, это самый необычный замысел"},
        {"text": " ", "ru": "пусто"},
        "Worth it",
        {"text": "extra", "ru": "ещё"},
        {"text": "extra 2", "ru": "ещё 2"},
    ],
    "pros": ["атмосфера", "", "стиль"],
    "cons": ["перезарядка"],
    "liked": True,
}


def ok(payload: dict) -> httpx.Response:
    return httpx.Response(
        200, json={"candidates": [{"content": {"parts": [{"text": json.dumps(payload)}]}}], "modelVersion": "lite-x"}
    )


async def test_gemini_falls_back_to_next_model_on_overload():
    seen = []
    transport = gemini_transport({"flash": httpx.Response(503), "lite": ok({"a": 1})}, seen)
    async with httpx.AsyncClient(transport=transport) as client:
        assert await gemini_json(client, "k", ["flash", "lite"], [{"text": "x"}]) == ({"a": 1}, "lite-x")
    assert [m for m, _ in seen] == ["flash", "lite"]


async def test_gemini_all_overloaded_raises():
    transport = gemini_transport({"flash": httpx.Response(503), "lite": httpx.Response(429)}, [])
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(LetsplayError, match=r"HTTP 503.*HTTP 429"):
            await gemini_json(client, "k", ["flash", "lite"], [{"text": "x"}])


async def test_analyze_uses_transcript_when_available():
    seen = []
    video = v("abc", "Game - Let's Play", 1, 91)
    async with httpx.AsyncClient(transport=gemini_transport({"lite": ok(GOOD)}, seen)) as client:
        result = await analyze(client, "k", ["lite"], "Game", video, "full transcript text")
    parts = seen[0][1]["contents"][0]["parts"]
    assert len(parts) == 1 and "full transcript text" in parts[0]["text"]
    assert result.method == "captions" and result.pros == ["атмосфера", "стиль"] and result.liked is True


def test_video_clips():
    # 91 минута: начало 5 мин, середина 5 мин, конец 10 мин.
    assert video_clips(91 * 60) == [(0, 300), (2580, 2880), (4860, 5460)]
    assert video_clips(15 * 60) == [(0, 900)]  # короткий ролик — целиком
    assert sum(b - a for a, b in video_clips(153 * 60)) == 20 * 60


async def test_analyze_without_captions_watches_three_clips():
    seen = []
    video = v("abc", "Game - Let's Play", 1, 91)
    async with httpx.AsyncClient(transport=gemini_transport({"lite": ok(GOOD)}, seen)) as client:
        result = await analyze(client, "k", ["lite"], "Game", video, None)
    assert len(seen) == 1  # один запрос: пересказ, заключение и цитаты сразу
    parts = seen[0][1]["contents"][0]["parts"]
    clips = [(p["video_metadata"]["start_offset"], p["video_metadata"]["end_offset"]) for p in parts[:-1]]
    assert clips == [("0s", "300s"), ("2580s", "2880s"), ("4860s", "5460s")]
    assert all(p["file_data"]["file_uri"] == "https://www.youtube.com/watch?v=abc" for p in parts[:-1])
    assert "цитаты" in parts[-1]["text"]
    assert seen[0][1]["generationConfig"]["mediaResolution"] == "MEDIA_RESOLUTION_LOW"
    assert result.method == "video" and result.transcript is None  # без субтитров полной расшифровки нет
    assert result.quotes == [
        {"text": "This might be the most unique concept", "ru": "Возможно, это самый необычный замысел"},
        {"text": "Worth it", "ru": ""},
        {"text": "extra", "ru": "ещё"},
    ]
    prompt = parts[-1]["text"]
    assert "перевод на русский" in prompt and "транслитерацию" in prompt


async def test_analyze_rejects_empty_answer():
    async with httpx.AsyncClient(transport=gemini_transport({"lite": ok({"retelling": "", "verdict": ""})}, [])) as c:
        with pytest.raises(LetsplayError):
            await analyze(c, "k", ["lite"], "Game", v("abc", "t", 1, 30), "text")


def test_views_and_duration_format():
    assert format_views(443990) == "444 тыс. просмотров"
    assert format_views(1234567) == "1,2 млн просмотров"
    assert format_views(2000000) == "2 млн просмотров"
    assert format_views(21) == "21 просмотр"
    assert format_duration(5465) == "1 ч 31 мин" and format_duration(2280) == "38 мин"


async def test_gemini_json_skips_model_with_broken_json():
    seen = []
    broken = httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": '{"a": "unterminated'}]}}]})
    transport = gemini_transport({"lite": broken, "flash": ok({"a": 1})}, seen)
    async with httpx.AsyncClient(transport=transport) as client:
        assert await gemini_json(client, "k", ["lite", "flash"], [{"text": "x"}]) == ({"a": 1}, "lite-x")
