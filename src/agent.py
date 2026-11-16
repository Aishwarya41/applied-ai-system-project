"""
VibeFinder pipeline: guard -> parse -> sanitize -> retrieve -> generate -> check.

run_query() returns a Result with the final recommendations plus a trace of
every stage, so the CLI and the Streamlit UI can show *how* an answer was
produced.

With a Gemini key, parse and generate use the LLM (RAG: the model writes its
answer only from the retrieved candidates). The check step validates that
answer; on failure Gemini gets one retry with the errors fed back, and if it
still fails (or Gemini is down) the deterministic offline path takes over.
"""

import time
import uuid
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional

from src import guardrails, llm
from src.logging_config import get_logger, setup_logging
from src.offline_parser import parse_query
from src.recommender import NUMERIC_TARGETS, SIGMAS, load_songs, numeric_similarity, retrieve_candidates
from src.validator import validate_recommendations

SONGS_CSV = Path(__file__).resolve().parent.parent / "data" / "songs.csv"
CANDIDATE_POOL = 10  # how many songs the retriever hands to the generate step
MAX_GENERATE_ATTEMPTS = 2  # first try + one retry with validator feedback

log = get_logger("agent")


@dataclass
class TraceStep:
    stage: str
    ok: bool
    detail: str
    ms: float = 0.0


@dataclass
class Result:
    query: str
    status: str  # ok | needs_clarification | blocked | fallback
    mode: str  # offline | gemini
    message: str = ""
    profile: Dict = field(default_factory=dict)
    avoid_genres: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    recommendations: List[Dict] = field(default_factory=list)
    retrieved_ids: List[int] = field(default_factory=list)
    trace: List[TraceStep] = field(default_factory=list)
    request_id: str = ""
    summary: str = ""  # Gemini's one-line summary of the set (empty offline)
    llm_calls: int = 0
    llm_tokens: int = 0


@lru_cache(maxsize=1)
def get_catalog() -> tuple:
    """Load songs once; return (songs, known_genres, known_moods)."""
    songs = load_songs(str(SONGS_CSV))
    genres = frozenset(s["genre"] for s in songs)
    moods = frozenset(s["mood"] for s in songs)
    return songs, genres, moods


class _Timer:
    def __enter__(self):
        self.start = time.perf_counter()
        return self

    def __exit__(self, *exc):
        self.ms = (time.perf_counter() - self.start) * 1000


def _template_explanation(song: Dict, prefs: Dict) -> str:
    """Deterministic plain-English explanation built from the scorer's features."""
    parts = []
    if prefs.get("favorite_genre") == song["genre"]:
        parts.append(f"it's {song['genre']}")
    if prefs.get("favorite_mood") == song["mood"]:
        parts.append(f"the mood is {song['mood']}")
    for feature, key in NUMERIC_TARGETS.items():
        target = prefs.get(key)
        if target is None:
            continue
        sim = numeric_similarity(song[feature], target, SIGMAS[feature])
        if sim >= 0.9:
            parts.append(f"its {feature} ({song[feature]:.2f}) is right on your target ({target:.2f})")
        elif sim >= 0.6:
            parts.append(f"its {feature} ({song[feature]:.2f}) is close to your target ({target:.2f})")

    if not parts:
        return (
            f"{song['title']} is the closest overall fit in the catalog, "
            "though nothing lines up strongly with your request."
        )
    if len(parts) > 1:
        parts[-1] = "and " + parts[-1]
    joiner = ", " if len(parts) > 2 else " "
    return f"{song['title']} fits because {joiner.join(parts)}."


def _to_rec(candidate: Dict, explanation: str) -> Dict:
    song = candidate["song"]
    return {
        "id": song["id"],
        "title": song["title"],
        "artist": song["artist"],
        "genre": song["genre"],
        "mood": song["mood"],
        "energy": song["energy"],
        "valence": song["valence"],
        "acousticness": song["acousticness"],
        "score": round(candidate["score"], 3),
        "reasons": candidate["reasons"],
        "explanation": explanation,
    }


def recommend_from_profile(
    result: Result,
    prefs: Dict,
    avoid_genres: Optional[List[str]] = None,
    k: int = 5,
    use_llm: bool = False,
    corrupt_attempts: int = 0,
) -> Result:
    """Sanitize -> retrieve -> generate -> check, given an already-parsed profile."""
    songs, genres, moods = get_catalog()
    rid = result.request_id

    # Sanitize
    with _Timer() as t:
        sanitized = guardrails.sanitize_profile(prefs, genres, moods)
    result.profile = sanitized.prefs
    result.avoid_genres = [g for g in (avoid_genres or []) if g in genres]
    result.warnings.extend(sanitized.warnings)
    result.trace.append(TraceStep("sanitize", True, f"profile={sanitized.prefs} warnings={len(sanitized.warnings)}", t.ms))
    log.info("[%s] sanitized profile=%s avoid=%s warnings=%s", rid, sanitized.prefs, result.avoid_genres, sanitized.warnings)

    if sanitized.is_empty:
        result.status = "needs_clarification"
        result.message = "I couldn't pick out a vibe from that. " + guardrails.CLARIFY_MESSAGE
        result.trace.append(TraceStep("clarify", False, "empty profile after parsing"))
        log.info("[%s] empty profile -> clarifying question", rid)
        return result

    # Retrieve
    with _Timer() as t:
        candidates = retrieve_candidates(sanitized.prefs, songs, k=max(CANDIDATE_POOL, k), exclude_genres=result.avoid_genres)
    result.retrieved_ids = [c["song"]["id"] for c in candidates]
    result.trace.append(TraceStep("retrieve", bool(candidates), f"{len(candidates)} candidates: {result.retrieved_ids}", t.ms))
    log.info("[%s] retrieved ids=%s", rid, result.retrieved_ids)

    if not candidates:
        result.status = "needs_clarification"
        result.message = "Every song was filtered out by your exclusions. Try loosening them."
        return result

    expected = min(k, len(candidates))

    if use_llm:
        # Agentic loop: generate -> check -> retry with feedback -> fall back.
        recs = _generate_with_checks(result, sanitized.prefs, candidates, expected, corrupt_attempts)
        if recs is None:
            recs = [_to_rec(c, _template_explanation(c["song"], sanitized.prefs)) for c in candidates[:expected]]
            result.status = "fallback"
            result.warnings.append(
                "Gemini's answer couldn't be verified, so these are the scorer's own picks "
                "with template explanations."
            )
            result.trace.append(TraceStep("fallback", True, "used scorer ranking + template explainer"))
            log.warning("[%s] falling back to scorer output", rid)
        else:
            result.status = "ok"
    else:
        with _Timer() as t:
            recs = [_to_rec(c, _template_explanation(c["song"], sanitized.prefs)) for c in candidates[:expected]]
        result.trace.append(TraceStep("generate", True, f"{len(recs)} picks via template explainer", t.ms))
        with _Timer() as t:
            errors = validate_recommendations(recs, candidates, expected)
        result.trace.append(TraceStep("check", not errors, "; ".join(errors) or "all picks grounded in retrieved candidates", t.ms))
        result.status = "fallback" if errors else "ok"

    result.recommendations = recs
    log.info("[%s] status=%s mode=%s picks=%s llm_calls=%d", rid, result.status, result.mode, [r["id"] for r in recs], result.llm_calls)
    return result


def _corrupt(picks: List[Dict]) -> List[Dict]:
    """Debug hook: swap the first pick for a hallucinated song to exercise the check loop."""
    fake = {"id": 999, "title": "Invented Song That Does Not Exist", "explanation": "A made-up pick."}
    return [fake] + list(picks[1:])


def _generate_with_checks(
    result: Result, prefs: Dict, candidates: List[Dict], expected: int, corrupt_attempts: int
) -> Optional[List[Dict]]:
    """Ask Gemini for grounded picks; validate; retry once with the errors. None = give up."""
    rid = result.request_id
    by_id = {c["song"]["id"]: c for c in candidates}
    feedback: Optional[List[str]] = None

    for attempt in range(1, MAX_GENERATE_ATTEMPTS + 1):
        try:
            with _Timer() as t:
                gen = llm.generate_with_llm(result.query, prefs, candidates, expected, feedback)
        except llm.LLMError as exc:
            result.trace.append(TraceStep("generate", False, f"attempt {attempt}: Gemini error: {exc}", t.ms))
            log.warning("[%s] generate attempt %d failed: %s", rid, attempt, exc)
            return None
        result.llm_calls += 1
        _add_tokens(result, gen.meta)

        picks = gen.picks
        note = ""
        if attempt <= corrupt_attempts:
            picks, note = _corrupt(picks), " [debug: corrupted]"
        result.trace.append(TraceStep(
            "generate", True,
            f"attempt {attempt}: gemini picked {[p.get('id') for p in picks]}{note} {_meta_str(gen.meta)}",
            t.ms,
        ))

        recs = []
        for p in picks:
            if p.get("id") in by_id:
                rec = _to_rec(by_id[p["id"]], p.get("explanation", ""))
                rec["title"] = p.get("title") or rec["title"]  # keep the model's title so the validator can check it
                recs.append(rec)
            else:
                recs.append(dict(p))

        with _Timer() as t:
            errors = validate_recommendations(recs, candidates, expected)
        log.info("[%s] generate attempt %d picks=%s errors=%s", rid, attempt, [p.get("id") for p in picks], errors)
        if not errors:
            result.trace.append(TraceStep("check", True, f"attempt {attempt}: all picks grounded in retrieved candidates", t.ms))
            result.summary = gen.summary
            return recs
        result.trace.append(TraceStep("check", False, f"attempt {attempt}: " + "; ".join(errors), t.ms))
        feedback = errors

    return None


def _meta_str(meta: Optional[llm.LLMMeta]) -> str:
    if meta is None:
        return ""
    return f"({meta.model}, {meta.latency_ms:.0f}ms, {meta.prompt_tokens}->{meta.output_tokens} tok)"


def _add_tokens(result: Result, meta: Optional[llm.LLMMeta]) -> None:
    if meta:
        result.llm_tokens += (meta.prompt_tokens or 0) + (meta.output_tokens or 0)


def run_query(text: str, k: int = 5, offline: bool = False, corrupt_attempts: int = 0) -> Result:
    """
    Full pipeline from free text. Uses Gemini when a key is set and offline=False;
    otherwise (or on any Gemini error) the deterministic offline path.
    corrupt_attempts (debug): number of Gemini generate answers to sabotage, to demo the retry/fallback loop.
    """
    setup_logging()
    result = Result(query=text or "", status="ok", mode="offline", request_id=uuid.uuid4().hex[:8])
    rid = result.request_id
    log.info("[%s] query=%r k=%d offline=%s", rid, (text or "")[:200], k, offline)

    # Guard
    with _Timer() as t:
        guard = guardrails.check_query(text)
    result.trace.append(TraceStep("guard", guard.allowed, guard.reason or "passed", t.ms))
    if not guard.allowed:
        result.status = "needs_clarification" if guard.reason == "empty" else "blocked"
        result.message = guard.message
        log.info("[%s] guardrail blocked: reason=%s", rid, guard.reason)
        return result

    _, genres, moods = get_catalog()
    use_llm = not offline and llm.is_available()

    # Parse: Gemini first, offline parser as the fallback.
    if use_llm:
        try:
            with _Timer() as t:
                parsed_llm = llm.parse_with_llm(guard.text, genres, moods)
        except llm.LLMError as exc:
            result.trace.append(TraceStep("parse", False, f"gemini error: {exc}", t.ms))
            result.warnings.append("Gemini is unavailable right now, so I used the offline parser.")
            log.warning("[%s] gemini parse failed: %s -> offline parser", rid, exc)
            use_llm = False  # don't wait on a second failing call for generate
        else:
            result.mode = "gemini"
            result.llm_calls += 1
            _add_tokens(result, parsed_llm.meta)
            result.trace.append(TraceStep(
                "parse", parsed_llm.on_topic,
                f"gemini: prefs={parsed_llm.prefs} avoid={parsed_llm.avoid_genres} "
                f"on_topic={parsed_llm.on_topic} {_meta_str(parsed_llm.meta)}",
                t.ms,
            ))
            log.info("[%s] parsed (gemini) prefs=%s avoid=%s on_topic=%s latency=%.0fms",
                     rid, parsed_llm.prefs, parsed_llm.avoid_genres, parsed_llm.on_topic, parsed_llm.meta.latency_ms)
            if not parsed_llm.on_topic:
                result.status = "needs_clarification"
                result.message = "I can only help with music. " + guardrails.CLARIFY_MESSAGE
                return result
            return recommend_from_profile(result, parsed_llm.prefs, parsed_llm.avoid_genres, k,
                                          use_llm=True, corrupt_attempts=corrupt_attempts)

    with _Timer() as t:
        parsed = parse_query(guard.text, genres, moods)
    reason = "" if offline else " (no GEMINI_API_KEY)" if not llm.is_available() else ""
    result.trace.append(TraceStep("parse", bool(parsed.prefs), f"offline{reason}: {', '.join(parsed.notes) or 'nothing matched'}", t.ms))
    log.info("[%s] parsed (offline) prefs=%s avoid=%s notes=%s", rid, parsed.prefs, parsed.avoid_genres, parsed.notes)

    return recommend_from_profile(result, parsed.prefs, parsed.avoid_genres, k)
