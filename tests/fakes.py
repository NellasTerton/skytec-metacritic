from app.metacritic import GameDetails, GameListItem, NotFound, Page, PlatformInfo


def item(slug: str) -> GameListItem:
    return GameListItem(slug=slug, title=slug.title())


class FakeListingClient:
    """new_releases + browse_newest поверх списков; страницы можно задать с пересечениями."""

    def __init__(self, new_releases: list[str], browse: list[str], *, pages: dict[int, list[str]] | None = None):
        self._new = new_releases
        self._browse = browse
        self._pages = pages or {}
        self.browse_calls: list[int] = []

    async def new_releases(self, limit: int = 20) -> list[GameListItem]:
        return [item(s) for s in self._new[:limit]]

    async def browse_newest(self, offset: int = 0, limit: int = 50) -> Page[GameListItem]:
        self.browse_calls.append(offset)
        slugs = self._pages.get(offset, self._browse[offset : offset + limit])
        return Page[GameListItem](total=len(self._browse), offset=offset, limit=limit, items=[item(s) for s in slugs])


class FakeGameClient(FakeListingClient):
    def __init__(self, *args, broken: set[str] = frozenset(), **kwargs):
        super().__init__(*args, **kwargs)
        self.broken = broken

    async def game(self, slug: str) -> GameDetails:
        if slug in self.broken:
            raise NotFound(slug)
        return GameDetails(
            slug=slug,
            title=slug.title(),
            url=f"https://www.metacritic.com/game/{slug}/",
            genres=["RPG"],
            developers=["Dev Studio"],
            platforms=[
                PlatformInfo(slug="pc", name="PC", is_lead=True, metascore=80, userscore=7.5, user_review_count=10),
                PlatformInfo(slug="playstation-5", name="PlayStation 5", metascore=None),
            ],
        )

    async def aclose(self) -> None:
        pass
