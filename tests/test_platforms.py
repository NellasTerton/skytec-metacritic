from app.platforms import short_platform


def test_known_platforms():
    assert [short_platform(n) for n in ("PlayStation 5", "Xbox Series X", "Nintendo Switch 2", "PC")] == [
        "PS5",
        "XSX",
        "NS2",
        "PC",
    ]


def test_unknown_platform_falls_back_to_initials():
    assert short_platform("Sega Saturn 2") == "SS2"
    assert len(short_platform("Some Very Long Platform Name Here")) <= 5


def test_page_window_and_plural():
    from app.queries import page_window, plural

    assert page_window(1, 1) == [1]
    assert page_window(1, 3) == [1, 2, 3]
    assert page_window(5, 10) == [1, None, 4, 5, 6, None, 10]
    assert page_window(2, 10) == [1, 2, 3, None, 10]
    assert [plural(n, "игра", "игры", "игр") for n in (1, 2, 5, 11, 21, 22, 112)] == [
        "игра",
        "игры",
        "игр",
        "игр",
        "игра",
        "игры",
        "игр",
    ]
