import httpx

from app.media import collect_media, normalize, pick_steam_candidate, pick_youtube_trailer, steam_media

# Реальная выдача Steam для «Dragon's Dogma 2: Dark Arisen»: первым идёт DLC.
DD2_SEARCH = [
    {"id": 2593290, "name": "Dragon's Dogma 2: Dark Arisen Expansion"},
    {"id": 1686532, "name": "Dragon's Dogma 2: Dark Arisen"},
]


# Реальная выдача YouTube для «Kingdom Hearts Collection I ~ III official trailer».
def _yt(video_id: str, title: str, views: int, duration: int) -> dict:
    return {"id": video_id, "title": title, "view_count": views, "duration": duration}


KH_YOUTUBE = [
    _yt("ldMqRmTwy80", "KINGDOM HEARTS Collection [I ~ III] Announce Trailer", 178878, 117),
    _yt("UBcONC-lad0", "KINGDOM HEARTS Collection [I ~ III] Launch Trailer", 603370, 33),
    _yt("RyXdmestcho", "Is The New Kingdom Hearts Collection Actually Worth It?", 900000, 496),
    _yt("6MAZN_5-F0M", "KINGDOM HEARTS Collection [I~III] Pre-Launch Livestream", 122321, 4937),
]


def test_normalize():
    assert normalize("Disney/Pixar Toy Story™: Retro Roundup!") == "disney pixar toy story retro roundup"
    assert normalize("KINGDOM HEARTS Collection [I ~ III]") == normalize("Kingdom Hearts Collection I ~ III")
    assert normalize("Pokémon Legends") == "pokemon legends"


def test_steam_candidate_skips_dlc_with_similar_name():
    assert pick_steam_candidate("Dragon's Dogma 2: Dark Arisen", DD2_SEARCH) == 1686532
    assert pick_steam_candidate("Some Other Game", DD2_SEARCH) is None


def test_youtube_picks_most_viewed_trailer_not_review():
    trailer = pick_youtube_trailer("Kingdom Hearts Collection I ~ III", KH_YOUTUBE)
    assert trailer["id"] == "UBcONC-lad0"
    assert trailer["embed_url"] == "https://www.youtube-nocookie.com/embed/UBcONC-lad0"
    assert trailer["url"] == "https://www.youtube.com/watch?v=UBcONC-lad0"
    assert pick_youtube_trailer("Unrelated Game", KH_YOUTUBE) is None


def steam_transport(app_type: str = "game"):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/storesearch/"):
            return httpx.Response(200, json={"items": DD2_SEARCH})
        appid = request.url.params["appids"]
        shots = [{"id": i, "path_thumbnail": f"t{i}.jpg", "path_full": f"f{i}.jpg"} for i in range(15)]
        return httpx.Response(200, json={appid: {"success": True, "data": {"type": app_type, "screenshots": shots}}})

    return httpx.MockTransport(handler)


async def test_steam_media_returns_limited_screenshots():
    async with httpx.AsyncClient(transport=steam_transport()) as client:
        appid, shots = await steam_media(client, "Dragon's Dogma 2: Dark Arisen")
    assert appid == 1686532 and len(shots) == 12 and shots[0] == {"thumb": "t0.jpg", "full": "f0.jpg"}


async def test_steam_media_rejects_non_game_apps():
    async with httpx.AsyncClient(transport=steam_transport("dlc")) as client:
        assert await steam_media(client, "Dragon's Dogma 2: Dark Arisen") == (None, [])


async def test_collect_media_survives_source_failures():
    async def broken_youtube(title):
        raise RuntimeError("Sign in to confirm you're not a bot")

    mc_video = {"title": "Official trailer", "embed_url": "https://cdn.jwplayer.com/players/x.html", "url": "u"}
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(503))) as client:
        media = await collect_media(client, "Game", mc_video, youtube=broken_youtube)
    assert media.screenshots == [] and media.steam_appid is None
    assert [v["source"] for v in media.videos] == ["metacritic"]


async def test_collect_media_puts_metacritic_video_first():
    async def youtube(title):
        return {"source": "youtube", "embed_url": "https://www.youtube.com/embed/y", "url": "y"}

    mc_video = {"title": "Official trailer", "embed_url": "https://cdn.jwplayer.com/players/x.html", "url": "u"}
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(503))) as client:
        media = await collect_media(client, "Game", mc_video, youtube=youtube)
    assert [v["source"] for v in media.videos] == ["metacritic", "youtube"]


def test_youtube_requires_title_prefix():
    entries = [_yt("x1", "Plague Doctor: Life in Pestilence - Official Trailer", 999, 90)]
    assert pick_youtube_trailer("Pestilence", entries) is None
    entries.append(_yt("x2", "Pestilence - Launch Trailer", 10, 60))
    assert pick_youtube_trailer("Pestilence", entries)["id"] == "x2"
