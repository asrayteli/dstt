from types import SimpleNamespace

from flask import Flask

import app.tools.worktime as worktime_module


def _client(authenticated):
    app = Flask(__name__)
    app.config.update(TESTING=True)
    app.register_blueprint(worktime_module.worktime_bp)
    worktime_module.current_user = SimpleNamespace(is_authenticated=authenticated)
    return app.test_client()


def test_calculate_api_requires_login():
    response = _client(False).post("/tools/worktime/api/calculate", json={})
    assert response.status_code == 401
    assert response.get_json()["success"] is False


def test_calculate_api_validates_and_returns_hhmm_fields():
    client = _client(True)
    invalid = client.post("/tools/worktime/api/calculate", json={"year": 2026, "month": 13})
    assert invalid.status_code == 400
    response = client.post("/tools/worktime/api/calculate", json={
        "year": 2026,
        "month": 7,
        "codes": [{"key": "A", "category": "work", "times": {"weekday": {"start": "09:00", "end": "18:00"}}}],
        "people": [{"person_id": "p1", "days": [{"day": 1, "day_type": "weekday", "code": "A"}]}],
    })
    assert response.status_code == 200
    totals = response.get_json()["result"]["people"][0]["totals"]
    assert totals["bind_total_minutes"] == 540
    assert totals["bind_total_hhmm"] == "9:00"


def test_calculate_api_enforces_people_and_day_limits():
    client = _client(True)
    too_many = client.post("/tools/worktime/api/calculate", json={
        "year": 2026,
        "month": 7,
        "people": [{"person_id": f"p{index}", "days": []} for index in range(201)],
    })
    assert too_many.status_code == 400

    duplicate_day = client.post("/tools/worktime/api/calculate", json={
        "year": 2026,
        "month": 7,
        "people": [{
            "person_id": "p1",
            "days": [{"day": 1, "day_type": "weekday"}, {"day": 1, "day_type": "weekday"}],
        }],
    })
    assert duplicate_day.status_code == 400
