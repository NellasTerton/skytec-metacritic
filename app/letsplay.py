"""Летсплей: самый просматриваемый ролик на YouTube → расшифровка речи блогера → заключение.

1. Поиск — YouTube Data API (search.list, order=viewCount; у поиска своя квота: 100 запросов
   в сутки на проект, поэтому ищем один раз на игру и только для игр с оценками).
2. Расшифровка — автосубтитры YouTube через yt-dlp: официальный API отдаёт субтитры только
   владельцу ролика. Текст 90-минутного ролика — ~14 тыс. токенов, Gemini читает его целиком.
3. Если субтитров нет (или YouTube не отдал их облачному IP) — Gemini смотрит сам ролик по ссылке,
   последние 20 минут: там обычно итоговое мнение блогера.
"""

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field

import httpx

from app.media import normalize

log = logging.getLogger(__name__)

YOUTUBE_API = "https://www.googleapis.com/youtube/v3"
GEMINI_API = "https://generativelanguage.googleapis.com/v1beta/models"
MIN_MINUTES, MAX_MINUTES = 15, 180
VIDEO_TAIL_SECONDS = 20 * 60
MAX_TRANSCRIPT_CHARS = 400_000
# Не летсплеи: трейлеры, обзоры, интервью и т.п.
EXCLUDE_WORDS = (
    "trailer", "review", "preview", "teaser", "reveal", "announcement", "interview",
    "soundtrack", "ost", "voice actor", "all bosses", "speedrun", "shorts", "reaction",
)  # fmt: skip


class LetsplayError(RuntimeError):
    pass


class QuotaExceeded(LetsplayError):
    """Суточная квота YouTube Data API кончилась — дальше в этом прогоне не ищем."""


@dataclass
class Video:
    id: str
    title: str
    channel: str
    view_count: int
    duration_s: int
    language: str | None = None

    @property
    def url(self) -> str:
        return f"https://www.youtube.com/watch?v={self.id}"


@dataclass
class LetsplayResult:
    video: Video
    method: str  # captions | video
    transcript: str | None
    retelling: str
    verdict: str
    pros: list[str] = field(default_factory=list)
    cons: list[str] = field(default_factory=list)
    liked: bool | None = None
    model: str = ""


# --- поиск ---------------------------------------------------------------------------------


def iso_duration_seconds(value: str) -> int:
    m = re.fullmatch(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", value or "")
    if not m:
        return 0
    days, hours, minutes, seconds = (int(x or 0) for x in m.groups())
    return ((days * 24 + hours) * 60 + minutes) * 60 + seconds


def pick_letsplay(title: str, videos: list[Video]) -> Video | None:
    """Самый просматриваемый англоязычный ролик 15–180 минут с названием игры, не трейлер и не обзор."""
    wanted = normalize(title)
    candidates = []
    for v in videos:
        name = normalize(v.title)
        if wanted not in name or any(word in name for word in EXCLUDE_WORDS):
            continue
        if not MIN_MINUTES * 60 <= v.duration_s <= MAX_MINUTES * 60:
            continue
        if v.language and not v.language.lower().startswith("en"):
            continue
        candidates.append(v)
    return max(candidates, key=lambda v: v.view_count, default=None)


async def _youtube_get(client: httpx.AsyncClient, path: str, params: dict) -> dict:
    response = await client.get(f"{YOUTUBE_API}/{path}", params=params)
    if response.status_code == 403 and "quota" in response.text.lower():
        raise QuotaExceeded(response.text[:200])
    if response.status_code != 200:
        raise LetsplayError(f"YouTube API {path} HTTP {response.status_code}: {response.text[:200]}")
    return response.json()


async def search_videos(client: httpx.AsyncClient, api_key: str, query: str) -> list[Video]:
    found = await _youtube_get(
        client,
        "search",
        {"part": "snippet", "q": query, "type": "video", "order": "viewCount", "maxResults": 25, "key": api_key},
    )
    ids = [item["id"]["videoId"] for item in found.get("items") or [] if item.get("id", {}).get("videoId")]
    if not ids:
        return []
    details = await _youtube_get(
        client, "videos", {"part": "snippet,contentDetails,statistics", "id": ",".join(ids), "key": api_key}
    )
    return [
        Video(
            id=item["id"],
            title=item["snippet"]["title"],
            channel=item["snippet"].get("channelTitle", ""),
            view_count=int(item.get("statistics", {}).get("viewCount", 0)),
            duration_s=iso_duration_seconds(item.get("contentDetails", {}).get("duration", "")),
            language=item["snippet"].get("defaultAudioLanguage"),
        )
        for item in details.get("items") or []
    ]


async def find_letsplay(client: httpx.AsyncClient, api_key: str, title: str) -> Video | None:
    """Ищем по полному названию; если пусто — по названию до двоеточия («Dragon's Dogma 2: Dark Arisen»)."""
    titles = [title]
    if ":" in title and (short := title.split(":")[0].strip()):
        titles.append(short)
    for name in titles:
        if video := pick_letsplay(name, await search_videos(client, api_key, f"{name} let's play")):
            return video
    return None


# --- расшифровка -----------------------------------------------------------------------------


def transcript_from_json3(data: dict) -> str:
    text = "".join(seg.get("utf8", "") for ev in data.get("events") or [] for seg in ev.get("segs") or [])
    return re.sub(r"\s+", " ", text).strip()


def _caption_track_url(info: dict) -> str | None:
    """json3-дорожка: ручные английские субтитры, иначе автосубтитры исходного языка ролика."""
    manual = info.get("subtitles") or {}
    auto = info.get("automatic_captions") or {}
    for tracks in (manual.get("en"), auto.get("en-orig"), auto.get("en")):
        for track in tracks or []:
            if track.get("ext") == "json3" and track.get("url"):
                return track["url"]
    return None


def _caption_url(video_url: str) -> str | None:
    import yt_dlp  # зависимость только воркера

    options = {"quiet": True, "no_warnings": True, "skip_download": True}
    with yt_dlp.YoutubeDL(options) as ydl:
        return _caption_track_url(ydl.extract_info(video_url, download=False))


async def fetch_transcript(client: httpx.AsyncClient, video: Video) -> str | None:
    try:
        url = await asyncio.to_thread(_caption_url, video.url)
        if not url:
            return None
        response = await client.get(url)
        response.raise_for_status()
        text = transcript_from_json3(response.json())
    except Exception as e:  # noqa: BLE001 - yt-dlp и YouTube падают по-разному; дальше фолбэк на видео
        log.warning("captions for %s failed: %s", video.id, e)
        return None
    return text[:MAX_TRANSCRIPT_CHARS] or None


# --- Gemini ---------------------------------------------------------------------------------

RESULT_SCHEMA = (
    '{"retelling": "...", "verdict": "...", "pros": ["до 4 пунктов"], "cons": ["до 4 пунктов"], "liked": true}'
)


TRANSCRIBE_STEP = (
    '0. Дословно расшифруй речь блогера на этом отрезке, на языке оригинала, без таймкодов — поле "transcript".\n'
)


def build_prompt(game_title: str, source: str, *, transcribe: bool = False) -> str:
    schema = RESULT_SCHEMA.replace('{"retelling"', '{"transcript": "...", "retelling"') if transcribe else RESULT_SCHEMA
    return (
        f"Это {source} летсплея игры «{game_title}»: блогер играет и комментирует вслух.\n"
        + (TRANSCRIBE_STEP if transcribe else "")
        + "1. Перескажи по-русски, что блогер говорит об игре по ходу прохождения: что впечатляет, что "
        "раздражает, с чем сравнивает. Это пересказ его речи, а не описание картинки. 5–7 предложений.\n"
        "2. Сделай заключение: понравилась ли игра блогеру и кому она подойдёт. Особенно учитывай его "
        "итоговые слова.\n"
        "Опирайся только на материал ролика, ничего не придумывай. Ответ — строго JSON:\n" + schema
    )


async def gemini_json(
    client: httpx.AsyncClient, api_key: str, models: list[str], parts: list[dict], **config
) -> tuple[dict, str]:
    """generateContent по цепочке моделей: при перегрузке (503) или лимите (429) — следующая модель."""
    errors = []
    for model in models:
        response = await client.post(
            f"{GEMINI_API}/{model}:generateContent",
            headers={"x-goog-api-key": api_key},
            json={
                "contents": [{"parts": parts}],
                "generationConfig": {"responseMimeType": "application/json", "temperature": 0.3, **config},
            },
        )
        if response.status_code in (429, 500, 503):
            errors.append(f"{model}: HTTP {response.status_code}")
            continue
        if response.status_code != 200:
            raise LetsplayError(f"Gemini {model} HTTP {response.status_code}: {response.text[:300]}")
        data = response.json()
        try:
            text = data["candidates"][0]["content"]["parts"][-1]["text"]
            return json.loads(text), data.get("modelVersion") or model
        except (KeyError, IndexError, ValueError) as e:
            errors.append(f"{model}: bad response ({e})")
    raise LetsplayError("; ".join(errors) or "no Gemini models configured")


def _clean(items) -> list[str]:
    return [str(x).strip() for x in items or [] if str(x).strip()][:4] if isinstance(items, list) else []


async def analyze(
    client: httpx.AsyncClient, gemini_key: str, models: list[str], game_title: str, video: Video, transcript: str | None
) -> LetsplayResult:
    if transcript:
        method = "captions"
        parts = [{"text": build_prompt(game_title, "расшифровка речи из") + "\n\nРасшифровка:\n" + transcript}]
        config = {}
    else:
        method = "video"
        start = max(0, video.duration_s - VIDEO_TAIL_SECONDS)
        parts = [
            {
                "file_data": {"file_uri": video.url, "mime_type": "video/*"},
                "video_metadata": {"start_offset": f"{start}s", "end_offset": f"{video.duration_s}s"},
            },
            # Субтитров нет (YouTube не отдаёт их облачным IP) — расшифровку речи делает сам Gemini.
            {"text": build_prompt(game_title, "последние 20 минут", transcribe=True)},
        ]
        config = {"mediaResolution": "MEDIA_RESOLUTION_LOW"}
    data, model = await gemini_json(client, gemini_key, models, parts, **config)
    if not str(data.get("retelling", "")).strip() or not str(data.get("verdict", "")).strip():
        raise LetsplayError("Gemini returned an empty retelling or verdict")
    liked = data.get("liked")
    return LetsplayResult(
        video=video,
        method=method,
        transcript=transcript or (str(data.get("transcript") or "").strip() or None),
        retelling=str(data["retelling"]).strip(),
        verdict=str(data["verdict"]).strip(),
        pros=_clean(data.get("pros")),
        cons=_clean(data.get("cons")),
        liked=liked if isinstance(liked, bool) else None,
        model=model,
    )
