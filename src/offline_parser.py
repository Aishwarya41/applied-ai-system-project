"""
Offline (deterministic, no API key) parser: free text -> preference profile.

Used when GEMINI_API_KEY is missing, when --offline is set, and as the
fallback whenever the LLM parse fails. Same input always gives same output.
"""

import re
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Tuple

from src.vocab import GENRE_ALIASES, MOOD_ALIASES

# Activity / setting words -> numeric targets (+ an implied mood, if any).
CONTEXT_CUES: List[Tuple[Tuple[str, ...], Dict[str, float], str | None]] = [
    (("study", "studying", "homework", "reading", "coding", "working", "work"),
     {"target_energy": 0.40, "target_acousticness": 0.75}, "focused"),
    (("workout", "gym", "running", "run", "lifting", "exercise", "training", "cardio"),
     {"target_energy": 0.92, "target_valence": 0.60, "target_acousticness": 0.08}, "intense"),
    (("party", "dance", "dancing", "club", "festival"),
     {"target_energy": 0.88, "target_valence": 0.85, "target_acousticness": 0.10}, "euphoric"),
    (("sleep", "sleeping", "bedtime", "meditate", "meditation", "yoga", "spa"),
     {"target_energy": 0.20, "target_acousticness": 0.92}, "peaceful"),
    (("rain", "rainy", "night", "late night", "midnight", "evening"),
     {"target_energy": 0.40, "target_valence": 0.50}, None),
    (("drive", "driving", "road trip", "roadtrip", "commute"),
     {"target_energy": 0.78}, None),
    (("dinner", "date", "date night"),
     {"target_energy": 0.45, "target_valence": 0.65}, "romantic"),
    (("coffee", "cafe", "sunday morning", "brunch"),
     {"target_energy": 0.38, "target_acousticness": 0.85}, "relaxed"),
]

# Descriptive words -> numeric targets only.
INTENSITY_CUES: List[Tuple[Tuple[str, ...], Dict[str, float]]] = [
    (("high energy", "high-energy", "upbeat", "energetic", "hype", "hyper", "loud",
      "fast", "pumped", "intense", "banger", "bangers"), {"target_energy": 0.88}),
    (("low energy", "low-energy", "mellow", "calm", "chill", "quiet", "soft", "slow",
      "gentle", "relaxing", "lazy"), {"target_energy": 0.30}),
    (("happy", "uplifting", "cheerful", "bright", "sunny", "positive", "feel good",
      "feel-good"), {"target_valence": 0.82}),
    (("sad", "dark", "melancholy", "melancholic", "gloomy", "moody", "heartbreak",
      "heartbroken", "crying"), {"target_valence": 0.30}),
    (("acoustic", "unplugged", "organic", "instrumental"), {"target_acousticness": 0.88}),
    (("electronic", "synth", "synthy", "edm"), {"target_acousticness": 0.10}),
]

_NEGATION = re.compile(r"\b(no|not|without|except|avoid|hate|skip|minus|nothing)\s+(\S+\s+){0,2}$")

_EXPLICIT_NUMBER = re.compile(
    r"\b(energy|valence|acousticness|acoustic)\b\s*(?:of|=|:|at|around|about|is|to)?\s*"
    r"(-?\d+(?:\.\d+)?|-?\.\d+)"
)
_NUMBER_KEYS = {
    "energy": "target_energy",
    "valence": "target_valence",
    "acousticness": "target_acousticness",
    "acoustic": "target_acousticness",
}


@dataclass
class ParsedQuery:
    prefs: Dict = field(default_factory=dict)
    avoid_genres: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)  # human-readable trace of what matched


def _is_negated(text: str, start: int) -> bool:
    return bool(_NEGATION.search(text[:start]))


def _find_terms(text: str, terms: Iterable[str]) -> List[Tuple[int, int, str]]:
    """Find whole-word occurrences of terms, preferring longer terms ("indie pop" over "pop")."""
    found: List[Tuple[int, int, str]] = []
    for term in sorted(set(terms), key=len, reverse=True):
        for m in re.finditer(rf"(?<![\w-]){re.escape(term)}(?![\w-])", text):
            if any(m.start() < e and s < m.end() for s, e, _ in found):
                continue
            found.append((m.start(), m.end(), term))
    return sorted(found)


def _label_mentions(text: str, known: Iterable[str], aliases: Dict[str, str]):
    """Yield (label, term, negated) for each genre/mood mention, in text order."""
    known = set(known)
    lookup = {k: k for k in known}
    lookup.update({a: t for a, t in aliases.items() if t in known})
    for start, _, term in _find_terms(text, lookup):
        yield lookup[term], term, _is_negated(text, start)


def parse_query(text: str, known_genres: Iterable[str], known_moods: Iterable[str]) -> ParsedQuery:
    text = (text or "").lower()
    result = ParsedQuery()
    prefs = result.prefs

    # 1. Genres: first positive mention wins; negated mentions become exclusions.
    for label, term, negated in _label_mentions(text, known_genres, GENRE_ALIASES):
        if negated:
            if label not in result.avoid_genres:
                result.avoid_genres.append(label)
                result.notes.append(f"avoid genre: {label}")
        elif "favorite_genre" not in prefs:
            prefs["favorite_genre"] = label
            result.notes.append(f"genre: {label}" + (f" (from '{term}')" if term != label else ""))

    # 2. Moods: first positive explicit mention.
    for label, term, negated in _label_mentions(text, known_moods, MOOD_ALIASES):
        if not negated and "favorite_mood" not in prefs:
            prefs["favorite_mood"] = label
            result.notes.append(f"mood: {label}" + (f" (from '{term}')" if term != label else ""))

    # 3. Context + intensity cues: average every matched target.
    collected: Dict[str, List[float]] = {}
    implied_mood = None
    all_cues = [(words, targets, mood) for words, targets, mood in CONTEXT_CUES]
    all_cues += [(words, targets, None) for words, targets in INTENSITY_CUES]
    for words, targets, mood in all_cues:
        hits = [(s, term) for s, _, term in _find_terms(text, words) if not _is_negated(text, s)]
        if not hits:
            continue
        result.notes.append(f"cue: {hits[0][1]}")
        for key, value in targets.items():
            collected.setdefault(key, []).append(value)
        if mood and implied_mood is None:
            implied_mood = mood
    for key, values in collected.items():
        prefs[key] = round(sum(values) / len(values), 2)

    if "favorite_mood" not in prefs and implied_mood and implied_mood in set(known_moods):
        prefs["favorite_mood"] = implied_mood
        result.notes.append(f"mood: {implied_mood} (implied by activity)")

    # 4. Explicit numbers ("energy 0.8") override cues. Not clamped here; guardrails do that.
    for m in _EXPLICIT_NUMBER.finditer(text):
        key = _NUMBER_KEYS[m.group(1)]
        prefs[key] = float(m.group(2))
        result.notes.append(f"explicit: {key}={m.group(2)}")

    return result
