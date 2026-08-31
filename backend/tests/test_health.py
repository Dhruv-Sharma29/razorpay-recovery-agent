from fastapi.testclient import TestClient

from app.main import app


def test_app_imports() -> None:
    assert app is not None
    assert app.title == "Razorpay Recovery Agent"


def test_health() -> None:
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
