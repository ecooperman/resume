"""The numbers the dashboard's stats pages read: administrators only, counts and dates only, everyone's accounts."""

from app import clock, models
from app.database import SessionLocal
from shared_python import owned
from tests.test_accounts import ADMIN, key, people, person, production  # noqa: F401 - fixtures
from tests.test_accounts import bobs_resume


def test_only_an_administrator_can_read_the_stats(person, people):
    assert person("bob@example.com").get("/api/admin/stats").status_code == 403
    assert person(ADMIN).get("/api/admin/stats").status_code == 200


def test_the_stats_count_each_persons_things_without_showing_any_of_them(person, people):
    bob, owner = person("bob@example.com"), person(ADMIN)
    bobs_resume(bob)
    bob.post("/api/jobs", json={"name": "SECRET TITLE", "company": "SECRET CO", "jd_text": "SECRET TEXT"})
    body = owner.get("/api/admin/stats")
    accounts = {a["email"]: a for a in body.json()["accounts"]}
    assert accounts["bob@example.com"]["counts"]["resumes"] == 1 and accounts["bob@example.com"]["counts"]["jobs"] == 1
    assert accounts["carol@example.com"]["counts"]["jobs"] == 0 and accounts["eve@example.com"]["status"] == "removed"
    assert accounts["bob@example.com"]["last_activity_at"] is not None
    for secret in ("SECRET TITLE", "SECRET CO", "SECRET TEXT"):
        assert secret not in body.text


def test_the_gate_notes_when_a_person_was_last_seen(person, people):
    bob = person("bob@example.com")
    assert bob.get("/api/me").status_code == 200
    with owned.unscoped(), SessionLocal() as db:
        user = db.query(models.User).filter(models.User.email == "bob@example.com").one()
        assert user.active_days == 1 and user.last_seen_at is not None and (clock.utcnow() - user.last_seen_at).total_seconds() < 60
