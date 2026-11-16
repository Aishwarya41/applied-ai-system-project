"""
Guardrails: run before (check_query) and after (sanitize_profile) parsing so
the scorer never sees junk input.

Fixes the failure modes documented in the README:
- out-of-range targets (e.g. energy 1.5) are clamped into [0, 1]
- unknown genres/moods are mapped to the nearest real label or dropped
- an empty profile asks a clarifying question instead of returning CSV order
"""

import math
import re
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Tuple

from src.vocab import GENRE_ALIASES, MOOD_ALIASES, normalize_label

MAX_QUERY_CHARS = 300

NUMERIC_KEYS = ("target_energy", "target_valence", "target_acousticness")
LABEL_KEYS = ("favorite_genre", "favorite_mood")

CLARIFY_MESSAGE = (
    "Tell me a bit about the vibe you want, for example a mood "
    "(\"happy\", \"chill\", \"intense\"), a genre (\"lofi\", \"rock\"), or an "
    "activity (\"studying\", \"workout\", \"rainy night drive\")."
)

_INJECTION_PATTERNS = [
    r"ignore (all |any |the )?(previous|prior|above|earlier) (instructions|prompts?)",
    r"\bsystem prompt\b",
    r"\bjailbreak\b",
    r"\byou are now\b",
]

_SELF_HARM_PATTERNS = [
    r"\bkill (myself|me)\b",
    r"\bsuicid",
    r"\bself[- ]harm",
    r"\bend my life\b",
]

_UNSAFE_PATTERNS = [
    r"\b(make|build) (a |an )?(bomb|weapon|explosive)",
    r"\bkill (someone|people|him|her|them)\b",
]


@dataclass
class GuardrailResult:
    allowed: bool
    text: str
    reason: Optional[str] = None  # empty | too_long | prompt_injection | self_harm | unsafe
    message: str = ""


@dataclass
class SanitizedProfile:
    prefs: Dict
    warnings: List[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.prefs


def _matches_any(text: str, patterns: List[str]) -> bool:
    return any(re.search(p, text) for p in patterns)


def check_query(text: Optional[str]) -> GuardrailResult:
    """Validate raw user text before any parsing or LLM call."""
    cleaned = " ".join((text or "").split())
    lowered = cleaned.lower()

    if not cleaned:
        return GuardrailResult(False, cleaned, "empty", CLARIFY_MESSAGE)

    if len(cleaned) > MAX_QUERY_CHARS:
        return GuardrailResult(
            False,
            cleaned,
            "too_long",
            f"That request is {len(cleaned)} characters; please keep it under "
            f"{MAX_QUERY_CHARS} and describe just the vibe you're after.",
        )

    if _matches_any(lowered, _SELF_HARM_PATTERNS):
        return GuardrailResult(
            False,
            cleaned,
            "self_harm",
            "I'm only a music recommender, but it sounds like you might be going "
            "through something hard. Please reach out to someone you trust, or in "
            "the US call or text 988 (Suicide & Crisis Lifeline).",
        )

    if _matches_any(lowered, _UNSAFE_PATTERNS):
        return GuardrailResult(
            False, cleaned, "unsafe", "I can only help with music recommendations."
        )

    if _matches_any(lowered, _INJECTION_PATTERNS):
        return GuardrailResult(
            False,
            cleaned,
            "prompt_injection",
            "I can only help with music recommendations. What kind of vibe are you after?",
        )

    return GuardrailResult(True, cleaned)


def _clamp_numeric(key: str, value, warnings: List[str]) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        warnings.append(f"Ignored {key}={value!r}: not a number.")
        return None
    if math.isnan(number) or math.isinf(number):
        warnings.append(f"Ignored {key}={value!r}: not a finite number.")
        return None
    clamped = min(1.0, max(0.0, number))
    if clamped != number:
        warnings.append(f"{key} {number:g} is outside 0-1; clamped to {clamped:g}.")
    return clamped


def sanitize_profile(
    prefs: Dict, known_genres: Iterable[str], known_moods: Iterable[str]
) -> SanitizedProfile:
    """Clamp numbers, normalize labels to catalog values, drop anything unusable."""
    warnings: List[str] = []
    clean: Dict = {}

    for key in NUMERIC_KEYS:
        if prefs.get(key) is None:
            continue
        value = _clamp_numeric(key, prefs[key], warnings)
        if value is not None:
            clean[key] = value

    label_sources = {
        "favorite_genre": (known_genres, GENRE_ALIASES, "genre"),
        "favorite_mood": (known_moods, MOOD_ALIASES, "mood"),
    }
    for key, (known, aliases, noun) in label_sources.items():
        raw = prefs.get(key)
        if not raw:
            continue
        label, how = normalize_label(str(raw), known, aliases)
        if label is None:
            warnings.append(f"No songs are tagged with {noun} '{raw}'; ignoring it.")
        else:
            if how != "exact":
                warnings.append(f"Interpreted {noun} '{raw}' as '{label}'.")
            clean[key] = label

    warnings.extend(detect_conflicts(clean))
    return SanitizedProfile(clean, warnings)


_CALM_MOODS = {"chill", "relaxed", "peaceful", "melancholic", "wistful"}
_HIGH_MOODS = {"intense", "angry", "energetic", "euphoric"}


def detect_conflicts(prefs: Dict) -> List[str]:
    """Flag contradictory requests (e.g. 'sad but hyper') so the user knows results will be a compromise."""
    notes = []
    energy = prefs.get("target_energy")
    mood = prefs.get("favorite_mood")
    acoustic = prefs.get("target_acousticness")

    if energy is not None and mood in _CALM_MOODS and energy >= 0.75:
        notes.append(
            f"Heads up: '{mood}' songs are usually low-energy, but you asked for "
            f"energy {energy:.2f}. Results will be a compromise."
        )
    if energy is not None and mood in _HIGH_MOODS and energy <= 0.3:
        notes.append(
            f"Heads up: '{mood}' songs are usually high-energy, but you asked for "
            f"energy {energy:.2f}. Results will be a compromise."
        )
    if energy is not None and acoustic is not None and energy >= 0.85 and acoustic >= 0.8:
        notes.append(
            "Heads up: very high energy and very acoustic rarely go together in "
            "this catalog. Results will be a compromise."
        )
    return notes
