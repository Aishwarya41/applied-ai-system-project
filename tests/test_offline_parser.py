from src.offline_parser import parse_query

GENRES = {"pop", "lofi", "rock", "jazz", "hip hop", "indie pop", "metal"}
MOODS = {"happy", "chill", "intense", "melancholic", "peaceful", "relaxed", "focused"}


def parse(text):
    return parse_query(text, GENRES, MOODS)


def test_study_query_is_low_energy_and_acoustic():
    prefs = parse("chill study music").prefs
    assert prefs["favorite_mood"] == "chill"
    assert prefs["target_energy"] < 0.5
    assert prefs["target_acousticness"] > 0.6


def test_activity_implies_mood_when_none_given():
    assert parse("songs for the gym").prefs["favorite_mood"] == "intense"


def test_explicit_mood_beats_implied_mood():
    assert parse("happy workout songs").prefs["favorite_mood"] == "happy"


def test_negated_genre_becomes_exclusion():
    parsed = parse("upbeat stuff but no pop")
    assert parsed.avoid_genres == ["pop"]
    assert parsed.prefs.get("favorite_genre") is None


def test_longer_genre_name_wins():
    assert parse("some indie pop please").prefs["favorite_genre"] == "indie pop"


def test_alias_maps_to_catalog_genre():
    assert parse("old school rap").prefs["favorite_genre"] == "hip hop"


def test_explicit_number_overrides_cues_and_is_not_clamped_here():
    prefs = parse("mellow but energy 1.5").prefs
    assert prefs["target_energy"] == 1.5  # guardrails clamp later


def test_off_topic_text_yields_empty_profile():
    assert parse("what is the capital of France").prefs == {}


def test_parser_is_deterministic():
    text = "something mellow for studying on a rainy night, no vocals-heavy pop"
    assert parse(text) == parse(text)
