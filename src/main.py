"""
Command line runner for VibeFinder.

    python -m src.main                                  # original profile demo
    python -m src.main --offline --query "chill study music"
    python -m src.main --query "upbeat workout, no pop" -k 3
"""

import argparse

from dotenv import load_dotenv

from src.agent import Result, run_query
from src.recommender import load_songs, recommend_songs


# Numeric values are targets to be close to, not minimums.
PROFILES = {
    # --- Distinct, well-behaved profiles ---
    "High-Energy Pop": {
        "favorite_genre": "pop",
        "favorite_mood": "happy",
        "target_energy": 0.90,
        "target_valence": 0.85,
        "target_acousticness": 0.15,
    },
    "Chill Lofi": {
        "favorite_genre": "lofi",
        "favorite_mood": "chill",
        "target_energy": 0.40,
        "target_valence": 0.60,
        "target_acousticness": 0.75,
    },
    "Deep Intense Rock": {
        "favorite_genre": "rock",
        "favorite_mood": "intense",
        "target_energy": 0.92,
        "target_valence": 0.40,
        "target_acousticness": 0.08,
    },

    # --- Adversarial / edge-case profiles ---
    # Conflicting: "sad" usually means low energy, but asks for 0.9 energy.
    # Also, no song in the catalog has mood "sad".
    "Sad but Hyper": {
        "favorite_genre": "pop",
        "favorite_mood": "sad",
        "target_energy": 0.90,
        "target_valence": 0.15,
        "target_acousticness": 0.50,
    },
    # Genre says calm, but everything else says loud and aggressive.
    "Lofi Genre, Metal Everything Else": {
        "favorite_genre": "lofi",
        "favorite_mood": "angry",
        "target_energy": 0.95,
        "target_valence": 0.30,
        "target_acousticness": 0.05,
    },
    # Physically implausible: very high energy AND very high acousticness.
    "Loud Unplugged": {
        "favorite_genre": "folk",
        "favorite_mood": "wistful",
        "target_energy": 0.95,
        "target_valence": 0.50,
        "target_acousticness": 0.95,
    },
    # Genre and mood that don't exist in the data: only numbers can score.
    "Unknown Genre/Mood": {
        "favorite_genre": "k-pop",
        "favorite_mood": "nostalgic-but-hype",
        "target_energy": 0.50,
        "target_valence": 0.50,
        "target_acousticness": 0.50,
    },
    # Targets outside the 0-1 range: every similarity collapses toward 0.
    "Out-of-Range Targets": {
        "favorite_genre": "edm",
        "favorite_mood": "euphoric",
        "target_energy": 1.5,
        "target_valence": -0.3,
        "target_acousticness": 2.0,
    },
    # No preferences at all: every score is 0.0, so ranking is just CSV order.
    "Empty Profile": {},
}


def run_profile_demo() -> None:
    """Original demo: raw scorer on hard-coded profiles (no guardrails, by design)."""
    songs = load_songs("data/songs.csv")

    for name, user_prefs in PROFILES.items():
        recommendations = recommend_songs(user_prefs, songs, k=5)

        print(f"\n=== {name} ===")
        for song, score, explanation in recommendations:
            print(f"{song['title']} ({song['genre']}/{song['mood']}) - Score: {score:.2f}")
            print(f"  Because: {explanation}")


def print_result(result: Result) -> None:
    print(f"\nQuery: {result.query!r}   [mode={result.mode} status={result.status} id={result.request_id}]")
    if result.message:
        print(f"\n{result.message}")
    if result.summary:
        print(f"\n{result.summary}")
    if result.profile:
        print(f"Parsed profile: {result.profile}")
    if result.avoid_genres:
        print(f"Avoiding genres: {', '.join(result.avoid_genres)}")
    for warning in result.warnings:
        print(f"  ! {warning}")

    for i, rec in enumerate(result.recommendations, start=1):
        print(f"\n{i}. {rec['title']} by {rec['artist']} ({rec['genre']}/{rec['mood']})  score {rec['score']:.2f}")
        print(f"   {rec['explanation']}")

    print("\nTrace:")
    for step in result.trace:
        mark = "ok " if step.ok else "-- "
        print(f"  {mark}{step.stage:<9} {step.ms:6.1f}ms  {step.detail}")


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="VibeFinder music recommender")
    parser.add_argument("-q", "--query", help="natural-language request, e.g. 'chill study music'")
    parser.add_argument("--offline", action="store_true", help="skip the LLM; use the rule-based parser")
    parser.add_argument("-k", type=int, default=5, help="number of recommendations (default 5)")
    parser.add_argument("--corrupt", type=int, default=0, choices=[0, 1, 2],
                        help="debug: sabotage N Gemini answers to demo the retry/fallback loop")
    args = parser.parse_args()

    if args.query is None:
        run_profile_demo()
        return
    print_result(run_query(args.query, k=args.k, offline=args.offline, corrupt_attempts=args.corrupt))


if __name__ == "__main__":
    main()
