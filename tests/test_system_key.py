"""An administrator may let particular people fall back to the server's Anthropic key when they have none of their own: their own key always
comes first, nobody else ever gets the server's, every such run is counted (with its tokens, for an estimate), and an optional monthly limit
stops it. Nothing here ever shows or sends the key itself."""
from datetime import datetime, timedelta

import pytest

from app import config, crud, models
from app.database import SessionLocal
from tests.test_accounts import ADMIN, key, people, person, production  # noqa: F401 - fixtures
from tests.test_builder import KEY_A, Fake, GOOD_YAML, accepts, key_storage, run_draft  # noqa: F401 - key_storage is an autouse fixture

SERVER_KEY = "sk-ant-api03-the-servers-own-key-9999"


@pytest.fixture(autouse=True)
def server_key(monkeypatch):
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", SERVER_KEY)
    monkeypatch.setattr(config, "HOUSE_KEY_CALLS_PER_MONTH", 0)  # the default: no limit


def id_of(owner, email):
    return next(u["id"] for u in owner.get("/api/users").json() if u["email"] == email)


def allow(owner, email, allowed=True):
    return owner.put(f"/api/users/{id_of(owner, email)}/system-key", json={"allowed": allowed})


def with_material(who):
    who.post("/api/engagements", json={"client": "Acme"})  # something to draft from
    return who


def usage(owner, email):
    return next(u for u in owner.get("/api/users").json() if u["email"] == email)


def test_someone_not_allowed_and_with_no_key_of_their_own_cannot_use_the_server_key(person, people, monkeypatch):
    accepts(monkeypatch)
    fake = Fake(monkeypatch, [GOOD_YAML])
    bob = with_material(person("bob@example.com"))
    resp = run_draft(bob)
    assert resp.status_code == 400 and "your own Anthropic key" in resp.json()["detail"] and fake.calls == []
    assert bob.get("/api/api-key").json()["usable"] is False and bob.get("/api/api-key").json()["system_key"] is False


def test_an_allowed_person_drafts_on_the_server_key_and_the_run_is_counted_with_a_cost_estimate(person, people, monkeypatch):
    accepts(monkeypatch)
    fake = Fake(monkeypatch, [GOOD_YAML])
    owner, bob = person(ADMIN), with_material(person("bob@example.com"))
    assert allow(owner, "bob@example.com").json()["system_key"] is True
    status = bob.get("/api/api-key").json()
    assert status["usable"] and status["system_key"] and status["using_house_key"] and status["system_runs_left"] is None
    assert run_draft(bob).status_code == 200
    assert fake.keys[-1] == SERVER_KEY
    row = usage(owner, "bob@example.com")
    assert row["system_key"] and row["runs_this_month"] == 1 and row["cost_this_month"] == 0.02  # 1000 in + 500 out at 5 and 25 dollars per million


def test_their_own_key_always_comes_first_and_such_a_run_is_not_counted(person, people, monkeypatch):
    accepts(monkeypatch)
    fake = Fake(monkeypatch, [GOOD_YAML])
    owner, bob = person(ADMIN), with_material(person("bob@example.com"))
    allow(owner, "bob@example.com")
    bob.put("/api/api-key", json={"key": KEY_A})
    assert run_draft(bob).status_code == 200 and fake.keys[-1] == KEY_A
    assert usage(owner, "bob@example.com")["runs_this_month"] == 0
    assert bob.get("/api/api-key").json()["using_house_key"] is False


def test_taking_the_permission_away_stops_it(person, people, monkeypatch):
    accepts(monkeypatch)
    Fake(monkeypatch, [GOOD_YAML])
    owner, bob = person(ADMIN), with_material(person("bob@example.com"))
    allow(owner, "bob@example.com")
    allow(owner, "bob@example.com", False)
    assert run_draft(bob).status_code == 400


def test_pasted_list_imports_and_reviews_on_the_server_key_count_too(person, people, monkeypatch):
    accepts(monkeypatch)
    fake = Fake(monkeypatch, ['[{"client": "Acme"}]', "[]", GOOD_YAML])
    owner, bob = person(ADMIN), with_material(person("bob@example.com"))
    allow(owner, "bob@example.com")
    assert bob.post("/api/engagements/import", json={"text": "1. Acme"}).status_code == 200
    assert bob.post("/api/builder/review", json={"resume_yaml": GOOD_YAML}).status_code == 200
    assert run_draft(bob).status_code == 200
    assert fake.keys == [SERVER_KEY] * 3 and usage(owner, "bob@example.com")["runs_this_month"] == 3


def test_a_run_that_fails_is_still_counted_because_it_was_paid_for(person, people, monkeypatch):
    accepts(monkeypatch)
    Fake(monkeypatch, [RuntimeError("boom")])
    owner, bob = person(ADMIN), with_material(person("bob@example.com"))
    allow(owner, "bob@example.com")
    assert run_draft(bob).status_code == 400
    assert usage(owner, "bob@example.com")["runs_this_month"] == 1


def test_an_optional_monthly_limit_stops_the_fallback_and_says_how_to_carry_on(person, people, monkeypatch):
    accepts(monkeypatch)
    monkeypatch.setattr(config, "HOUSE_KEY_CALLS_PER_MONTH", 2)
    Fake(monkeypatch, [GOOD_YAML] * 3)
    owner, bob = person(ADMIN), with_material(person("bob@example.com"))
    allow(owner, "bob@example.com")
    assert bob.get("/api/api-key").json()["system_runs_left"] == 2
    assert run_draft(bob).status_code == 200 and run_draft(bob).status_code == 200
    over = run_draft(bob)
    assert over.status_code == 429 and "2 AI runs" in over.json()["detail"] and "your own Anthropic key" in over.json()["detail"]
    status = bob.get("/api/api-key").json()
    assert status["usable"] is False and status["system_runs_left"] == 0


def test_last_months_runs_do_not_count_against_this_months_limit(person, people, monkeypatch):
    monkeypatch.setattr(config, "HOUSE_KEY_CALLS_PER_MONTH", 1)
    owner, bob = person(ADMIN), person("bob@example.com")
    allow(owner, "bob@example.com")
    with SessionLocal() as db:
        db.add(models.HouseKeyUse(user_id=id_of(owner, "bob@example.com"), kind="draft", at=crud._month_start() - timedelta(days=3)))
        db.commit()
    assert bob.get("/api/api-key").json()["system_runs_left"] == 1


def test_only_an_administrator_can_switch_it_on_and_not_for_an_administrator_or_a_removed_person(person, people):
    bob, owner = person("bob@example.com"), person(ADMIN)
    assert bob.put(f"/api/users/{id_of(owner, 'carol@example.com')}/system-key", json={"allowed": True}).status_code == 403
    assert allow(owner, ADMIN).status_code in (404, 409)  # an administrator already can
    assert allow(owner, "eve@example.com").status_code == 409  # removed: restore them first


def test_an_administrators_own_use_of_the_server_key_is_not_counted(person, people, monkeypatch):
    accepts(monkeypatch)
    Fake(monkeypatch, [GOOD_YAML])
    owner = with_material(person(ADMIN))
    assert run_draft(owner).status_code == 200
    with SessionLocal() as db:
        assert db.query(models.HouseKeyUse).count() == 0


def test_deleting_a_removed_persons_data_deletes_their_run_counts_too(person, people, monkeypatch):
    accepts(monkeypatch)
    Fake(monkeypatch, [GOOD_YAML])
    owner, bob = person(ADMIN), with_material(person("bob@example.com"))
    allow(owner, "bob@example.com")
    run_draft(bob)
    bob_id = id_of(owner, "bob@example.com")
    owner.patch(f"/api/users/{bob_id}", json={"status": "removed"})
    assert owner.delete(f"/api/users/{bob_id}/data").status_code == 200
    with SessionLocal() as db:
        assert db.query(models.HouseKeyUse).count() == 0


def test_the_server_key_never_appears_in_anything_the_page_receives(person, people, monkeypatch):
    accepts(monkeypatch)
    Fake(monkeypatch, [GOOD_YAML])
    owner, bob = person(ADMIN), with_material(person("bob@example.com"))
    allow(owner, "bob@example.com")
    run_draft(bob)
    for who in (owner, bob):
        for path in ("/api/api-key", "/api/me", "/api/users"):
            resp = who.get(path)
            assert SERVER_KEY not in resp.text and "9999" not in resp.text, (path, resp.status_code)
