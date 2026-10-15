"""Проверка доступа к Metacritic из облачного окружения (GitHub Actions, Vercel).

Запуск: python probe/probe.py  ->  JSON с результатами в stdout.
"""
import asyncio
import json
import sys
import time

import httpx

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)
B = "https://backend.metacritic.com"
URLS = {
    "www_game": "https://www.metacritic.com/game/",
    "www_browse_new": "https://www.metacritic.com/browse/game/all/all/all-time/new/",
    "www_elden": "https://www.metacritic.com/game/elden-ring/",
    "finder_new_releases": f"{B}/finder/metacritic/web?mcoTypeId=13&sortBy=-releaseDate&metaScoreMin=1&offset=0&limit=20",
    "finder_see_all": f"{B}/finder/metacritic/web?mcoTypeId=13&sortBy=-releaseDate&offset=0&limit=50",
    "composer": f"{B}/composer/metacritic/pages/games/elden-ring/web",
    "user_stats_pc": f"{B}/reviews/metacritic/user/games/elden-ring/platform/pc/stats/web",
    "user_reviews_pc": f"{B}/reviews/metacritic/user/games/elden-ring/platform/pc/web?offset=0&limit=50&filterBySentiment=all&sort=date",
    "critic_reviews_pc": f"{B}/reviews/metacritic/critic/games/elden-ring/platform/pc/web?offset=0&limit=10&filterBySentiment=all&sort=score",
    "image": "https://www.metacritic.com/a/img/catalog/provider/7/2/7-1790044020.jpg",
    "youtube_search": "https://www.youtube.com/results?search_query=elden+ring+lets+play",
}


async def hit(client: httpx.AsyncClient, name: str, url: str, ua: str = BROWSER_UA) -> dict:
    started = time.monotonic()
    try:
        r = await client.get(url, headers={"User-Agent": ua})
        body = r.text[:20000] if "text" in r.headers.get("content-type", "") or "json" in r.headers.get("content-type", "") else ""
        return {
            "name": name,
            "status": r.status_code,
            "bytes": len(r.content),
            "ms": int((time.monotonic() - started) * 1000),
            "cf_cache": r.headers.get("cf-cache-status"),
            "challenge": any(m in body for m in ("cf-chl", "Just a moment", "challenge-platform")),
            "yt_bot_wall": "confirm you" in body.lower() and "not a bot" in body.lower(),
        }
    except Exception as e:  # noqa: BLE001 - зонд должен вернуть любую ошибку как данные
        return {"name": name, "error": repr(e)[:300]}


async def run() -> dict:
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as c:
        try:
            ip = (await c.get("https://ipinfo.io/json")).json()
            egress = {k: ip.get(k) for k in ("ip", "city", "country", "org")}
        except Exception as e:  # noqa: BLE001
            egress = {"error": repr(e)[:200]}

        basic = await asyncio.gather(*(hit(c, n, u) for n, u in URLS.items()))

        # Как реагируют на «библиотечные» User-Agent с облачного IP
        ua_checks = await asyncio.gather(
            hit(c, "backend:python-urllib", URLS["finder_new_releases"], "Python-urllib/3.12"),
            hit(c, "backend:python-httpx", URLS["finder_new_releases"], "python-httpx/0.28.1"),
            hit(c, "www:python-requests", URLS["www_elden"], "python-requests/2.32.3"),
        )

        slugs: list[str] = []
        try:
            r = await c.get(URLS["finder_new_releases"], headers={"User-Agent": BROWSER_UA})
            slugs = [x["slug"] for x in r.json()["data"]["items"]]
        except Exception:  # noqa: BLE001
            pass
        started = time.monotonic()
        burst = await asyncio.gather(
            *(hit(c, s, f"{B}/composer/metacritic/pages/games/{s}/web") for s in slugs)
        )
        burst_ms = int((time.monotonic() - started) * 1000)

    statuses: dict[str, int] = {}
    for b in burst:
        key = str(b.get("status", "error"))
        statuses[key] = statuses.get(key, 0) + 1
    return {
        "egress": egress,
        "basic": basic,
        "ua_checks": ua_checks,
        "burst_composer": {"n": len(slugs), "ms": burst_ms, "statuses": statuses},
    }


def to_markdown(res: dict) -> str:
    lines = [f"### Egress: `{json.dumps(res['egress'], ensure_ascii=False)}`", "",
             "| check | status | ms | bytes | cf | challenge | yt bot wall |", "|---|---|---|---|---|---|---|"]
    for r in res["basic"] + res["ua_checks"]:
        lines.append(f"| {r['name']} | {r.get('status', r.get('error'))} | {r.get('ms', '')} | {r.get('bytes', '')} "
                     f"| {r.get('cf_cache', '')} | {r.get('challenge', '')} | {r.get('yt_bot_wall', '')} |")
    b = res["burst_composer"]
    lines += ["", f"Burst: {b['n']} composer-запросов параллельно за {b['ms']} мс → {b['statuses']}"]
    return "\n".join(lines)


if __name__ == "__main__":
    result = asyncio.run(run())
    print(json.dumps(result, ensure_ascii=False, indent=1))
    if len(sys.argv) > 1:
        with open(sys.argv[1], "a", encoding="utf-8") as f:
            f.write(to_markdown(result) + "\n")
