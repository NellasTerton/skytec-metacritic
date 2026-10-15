"""Какие игры обрабатывать в этом прогоне.

Каждый день начинаем заново: первый прогон берёт New Releases с /game/, следующие —
очередные страницы SEE ALL (Newest). Пропускаем игры, которые сегодня уже обрабатывали.

Внутри одной даты релиза Metacritic отдаёт игры в нестабильном порядке, и соседние
страницы пересекаются. Поэтому курсор — смещение, а уникальность обеспечивает дедуп по slug:
если на странице остались необработанные игры, следующий прогон начнёт с неё же.
"""

from dataclasses import dataclass, field
from typing import Protocol

from app.metacritic import GameListItem, Page

PHASE_NEW_RELEASES = "new_releases"
PHASE_BROWSE = "browse"
BROWSE_PAGE_SIZE = 50


class ListingClient(Protocol):
    async def new_releases(self, limit: int = 20) -> list[GameListItem]: ...

    async def browse_newest(self, offset: int = 0, limit: int = BROWSE_PAGE_SIZE) -> Page[GameListItem]: ...


@dataclass
class CrawlCursor:
    phase: str = PHASE_NEW_RELEASES
    next_offset: int = 0


@dataclass
class Selection:
    picked: list[tuple[str, str]] = field(default_factory=list)  # (slug, source)
    cursor: CrawlCursor = field(default_factory=CrawlCursor)
    pages_fetched: int = 0
    exhausted: bool = False  # SEE ALL кончился, брать больше нечего


async def select_games(
    client: ListingClient,
    cursor: CrawlCursor,
    processed_today: set[str],
    *,
    batch_size: int = 20,
    max_pages: int = 10,
) -> Selection:
    skip = set(processed_today)
    picked: list[tuple[str, str]] = []
    cursor = CrawlCursor(cursor.phase, cursor.next_offset)

    if cursor.phase == PHASE_NEW_RELEASES:
        for item in await client.new_releases(limit=batch_size):
            if item.slug not in skip:
                skip.add(item.slug)
                picked.append((item.slug, PHASE_NEW_RELEASES))
        # New Releases берём только первым прогоном дня, дальше — SEE ALL.
        cursor = CrawlCursor(PHASE_BROWSE, 0)
        if picked:
            return Selection(picked=picked, cursor=cursor)

    selection = Selection(cursor=cursor)
    offset = cursor.next_offset
    while len(picked) < batch_size and selection.pages_fetched < max_pages:
        page = await client.browse_newest(offset=offset, limit=BROWSE_PAGE_SIZE)
        selection.pages_fetched += 1
        fresh = [i.slug for i in page.items if i.slug not in skip]
        need = batch_size - len(picked)
        for slug in fresh[:need]:
            skip.add(slug)
            picked.append((slug, PHASE_BROWSE))
        if len(fresh) > need:
            break  # на странице ещё есть необработанные — в следующий раз начнём с неё
        offset += len(page.items)
        if not page.items or offset >= page.total:
            selection.exhausted = True
            break

    selection.picked = picked
    selection.cursor = CrawlCursor(PHASE_BROWSE, offset)
    return selection
