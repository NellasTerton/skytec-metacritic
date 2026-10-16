"""Трейлеры и скриншоты для карточки игры.

У Metacritic видео есть не у всех игр, а скриншотов нет вовсе, поэтому добираем:
- Steam Store API (без ключа): скриншоты 1920×1080 и appid — для игр, которые есть в Steam;
- YouTube (поиск через yt-dlp, без API-ключа): трейлер, который встраивается в любой браузер
  и в Telegram WebView.

Сопоставление строгое: поиск Steam часто первым отдаёт DLC или саундтрек, а YouTube — обзоры.
"""

import asyncio
import logging
import re
import unicodedata
from dataclasses import dataclass, field

import httpx

log = logging.getLogger(__name__)

STEAM_SEARCH_URL = "https://store.steampowered.com/api/storesearch/"
STEAM_DETAILS_URL = "https://store.steampowered.com/api/appdetails"
MAX_SCREENSHOTS = 12
MAX_TRAILER_SECONDS = 600
YOUTUBE_RESULTS = 8


def normalize(title: str) -> str:
    """«Disney/Pixar Toy Story™: Retro Roundup!» → «disney pixar toy story retro roundup»."""
    # Знаки ™®© убираем до NFKD: иначе «™» превратится в буквы «TM».
    text = re.sub(r"[™®©]", "", title)
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


@dataclass
class Media:
    videos: list[dict] = field(default_factory=list)
    screenshots: list[dict] = field(default_factory=list)
    steam_appid: int | None = None

    def as_json(self) -> dict:
        return {"videos": self.videos, "screenshots": self.screenshots}


# --- Steam -----------------------------------------------------------------------------------


def pick_steam_candidate(title: str, items: list[dict]) -> int | None:
    """appid позиции, чьё название совпадает с игрой после нормализации."""
    wanted = normalize(title)
    for item in items:
        if normalize(item.get("name", "")) == wanted:
            return item.get("id")
    return None


async def steam_media(client: httpx.AsyncClient, title: str) -> tuple[int | None, list[dict]]:
    """(appid, скриншоты) или (None, []), если игры в Steam нет."""
    response = await client.get(STEAM_SEARCH_URL, params={"term": title, "cc": "us", "l": "english"})
    response.raise_for_status()
    appid = pick_steam_candidate(title, response.json().get("items") or [])
    if appid is None:
        return None, []
    response = await client.get(STEAM_DETAILS_URL, params={"appids": appid, "cc": "us", "l": "english"})
    response.raise_for_status()
    entry = response.json().get(str(appid)) or {}
    data = entry.get("data") or {}
    if not entry.get("success") or data.get("type") != "game":
        return None, []
    screenshots = [
        {"thumb": s["path_thumbnail"], "full": s["path_full"]}
        for s in (data.get("screenshots") or [])[:MAX_SCREENSHOTS]
        if s.get("path_thumbnail") and s.get("path_full")
    ]
    return appid, screenshots


# --- YouTube ---------------------------------------------------------------------------------


def pick_youtube_trailer(title: str, entries: list[dict]) -> dict | None:
    """Самый просматриваемый ролик, название которого начинается с игры и содержит слово trailer."""
    wanted = normalize(title)
    candidates = []
    for e in entries:
        name = normalize(e.get("title") or "")
        duration = e.get("duration") or 0
        # Название ролика должно начинаться с названия игры: подстрока ловит чужие игры
        # («Pestilence» ⊂ «Plague Doctor: Life in Pestilence»).
        if e.get("id") and name.startswith(wanted) and "trailer" in name and 0 < duration <= MAX_TRAILER_SECONDS:
            candidates.append(e)
    if not candidates:
        return None
    best = max(candidates, key=lambda e: e.get("view_count") or 0)
    return {
        "source": "youtube",
        "id": best["id"],
        "title": best.get("title"),
        "channel": best.get("channel"),
        "url": f"https://www.youtube.com/watch?v={best['id']}",
        "embed_url": f"https://www.youtube-nocookie.com/embed/{best['id']}",
        "thumbnail": f"https://i.ytimg.com/vi/{best['id']}/hqdefault.jpg",
    }


def _youtube_search(query: str) -> list[dict]:
    import yt_dlp  # зависимость только воркера

    options = {"quiet": True, "no_warnings": True, "skip_download": True, "extract_flat": True}
    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(f"ytsearch{YOUTUBE_RESULTS}:{query}", download=False)
    return list(info.get("entries") or [])


async def youtube_trailer(title: str) -> dict | None:
    entries = await asyncio.to_thread(_youtube_search, f"{title} official trailer")
    return pick_youtube_trailer(title, entries)


# --- всё вместе ------------------------------------------------------------------------------


async def collect_media(
    client: httpx.AsyncClient, title: str, metacritic_video: dict | None = None, *, youtube=youtube_trailer
) -> Media:
    """Ошибки отдельных источников не роняют сбор: что нашли, то и вернём."""
    media = Media()
    try:
        media.steam_appid, media.screenshots = await steam_media(client, title)
    except (httpx.HTTPError, ValueError) as e:
        log.warning("steam media for %r failed: %s", title, e)
    try:
        if trailer := await youtube(title):
            media.videos.append(trailer)
    except Exception as e:  # noqa: BLE001 - yt-dlp бросает свои типы ошибок, падать из-за них нельзя
        log.warning("youtube trailer for %r failed: %s", title, e)
    if metacritic_video and metacritic_video.get("embed_url"):
        # Видео самой карточки Metacritic — первым: это ссылка на видео из ТЗ, YouTube — запасной.
        media.videos.insert(0, {"source": "metacritic", **metacritic_video})
    return media
