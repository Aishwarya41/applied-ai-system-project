"""
Shared vocabulary: maps everyday words onto the genre/mood labels that
actually exist in data/songs.csv.

The original scorer only rewarded exact label matches, so "sad" never
matched "melancholic" and "calm" never matched "peaceful". Normalizing
labels here fixes that bias for both the offline parser and guardrails.
"""

import difflib
from typing import Iterable, Optional, Tuple

GENRE_ALIASES = {
    "hip-hop": "hip hop",
    "hiphop": "hip hop",
    "rap": "hip hop",
    "electronic dance": "edm",
    "techno": "edm",
    "house": "edm",
    "lo-fi": "lofi",
    "lo fi": "lofi",
    "rnb": "r&b",
    "r and b": "r&b",
    "soul": "r&b",
    "indie": "indie pop",
    "k-pop": "pop",
    "kpop": "pop",
    "orchestral": "classical",
    "piano": "classical",
    "heavy metal": "metal",
    "retro": "synthwave",
    "80s": "synthwave",
    "bluegrass": "country",
}

MOOD_ALIASES = {
    "sad": "melancholic",
    "melancholy": "melancholic",
    "heartbroken": "melancholic",
    "calm": "peaceful",
    "serene": "peaceful",
    "meditative": "peaceful",
    "mellow": "chill",
    "chilled": "chill",
    "cozy": "relaxed",
    "relaxing": "relaxed",
    "upbeat": "happy",
    "cheerful": "happy",
    "joyful": "happy",
    "hype": "energetic",
    "hyped": "energetic",
    "pumped": "energetic",
    "aggressive": "angry",
    "rage": "angry",
    "love": "romantic",
    "dark": "moody",
    "brooding": "moody",
    "focus": "focused",
    "concentrate": "focused",
    "nostalgia": "nostalgic",
    "swagger": "confident",
    "bold": "confident",
    "laid back": "laid-back",
    "laidback": "laid-back",
    "ecstatic": "euphoric",
}

FUZZY_CUTOFF = 0.75


def normalize_label(
    raw: str, known: Iterable[str], aliases: dict
) -> Tuple[Optional[str], str]:
    """
    Map a free-form label to a known catalog label.

    Returns (label, how) where how is one of "exact", "alias", "fuzzy", "none".
    label is None when nothing reasonable matches.
    """
    known = set(known)
    value = (raw or "").strip().lower()
    if not value:
        return None, "none"
    if value in known:
        return value, "exact"
    target = aliases.get(value)
    if target in known:
        return target, "alias"
    close = difflib.get_close_matches(value, sorted(known), n=1, cutoff=FUZZY_CUTOFF)
    if close:
        return close[0], "fuzzy"
    return None, "none"
