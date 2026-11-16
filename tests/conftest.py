import pytest


@pytest.fixture(autouse=True)
def no_real_gemini(monkeypatch):
    """Never hit the real API in tests. An empty (but set) key also stops load_dotenv() from filling it in."""
    monkeypatch.setenv("GEMINI_API_KEY", "")
