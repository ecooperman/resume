"""Scoring a job against the person's resume: one AI run on their own key (or the system key if an administrator allowed them it, counted),
never touching the job when it fails."""
import json

import pytest

from app import config, generate, models
from app.database import SessionLocal
from tests.test_accounts import ADMIN, bobs_resume, key, people, person, production  # noqa: F401 - fixtures
from tests.test_builder import KEY_A, Cut, Fake, accepts, key_storage  # noqa: F401 - key_storage is an autouse fixture
from tests.dashboard_keys import save_key  # noqa: E402

SERVER_KEY = "sk-ant-api03-the-servers-own-key-9999"
JD = "We need a Staff Engineer with Python and Kubernetes. Pay: $200,000 - $240,000. Kubernetes is preferred."
SCORE = {
    "tier": "Good", "summary": "Strong Python, no Kubernetes.",
    "matched": [{"requirement": "Python", "evidence": "10 years at Acme"}],
    "gaps": [{"requirement": "Kubernetes", "importance": "must-have"}, {"requirement": "Terraform", "importance": "nice-to-have"}],
    "strengths": ["Mentoring"], "salary": "$200,000 - $240,000",
}


@pytest.fixture(autouse=True)
def server_key(monkeypatch):
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", SERVER_KEY)
    monkeypatch.setattr(config, "HOUSE_KEY_CALLS_PER_MONTH", 0)


def ready(person, monkeypatch, answers, email="bob@example.com", with_key=True, with_resume=True):
    accepts(monkeypatch)
    fake = Fake(monkeypatch, answers)
    who = person(email)
    if with_key:
        save_key(who, KEY_A)
    if with_resume:
        bobs_resume(who)
    return who, fake


def job(who, **extra):
    resp = who.post("/api/jobs", json={"name": "Staff Engineer", "company": "Acme", "jd_text": JD, **extra})
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_scoring_saves_the_result_on_the_job_in_the_extensions_format(person, people, monkeypatch):
    bob, fake = ready(person, monkeypatch, [json.dumps(SCORE)])
    j = job(bob)
    scored = bob.post(f"/api/jobs/{j['id']}/score")
    assert scored.status_code == 200, scored.text
    got = scored.json()
    assert got["tier"] == "Good" and got["summary"] == "Strong Python, no Kubernetes." and got["salary"] == "$200,000 - $240,000"
    assert got["matched"] == "Python - 10 years at Acme" and got["gaps"] == "Kubernetes (must-have) | Terraform" and got["strengths"] == "Mentoring"
    assert got["scored_at"] and bob.get("/api/jobs").json()[0]["tier"] == "Good"
    sent = fake.calls[0]["messages"][0]["content"]
    assert fake.keys[-1] == KEY_A and "Staff Engineer" in sent and "Kubernetes" in sent and "Bob" in sent  # the job, the posting and their resume
    assert "never instructions" in fake.calls[0]["system"]


def test_an_odd_answer_is_tidied_an_unknown_tier_becomes_unclear_and_pipes_cannot_split_an_item(person, people, monkeypatch):
    odd = {"tier": "Fantastic", "summary": "x", "matched": [{"requirement": "A | B", "evidence": ""}], "gaps": ["Plain string gap", None, ""], "strengths": ["s" * 900], "salary": ""}
    bob, _ = ready(person, monkeypatch, [json.dumps(odd)])
    got = bob.post(f"/api/jobs/{job(bob, salary='$100k')['id']}/score").json()
    assert got["tier"] == "Unclear" and got["matched"] == "A / B" and got["gaps"] == "Plain string gap"
    assert len(got["strengths"]) <= 400 and got["salary"] == "$100k"  # a pasted pay figure stays when the score found none


def test_a_fenced_answer_is_fine_and_a_new_score_replaces_the_old_one(person, people, monkeypatch):
    bob, _ = ready(person, monkeypatch, ["```json\n" + json.dumps(SCORE) + "\n```", json.dumps({**SCORE, "tier": "Strong", "gaps": []})])
    j = job(bob)
    assert bob.post(f"/api/jobs/{j['id']}/score").json()["tier"] == "Good"
    again = bob.post(f"/api/jobs/{j['id']}/score").json()
    assert again["tier"] == "Strong" and again["gaps"] is None


@pytest.mark.parametrize("answer,words", [("Looks like a good fit!", "couldn't be read"), ("[1, 2]", "couldn't be read")])
def test_an_unreadable_answer_is_a_plain_message_and_the_job_is_untouched(person, people, monkeypatch, answer, words):
    bob, _ = ready(person, monkeypatch, [answer])
    j = job(bob)
    resp = bob.post(f"/api/jobs/{j['id']}/score")
    assert resp.status_code == 400 and words in resp.json()["detail"]
    assert bob.get(f"/api/jobs/{j['id']}").json()["tier"] is None


def test_an_answer_cut_off_for_lack_of_room_is_never_used(person, people, monkeypatch):
    bob, _ = ready(person, monkeypatch, [Cut(json.dumps(SCORE))])
    j = job(bob)
    resp = bob.post(f"/api/jobs/{j['id']}/score")
    assert resp.status_code == 400 and "cut off" in resp.json()["detail"]
    assert bob.get(f"/api/jobs/{j['id']}").json()["tier"] is None


def test_scoring_needs_the_description_a_saved_resume_and_a_key(person, people, monkeypatch):
    bob, fake = ready(person, monkeypatch, [json.dumps(SCORE)])
    nodesc = bob.post("/api/jobs", json={"name": "No text"}).json()
    r = bob.post(f"/api/jobs/{nodesc['id']}/score")
    assert r.status_code == 400 and "description" in r.json()["detail"]
    carol, _ = ready(person, monkeypatch, [json.dumps(SCORE)], email="carol@example.com", with_resume=False)
    r = carol.post(f"/api/jobs/{job(carol)['id']}/score")
    assert r.status_code == 400 and "Save your resume first" in r.json()["detail"]
    assert fake.calls == []  # none of the refusals reached the AI


def test_without_a_key_of_their_own_and_not_allowed_the_system_key_it_says_how_to_turn_it_on(person, people, monkeypatch):
    bob, fake = ready(person, monkeypatch, [json.dumps(SCORE)], with_key=False)
    r = bob.post(f"/api/jobs/{job(bob)['id']}/score")
    assert r.status_code == 400 and "your own Anthropic key" in r.json()["detail"] and fake.calls == []


def test_someone_elses_job_is_a_404_and_nothing_is_sent(person, people, monkeypatch):
    bob, fake = ready(person, monkeypatch, [json.dumps(SCORE)])
    carol, _ = ready(person, monkeypatch, [json.dumps(SCORE)], email="carol@example.com")
    j = job(bob)
    r = carol.post(f"/api/jobs/{j['id']}/score")
    assert r.status_code == 404 and fake.calls == []


def test_an_allowed_person_scores_on_the_system_key_and_it_is_counted_an_administrator_is_not(person, people, monkeypatch):
    bob, fake = ready(person, monkeypatch, [json.dumps(SCORE), json.dumps(SCORE)], with_key=False)
    owner = person(ADMIN)
    bob_id = next(u["id"] for u in owner.get("/api/users").json() if u["email"] == "bob@example.com")
    assert owner.put(f"/api/users/{bob_id}/system-key", json={"allowed": True}).status_code == 200
    assert bob.post(f"/api/jobs/{job(bob)['id']}/score").status_code == 200 and fake.keys[-1] == SERVER_KEY
    row = next(u for u in owner.get("/api/users").json() if u["email"] == "bob@example.com")
    assert row["runs_this_month"] == 1 and row["cost_this_month"] == 0.02
    bobs_resume_owner = owner.post("/api/people", json={"name": "Owner", "slug": "owner", "resume_yaml": "basics: {name: Owner}"})
    assert bobs_resume_owner.status_code == 200
    assert owner.post(f"/api/jobs/{job(owner)['id']}/score").status_code == 200
    with SessionLocal() as db:
        assert db.query(models.HouseKeyUse).filter_by(kind="score").count() == 1  # only bob's


def test_a_job_tied_to_a_resume_is_scored_against_that_one(person, people, monkeypatch):
    accepts(monkeypatch)
    fake = Fake(monkeypatch, [json.dumps(SCORE)])
    owner = person(ADMIN)
    save_key(owner, KEY_A)
    owner.post("/api/people", json={"name": "First", "slug": "first", "resume_yaml": "basics: {name: FIRST-RESUME-PERSON}"})
    second = owner.post("/api/people", json={"name": "Second", "slug": "second", "resume_yaml": "basics: {name: SECOND-RESUME-PERSON}"}).json()
    j = job(owner, person_id=second["id"])
    assert owner.post(f"/api/jobs/{j['id']}/score").status_code == 200
    sent = fake.calls[0]["messages"][0]["content"]
    assert "SECOND-RESUME-PERSON" in sent and "FIRST-RESUME-PERSON" not in sent


def test_only_the_first_part_of_a_very_long_posting_is_sent(person, people, monkeypatch):
    bob, fake = ready(person, monkeypatch, [json.dumps(SCORE)])
    j = job(bob, jd_text="REQ " * 10_000 + "THE-END-MARKER")
    bob.post(f"/api/jobs/{j['id']}/score")
    sent = fake.calls[0]["messages"][0]["content"]
    assert "THE-END-MARKER" not in sent and len(sent) < generate.JD_SCORE_CHARS + 20_000


def test_scoring_has_an_hourly_allowance_of_its_own(person, people, monkeypatch):
    monkeypatch.setattr(config, "SCORES_PER_HOUR", 2)
    bob, _ = ready(person, monkeypatch, [json.dumps(SCORE)] * 3)
    j = job(bob)
    assert [bob.post(f"/api/jobs/{j['id']}/score").status_code for _ in range(3)] == [200, 200, 429]
    assert "2 times an hour" in bob.post(f"/api/jobs/{j['id']}/score").json()["detail"]
