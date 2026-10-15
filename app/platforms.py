"""Короткие метки платформ для карточек списка."""

import re

SHORT_NAMES = {
    "PC": "PC",
    "PlayStation 5": "PS5",
    "PlayStation 4": "PS4",
    "PlayStation 3": "PS3",
    "PlayStation Vita": "Vita",
    "Xbox Series X": "XSX",
    "Xbox One": "XB1",
    "Xbox 360": "X360",
    "Nintendo Switch 2": "NS2",
    "Nintendo Switch": "NS",
    "Wii U": "WiiU",
    "3DS": "3DS",
    "iOS (iPhone/iPad)": "iOS",
    "Android": "And",
    "Meta Quest": "Quest",
    "Stadia": "Stadia",
}


def short_platform(name: str) -> str:
    if name in SHORT_NAMES:
        return SHORT_NAMES[name]
    # Неизвестная платформа: инициалы слов + цифры («Sega Saturn 2» → «SS2»).
    words = re.findall(r"[A-Za-z]+|\d+", name)
    short = "".join(w if w.isdigit() else w[0].upper() for w in words)
    return short[:5] or name[:5]
