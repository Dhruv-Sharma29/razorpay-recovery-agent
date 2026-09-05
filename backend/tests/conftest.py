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
