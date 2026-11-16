"""Gemini path of the agent, with the LLM mocked: parse, grounded generate, retry, fallback."""

import pytest

from src import agent, llm
from src.agent import run_query

META = llm.LLMMeta(model="mock", latency_ms=1.0, prompt_tokens=10, output_tokens=5)


@pytest.fixture
def gemini(monkeypatch):
    """Pretend a key is set and script the LLM's answers."""
    state = {"parse": None, "generate": [], "generate_calls": [], "parse_error": None}

    def fake_parse(text, genres, moods):
        if state["parse_error"]:
            raise llm.LLMError(state["parse_error"])
        return state["parse"] or llm.LLMParse(
            prefs={"favorite_mood": "chill", "target_energy": 0.35}, avoid_genres=[], on_topic=True, meta=META
        )

    def fake_generate(query, profile, candidates, k, feedback=None):
        state["generate_calls"].append(feedback)
        script = state["generate"]
        step = script.pop(0) if script else "good"
        if step == "error":
            raise llm.LLMError("504 DEADLINE_EXCEEDED")
        picks = [
            {"id": c["song"]["id"], "title": c["song"]["title"], "explanation": "Fits the vibe."}
            for c in candidates[:k]
        ]
        if step == "hallucinate":
            picks[0] = {"id": 999, "title": "Fake Song", "explanation": "Made up."}
        return llm.LLMGenerate(summary="A mellow set.", picks=picks, meta=META)

    monkeypatch.setattr(llm, "is_available", lambda: True)
    monkeypatch.setattr(llm, "parse_with_llm", fake_parse)
    monkeypatch.setattr(llm, "generate_with_llm", fake_generate)
    return state


def test_gemini_happy_path(gemini):
    result = run_query("mellow rainy day", k=3)
    assert result.mode == "gemini"
    assert result.status == "ok"
    assert result.summary == "A mellow set."
    assert result.llm_calls == 2
    assert all(r["id"] in result.retrieved_ids for r in result.recommendations)
    assert gemini["generate_calls"] == [None]


def test_hallucination_triggers_retry_with_feedback(gemini):
    gemini["generate"] = ["hallucinate", "good"]
    result = run_query("mellow rainy day", k=3)
    assert result.status == "ok"
    assert len(gemini["generate_calls"]) == 2
    assert any("999" in err for err in gemini["generate_calls"][1])  # errors were fed back
    checks = [s for s in result.trace if s.stage == "check"]
    assert [c.ok for c in checks] == [False, True]


def test_two_bad_answers_fall_back_to_scorer(gemini):
    gemini["generate"] = ["hallucinate", "hallucinate"]
    result = run_query("mellow rainy day", k=3)
    assert result.status == "fallback"
    assert len(result.recommendations) == 3
    assert all(r["id"] in result.retrieved_ids for r in result.recommendations)
    assert result.trace[-1].stage == "fallback"


def test_generate_error_falls_back_without_retry(gemini):
    gemini["generate"] = ["error"]
    result = run_query("mellow rainy day", k=3)
    assert result.status == "fallback"
    assert len(gemini["generate_calls"]) == 1


def test_parse_error_falls_back_to_offline_parser(gemini):
    gemini["parse_error"] = "timeout"
    result = run_query("chill study music", k=3)
    assert result.mode == "offline"
    assert result.status == "ok"
    assert gemini["generate_calls"] == []  # didn't waste a second call on a failing API
    assert any("offline parser" in w for w in result.warnings)


def test_off_topic_from_gemini_asks_for_clarification(gemini):
    gemini["parse"] = llm.LLMParse(prefs={}, avoid_genres=[], on_topic=False, meta=META)
    result = run_query("what is the capital of France")
    assert result.status == "needs_clarification"
    assert result.recommendations == []


def test_gemini_out_of_range_numbers_still_clamped(gemini):
    gemini["parse"] = llm.LLMParse(prefs={"target_energy": 1.5}, avoid_genres=[], on_topic=True, meta=META)
    result = run_query("energy 1.5")
    assert result.profile["target_energy"] == 1.0


def test_debug_corruption_exercises_the_loop(gemini):
    result = run_query("mellow rainy day", k=3, corrupt_attempts=1)
    assert result.status == "ok"
    assert len(gemini["generate_calls"]) == 2


def test_offline_flag_never_calls_gemini(gemini):
    result = run_query("chill study music", offline=True)
    assert result.mode == "offline"
    assert result.llm_calls == 0
    assert gemini["generate_calls"] == []


def test_no_key_uses_offline_path():
    result = run_query("chill study music")
    assert result.mode == "offline"
    assert "no GEMINI_API_KEY" in result.trace[1].detail
    assert agent.llm.is_available() is False
