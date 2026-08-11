from __future__ import annotations

from app import create_app


def _app():
    return create_app(
        {
            "TESTING": True,
            "SECRET_KEY": "health-test",
            "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
            "TO_BELL_PUSH_SCHEDULER_ENABLED": False,
        }
    )


def test_liveness_and_readiness_are_public_and_healthy() -> None:
    client = _app().test_client()
    assert client.get("/health/live").get_json() == {"status": "ok"}
    ready = client.get("/health/ready")
    assert ready.status_code == 200
    assert ready.get_json() == {"status": "ok"}
