import pytest
from app.main import app
from app.auth import get_api_key

# We can also configure a dummy key in settings
from app.config import settings
settings.api_secret_key = "test-key"

from fastapi.testclient import TestClient

# Monkeypatch TestClient to inject the API key header
original_init = TestClient.__init__

def patched_init(self, *args, **kwargs):
    original_init(self, *args, **kwargs)
    self.headers["X-API-Key"] = "test-key"

TestClient.__init__ = patched_init


@pytest.fixture(autouse=True)
def _isolate_recommendation_cache():
    """Give every test a cold advisory cache.

    The cache is deliberately shared across recommender instances in
    production — that is what lets both A/B arms reason from identical advice.
    In a test suite the same sharing means one test's mocked success is served
    to the next test's mocked failure, so each test starts clean.
    """
    from app.recommendation.engine import _SHARED_CACHE

    _SHARED_CACHE.clear()
    yield
    _SHARED_CACHE.clear()
