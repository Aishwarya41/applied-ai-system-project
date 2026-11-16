"""
Output validator: the "check" step of the agent loop.

Confirms that a set of recommendations is grounded in the retrieved
candidates. In phase 3 the Gemini output goes through the same check and,
on failure, gets one retry and then a fallback to the plain scorer.
"""

from typing import Dict, List


def validate_recommendations(
    recommendations: List[Dict], candidates: List[Dict], expected_count: int
) -> List[str]:
    """Return a list of problems; an empty list means the output is valid."""
    errors: List[str] = []
    by_id = {c["song"]["id"]: c["song"] for c in candidates}

    if len(recommendations) != expected_count:
        errors.append(f"Expected {expected_count} recommendations, got {len(recommendations)}.")

    seen = set()
    for i, rec in enumerate(recommendations, start=1):
        song_id = rec.get("id")
        if song_id not in by_id:
            errors.append(f"#{i}: song id {song_id!r} was not in the retrieved candidates.")
            continue
        if song_id in seen:
            errors.append(f"#{i}: song id {song_id} is duplicated.")
        seen.add(song_id)

        title = rec.get("title")
        if title is not None and title != by_id[song_id]["title"]:
            errors.append(
                f"#{i}: title {title!r} does not match song {song_id} "
                f"({by_id[song_id]['title']!r})."
            )
        if not str(rec.get("explanation", "")).strip():
            errors.append(f"#{i}: missing explanation.")

    return errors
