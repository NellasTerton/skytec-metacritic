from app.metacritic import parse
from app.summaries import (
    MIN_REVIEWS,
    build_prompt,
    kinds_to_update,
    parse_summaries,
    review_hash,
    summarize,
)
from tests.conftest import load


def elden():
    return parse.parse_composer(load("composer_elden-ring"))


def test_kinds_to_update_respects_hash_and_minimum():
    game = elden()
    kinds = kinds_to_update(game, {})
    assert set(kinds) == {"critic", "user"}
    known = {"critic": review_hash(game.critic_reviews_sample), "user": "stale"}
    assert set(kinds_to_update(game, known)) == {"user"}

    empty = parse.parse_composer(load("composer_robot-city-delivery"))
    assert kinds_to_update(empty, {}) == {}
    fresh = parse.parse_composer(load("composer_enshrouded"))  # 7 критиков, 0 игроков
    assert set(kinds_to_update(fresh, {})) == {"critic"} and MIN_REVIEWS <= 7


def test_review_hash_ignores_order():
    reviews = elden().user_reviews_sample
    assert review_hash(reviews) == review_hash(list(reversed(reviews)))
    assert review_hash(reviews) != review_hash(reviews[1:])


def test_prompt_contains_only_requested_kinds():
    game = elden()
    prompt = build_prompt(game, {"user": game.user_reviews_sample})
    assert "Elden Ring" in prompt and "Отзывы игроков" in prompt and "Отзывы критиков" not in prompt
    assert '"user"' in prompt and '"critic"' not in prompt
    assert prompt.count("\n- [") == len(game.user_reviews_sample)


def test_parse_summaries_cleans_output():
    game = elden()
    kinds = {"critic": game.critic_reviews_sample, "user": game.user_reviews_sample}
    data = {
        "critic": {"summary": " Шедевр. ", "pros": ["мир", " ", "боссы"] + ["x"] * 10, "cons": "нет"},
        "user": {"summary": ""},
    }
    [critic] = parse_summaries(data, kinds, "groq/m")
    assert critic.summary == "Шедевр." and critic.pros[:2] == ["мир", "боссы"] and len(critic.pros) == 5
    assert critic.cons == [] and critic.model == "groq/m"
    assert critic.source_hash == review_hash(game.critic_reviews_sample)


class FakeLLM:
    def __init__(self):
        self.prompts = []

    async def complete_json(self, system, user, **kw):
        self.prompts.append(user)
        return {"critic": {"summary": "ok", "pros": ["a"], "cons": []}}, "fake/model"


async def test_summarize_calls_llm_once():
    game = elden()
    llm = FakeLLM()
    results = await summarize(llm, game, {"critic": game.critic_reviews_sample})
    assert len(llm.prompts) == 1 and [r.kind for r in results] == ["critic"]
