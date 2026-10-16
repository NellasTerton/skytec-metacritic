from app.models import Game
from app.queries import rank_similar


def g(i, title, genres, devs=(), metascore=None):
    return Game(id=i, slug=f"g{i}", title=title, genres=list(genres), developers=list(devs), metascore=metascore)


BASE = g(0, "Silver Pines", ["Survival"], ["Wych Elm"], metascore=86)


def test_scored_games_rank_above_unscored_among_relevant():
    close_unscored = g(1, "Indie horror", ["Survival"])
    close_scored = g(2, "Hellraiser", ["Survival"], metascore=69)
    ranked = [o.title for o, _ in rank_similar(BASE, [(close_unscored, 0.95), (close_scored, 0.80)])]
    assert ranked == ["Hellraiser", "Indie horror"]


def test_irrelevant_scored_game_does_not_push_out_relevant_ones():
    relevant = [g(i, f"Zoo sim {i}", ["Simulation"]) for i in range(1, 11)]
    irrelevant = g(99, "Shooter", ["FPS"], metascore=90)
    zoo = g(0, "Planet Zoo 2", ["Simulation"], metascore=85)
    pairs = [(o, 0.84) for o in relevant] + [(irrelevant, 0.70)]
    ranked = [o.title for o, _ in rank_similar(zoo, pairs, limit=6)]
    assert "Shooter" not in ranked and len(ranked) == 6


def test_reasons_genre_developer_and_closest_descriptions():
    same_genre = g(1, "Hellraiser", ["Survival"], metascore=69)
    same_dev = g(2, "Wych Elm next", ["Adventure"], ["Wych Elm"], metascore=70)
    closer_text = g(3, "Castlevania", ["Metroidvania"], metascore=85)
    ranked = rank_similar(BASE, [(same_genre, 0.78), (same_dev, 0.78), (closer_text, 0.84)])
    assert [o.title for o, _ in ranked] == ["Hellraiser", "Wych Elm next", "Castlevania"]
    reasons = {o.title: r for o, r in ranked}
    assert reasons["Castlevania"] == ["похожее описание"]
    assert reasons["Hellraiser"] == ["похожее описание", "Survival"]
    assert reasons["Wych Elm next"] == ["похожее описание", "разработчик Wych Elm"]


def test_without_embeddings_falls_back_to_genres_and_developer():
    a = g(1, "A", ["Survival"], metascore=60)
    b = g(2, "B", ["Puzzle"], metascore=90)
    assert [o.title for o, _ in rank_similar(BASE, [(b, None), (a, None)])] == ["A", "B"]
    assert rank_similar(BASE, [(BASE, 1.0)]) == []  # сама игра не предлагается
