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
