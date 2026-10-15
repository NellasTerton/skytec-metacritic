from app.worker.selection import PHASE_BROWSE, PHASE_NEW_RELEASES, CrawlCursor, select_games
from tests.fakes import FakeListingClient

NEW = [f"new-{i}" for i in range(20)]
BROWSE = [f"b-{i}" for i in range(200)]


async def test_first_run_of_day_takes_new_releases():
    client = FakeListingClient(NEW, BROWSE)
    sel = await select_games(client, CrawlCursor(), set())
    assert [s for s, _ in sel.picked] == NEW
    assert {src for _, src in sel.picked} == {PHASE_NEW_RELEASES}
    assert sel.cursor == CrawlCursor(PHASE_BROWSE, 0)
    assert client.browse_calls == []


async def test_new_releases_already_done_falls_through_to_browse():
    client = FakeListingClient(NEW, BROWSE)
    sel = await select_games(client, CrawlCursor(), set(NEW))
    assert [s for s, _ in sel.picked] == BROWSE[:20]
    assert {src for _, src in sel.picked} == {PHASE_BROWSE}


async def test_browse_skips_processed_and_stays_on_unfinished_page():
    client = FakeListingClient(NEW, BROWSE)
    done = set(NEW) | set(BROWSE[:10])
    sel = await select_games(client, CrawlCursor(PHASE_BROWSE, 0), done)
    assert [s for s, _ in sel.picked] == BROWSE[10:30]
    assert sel.cursor.next_offset == 0  # на первой странице ещё 20 необработанных


async def test_browse_advances_past_exhausted_pages():
    client = FakeListingClient(NEW, BROWSE)
    done = set(BROWSE[:60])
    sel = await select_games(client, CrawlCursor(PHASE_BROWSE, 0), done)
    assert [s for s, _ in sel.picked] == BROWSE[60:80]
    assert client.browse_calls == [0, 50]
    assert sel.cursor.next_offset == 50


async def test_overlapping_pages_do_not_duplicate():
    # Нестабильная сортировка: вторая страница повторяет часть первой.
    pages = {0: BROWSE[0:50], 50: BROWSE[40:90]}
    client = FakeListingClient(NEW, BROWSE, pages=pages)
    done = set(BROWSE[:45])
    sel = await select_games(client, CrawlCursor(PHASE_BROWSE, 0), done, batch_size=20)
    slugs = [s for s, _ in sel.picked]
    assert len(slugs) == len(set(slugs)) == 20
    assert slugs == BROWSE[45:65]


async def test_listing_exhausted():
    client = FakeListingClient(NEW, BROWSE[:30])
    sel = await select_games(client, CrawlCursor(PHASE_BROWSE, 0), set(BROWSE[:25]))
    assert [s for s, _ in sel.picked] == BROWSE[25:30]
    assert sel.exhausted


async def test_page_limit():
    client = FakeListingClient(NEW, BROWSE)
    sel = await select_games(client, CrawlCursor(PHASE_BROWSE, 0), set(BROWSE[:150]), max_pages=2)
    assert sel.picked == [] and sel.cursor.next_offset == 100
