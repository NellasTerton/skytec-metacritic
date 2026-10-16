from app.models import Game
from app.queries import rank_similar


def g(i, title, genres, devs=(), metascore=None):
    return Game(id=i, slug=f"g{i}", title=title, genres=list(genres), developers=list(devs), metascore=metascore)


BASE = g(0, "Silver Pines", ["Survival"], ["Wych Elm"], metascore=86)


def test_scored_games_rank_above_unscored():
    close_unscored = g(1, "Indie horror", ["Survival"])
    far_scored = g(2, "Planet Zoo 2", ["Simulation"], metascore=85)
    ranked = [o.title for o, _ in rank_similar(BASE, [(close_unscored, 0.95), (far_scored, 0.70)])]
    assert ranked == ["Planet Zoo 2", "Indie horror"]


def test_description_similarity_with_genre_and_developer_bonus():
    same_genre = g(1, "Hellraiser", ["Survival"], metascore=69)
    same_dev = g(2, "Wych Elm next", ["Adventure"], ["Wych Elm"], metascore=70)
    closer_text = g(3, "Castlevania", ["Metroidvania"], metascore=85)
    ranked = rank_similar(BASE, [(same_genre, 0.78), (same_dev, 0.78), (closer_text, 0.84)])
    assert [o.title for o, _ in ranked] == ["Hellraiser", "Wych Elm next", "Castlevania"]
    reasons = dict((o.title, r) for o, r in ranked)
    assert reasons["Castlevania"] == ["похожее описание"]
    assert reasons["Hellraiser"] == ["Survival"] and reasons["Wych Elm next"] == ["разработчик Wych Elm"]


def test_without_embeddings_falls_back_to_genres_and_developer():
    a = g(1, "A", ["Survival"], metascore=60)
    b = g(2, "B", ["Puzzle"], metascore=90)
    assert [o.title for o, _ in rank_similar(BASE, [(b, None), (a, None)])] == ["A", "B"]
    assert rank_similar(BASE, [(BASE, 1.0)]) == []  # сама игра не предлагается
