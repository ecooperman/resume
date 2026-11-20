"""The Chrome extension pushes jobs with a personal token, and unscored arrivals are scored in the background. What matters: a token is shown
once and kept only as a hash; it opens the push routes and nothing else; each person's token puts jobs only in their own account; a
bad, revoked or removed person's token gets nothing; pushing the same posting twice never wipes a score or a note; and the queue
does what the Score button does (their key, the allowance, counted on the system key) without a page held open."""
import json
import time

import pytest
from fastapi.testclient import TestClient

from app import admin, config, crud, limits, models, scoring, server
from app.database import SessionLocal
from shared_python import owned
from tests.test_accounts import ADMIN, bobs_resume, key, people, person, production  # noqa: F401 - fixtures
from tests.test_builder import KEY_A, Cut, Fake, accepts, key_storage  # noqa: F401 - key_storage is an autouse fixture
from tests.test_job_scoring import JD, SCORE, SERVER_KEY, server_key  # noqa: F401 - server_key is an autouse fixture

HOST = "https://resume-admin.example.org"


@pytest.fixture(autouse=True)
def fresh_queues():
    scoring.reset()
    limits.reset()
    yield
    scoring.reset()


def make_token(who, name="Laptop"):
    resp = who.post("/api/tokens", json={"name": name})
    assert resp.status_code == 200, resp.text
    return resp.json()


def pusher(token=None):
    """What the extension is: no cookie, no sign-in, only (maybe) a token."""
    c = TestClient(admin.app, base_url=HOST, follow_redirects=False)
    if token:
        c.headers["Authorization"] = f"Bearer {token}"
    return c


def posting(n=1, **extra):
    return {"url": f"https://jobs.example.com/{n}", "title": f"Engineer {n}", "company": "Acme", "jd_text": JD, **extra}


def wait_for_queue(account_id, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        got = scoring.status(account_id)
        if got["state"] != "running":
            return got
        time.sleep(0.02)
    raise AssertionError("the queue did not finish")


def ready(person, monkeypatch, answers, email="bob@example.com", with_key=True, with_resume=True):
    accepts(monkeypatch)
    fake = Fake(monkeypatch, answers)
    who = person(email)
    if with_key:
        who.put("/api/api-key", json={"key": KEY_A})
    if with_resume:
        bobs_resume(who)
    return who, fake


def bob_id(people):
    return people["bob"]


# --- tokens ---------------------------------------------------------------------------------------------------------------------------------


def test_a_token_is_shown_once_and_only_its_hash_is_kept(person, people):
    bob = person("bob@example.com")
    made = make_token(bob)
    assert made["token"].startswith(config.PUSH_TOKEN_PREFIX) and len(made["token"]) > 30
    listed = bob.get("/api/tokens").json()
    assert [t["name"] for t in listed] == ["Laptop"] and "token" not in listed[0]
    assert made["token"] not in json.dumps(listed) and listed[0]["prefix"] == made["token"][: len(listed[0]["prefix"])]
    with owned.unscoped(), SessionLocal() as db:
        row = db.query(models.PushToken).one()
        assert row.token_hash == crud.hash_token(made["token"]) and made["token"] not in (row.token_hash, row.prefix, row.name)


def test_tokens_are_private_to_their_owner(person, people):
    bob, carol = person("bob@example.com"), person("carol@example.com")
    made = make_token(bob)
    assert carol.get("/api/tokens").json() == []
    assert carol.delete(f"/api/tokens/{made['id']}").status_code == 404
    assert pusher(made["token"]).get("/api/push/ping").status_code == 200  # still works: carol could not revoke it


def test_a_token_needs_a_name_and_there_is_a_limit_on_how_many(person, people, monkeypatch):
    bob = person("bob@example.com")
    assert bob.post("/api/tokens", json={"name": "   "}).status_code == 400
    monkeypatch.setattr(config, "MAX_TOKENS_PER_ACCOUNT", 2)
    make_token(bob, "a"), make_token(bob, "b")
    assert "up to 2 tokens" in bob.post("/api/tokens", json={"name": "c"}).json()["detail"]


def test_removing_a_token_revokes_it_at_once(person, people):
    bob = person("bob@example.com")
    made = make_token(bob)
    ext = pusher(made["token"])
    assert ext.get("/api/push/ping").json() == {"ok": True, "email": "bob@example.com"}
    assert bob.delete(f"/api/tokens/{made['id']}").status_code == 200
    assert ext.get("/api/push/ping").status_code == 401


def test_a_token_cannot_be_made_or_listed_without_signing_in(person, people):
    assert pusher().post("/api/tokens", json={"name": "x"}).status_code in (302, 401)
    assert pusher().get("/api/tokens").status_code in (302, 401)


# --- who the push door lets in --------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("header", [None, "", "Bearer", "Bearer nope", "Bearer rjt_" + "x" * 43, "Basic abc", "rjt_abc"])
def test_no_token_or_a_wrong_one_gets_nothing(person, people, header):
    c = pusher()
    if header is not None:
        c.headers["Authorization"] = header
    for call in (lambda: c.get("/api/push/ping"), lambda: c.post("/api/push/jobs", json={"jobs": [posting()]})):
        assert call().status_code == 401
    with owned.unscoped(), SessionLocal() as db:
        assert db.query(models.Job).count() == 0


def test_a_removed_persons_token_stops_working_and_restoring_them_brings_it_back(person, people, make_user):
    bob = person("bob@example.com")
    token = make_token(bob)["token"]
    with owned.unscoped(), SessionLocal() as db:
        user = db.query(models.User).filter_by(email="bob@example.com").one()
        user.status = models.USER_REMOVED
        db.commit()
    assert pusher(token).get("/api/push/ping").status_code == 401
    with owned.unscoped(), SessionLocal() as db:
        db.query(models.User).filter_by(email="bob@example.com").one().status = models.USER_ACTIVE
        db.commit()
    assert pusher(token).get("/api/push/ping").status_code == 200


def test_a_token_opens_only_the_push_routes(person, people):
    bob = person("bob@example.com")
    ext = pusher(make_token(bob)["token"])
    for method, path in (("GET", "/api/jobs"), ("GET", "/api/people"), ("GET", "/api/tokens"), ("POST", "/api/jobs"), ("GET", "/api/me"), ("GET", "/jobs.html")):
        assert ext.request(method, path).status_code in (302, 401, 403), (method, path)


def test_the_extension_is_not_stopped_by_the_cross_site_check_but_a_page_still_is(person, people):
    bob = person("bob@example.com")
    ext = pusher(make_token(bob)["token"])
    from_extension = {"Origin": "chrome-extension://abcdefghijklmnop", "Sec-Fetch-Site": "cross-site"}
    assert ext.post("/api/push/jobs", json={"jobs": [posting()], "score": False}, headers=from_extension).status_code == 200
    forged = bob.post("/api/jobs", json={"name": "X"}, headers={"Origin": "https://evil.example.com", "Sec-Fetch-Site": "cross-site"})
    assert forged.status_code == 403  # the ordinary routes keep their protection


def test_the_preflight_is_answered_without_a_token_and_allows_the_authorization_header(person, people):
    resp = pusher().options("/api/push/jobs", headers={"Origin": "chrome-extension://abc", "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "authorization,content-type"})
    assert resp.status_code == 204 and resp.headers["access-control-allow-origin"] == "*"
    assert "authorization" in resp.headers["access-control-allow-headers"]
    assert "access-control-allow-credentials" not in resp.headers  # never combined with cookies
    ok = pusher(make_token(person("bob@example.com"))["token"]).get("/api/push/ping")
    assert ok.headers["access-control-allow-origin"] == "*" and ok.headers["cache-control"] == "no-store"
    assert pusher().get("/api/push/ping").headers["access-control-allow-origin"] == "*"  # even the refusal, so the extension can read why


def test_the_push_door_exists_only_on_the_admin_hostnames(person, people, monkeypatch):
    monkeypatch.setattr(config, "ADMIN_HOSTS", {"resume-admin.example.org"})
    token = make_token(person("bob@example.com"))["token"]
    head = {"Authorization": f"Bearer {token}"}
    on_admin = TestClient(server.app, base_url=HOST).get("/api/push/ping", headers=head)
    assert on_admin.status_code == 200
    on_public = TestClient(server.app, base_url="https://resume.example.org").get("/api/push/ping", headers=head)
    assert on_public.status_code == 404


# --- pushing postings -----------------------------------------------------------------------------------------------------------------


def push(token, jobs, **extra):
    return pusher(token).post("/api/push/jobs", json={"jobs": jobs, **extra})


def test_pushed_jobs_land_in_the_token_owners_account_and_nowhere_else(person, people, monkeypatch):
    bob, carol = person("bob@example.com"), person("carol@example.com")
    resp = push(make_token(bob)["token"], [posting(1), posting(2)], score=False)
    assert resp.status_code == 200 and resp.json()["created"] == 2
    assert sorted(j["name"] for j in bob.get("/api/jobs").json()) == ["Engineer 1", "Engineer 2"]
    assert carol.get("/api/jobs").json() == []
    assert bob.get("/api/jobs").json()[0]["has_description"] is True and bob.get("/api/jobs").json()[0]["tier"] is None


def test_arrivals_with_a_description_are_scored_in_the_background_on_their_key(person, people, monkeypatch):
    bob, fake = ready(person, monkeypatch, [json.dumps(SCORE)] * 3)
    resp = push(make_token(bob)["token"], [posting(1), posting(2), posting(3, jd_text="")])
    body = resp.json()
    assert body["created"] == 3 and body["queued"] == 2 and body["scoring_blocked"] is None  # the one with no description has nothing to read
    done = wait_for_queue(people["bob"])
    assert done["state"] == "done" and done["done"] == 2 and done["failed"] == 0
    got = {j["name"]: j["tier"] for j in bob.get("/api/jobs").json()}
    assert got == {"Engineer 1": "Good", "Engineer 2": "Good", "Engineer 3": None}
    assert set(fake.keys) == {KEY_A}
    assert bob.get("/api/jobs/score-queue/status").json()["state"] == "done"


def test_with_no_key_or_no_resume_they_are_saved_and_it_says_why_scoring_did_not_start(person, people, monkeypatch):
    nokey, _ = ready(person, monkeypatch, [], with_key=False)
    body = push(make_token(nokey)["token"], [posting(1)]).json()
    assert body["created"] == 1 and body["queued"] == 0 and "Anthropic key" in body["scoring_blocked"]
    nores, _ = ready(person, monkeypatch, [], email="carol@example.com", with_resume=False)
    body = push(make_token(nores)["token"], [posting(1)]).json()
    assert body["queued"] == 0 and "Save your resume" in body["scoring_blocked"]
    assert scoring.status(people["bob"])["state"] == "idle" and scoring.status(people["carol"])["state"] == "idle"


def test_score_false_only_saves(person, people, monkeypatch):
    bob, fake = ready(person, monkeypatch, [])
    body = push(make_token(bob)["token"], [posting(1)], score=False).json()
    assert body["created"] == 1 and body["queued"] == 0 and fake.calls == []


def test_pushing_the_same_posting_again_never_wipes_a_score_a_note_or_an_applied_mark(person, people, monkeypatch):
    bob, _ = ready(person, monkeypatch, [json.dumps(SCORE)])
    token = make_token(bob)["token"]
    push(token, [posting(1)], score=False)
    job = bob.get("/api/jobs").json()[0]
    bob.patch(f"/api/jobs/{job['id']}", json={"notes": "call Sam", "applied": True})
    assert bob.post(f"/api/jobs/{job['id']}/score").status_code == 200
    again = push(token, [posting(1, title="Renamed", jd_text="something else entirely", salary="$1")]).json()
    assert again["created"] == 0 and again["updated"] == 1 and again["queued"] == 0
    kept = bob.get(f"/api/jobs/{job['id']}").json()
    assert kept["tier"] == "Good" and kept["notes"] == "call Sam" and kept["applied"] is True
    assert kept["name"] == "Engineer 1" and kept["jd_text"] == JD and kept["company"] == "Acme"  # what it already had is not replaced
    push(token, [posting(1, company="Other")], score=False)
    assert bob.get(f"/api/jobs/{job['id']}").json()["company"] == "Acme"


def test_a_repeat_fills_in_what_was_missing_and_then_scores_it(person, people, monkeypatch):
    bob, _ = ready(person, monkeypatch, [json.dumps(SCORE)])
    token = make_token(bob)["token"]
    push(token, [posting(1, jd_text=None)])  # captured before the page had loaded its text
    assert bob.get("/api/jobs").json()[0]["has_description"] is False
    body = push(token, [posting(1, salary="$150k")]).json()
    assert body["updated"] == 1 and body["queued"] == 1
    wait_for_queue(people["bob"])
    job = bob.get("/api/jobs").json()[0]
    assert job["has_description"] and job["tier"] == "Good"


def test_a_posting_that_comes_with_its_own_score_keeps_it_and_is_not_scored_again(person, people, monkeypatch):
    bob, fake = ready(person, monkeypatch, [])
    body = push(make_token(bob)["token"], [posting(1, tier="Strong", summary="Great fit", matched="Python - evidence", gaps="Go", strengths="Teaching")]).json()
    assert body["queued"] == 0 and fake.calls == []
    job = bob.get("/api/jobs").json()[0]
    assert (job["tier"], job["summary"], job["matched"], job["gaps"], job["strengths"]) == ("Strong", "Great fit", "Python - evidence", "Go", "Teaching") and job["scored_at"]


def test_postings_without_a_link_or_a_title_are_skipped_and_a_resume_must_be_theirs(person, people, monkeypatch):
    bob, carol = person("bob@example.com"), person("carol@example.com")
    token = make_token(bob)["token"]
    body = push(token, [{"url": "", "title": "No link"}, {"url": "https://x.example.com/a"}, posting(1)], score=False).json()
    assert body["created"] == 1 and body["skipped"] == 2
    carols = bobs_resume(carol)
    assert push(token, [posting(2)], person_id=carols["id"]).status_code == 400


def test_the_size_of_a_push_is_limited(person, people, monkeypatch):
    token = make_token(person("bob@example.com"))["token"]
    monkeypatch.setattr(config, "MAX_PUSH_ITEMS", 2)
    assert push(token, [posting(i) for i in range(3)], score=False).status_code == 400
    monkeypatch.setattr(config, "MAX_PUSH_BYTES", 100)
    assert push(token, [posting(1)], score=False).status_code == 413


def test_a_push_that_does_not_say_how_big_it_is_is_refused(person, people):
    token = make_token(person("bob@example.com"))["token"]
    chunks = (part for part in (b'{"jobs": [', b'], "score": false}'))  # no Content-Length: sent in pieces
    assert pusher(token).post("/api/push/jobs", content=chunks, headers={"Content-Type": "application/json"}).status_code == 411


def test_the_job_limit_still_applies(person, people, monkeypatch):
    monkeypatch.setattr(config, "MAX_JOBS_PER_ACCOUNT", 2)
    body = push(make_token(person("bob@example.com"))["token"], [posting(i) for i in range(4)], score=False).json()
    assert body["created"] == 2 and body["skipped"] == 2


# --- the background queue -------------------------------------------------------------------------------------------------------------


def seed(bob, count, **extra):
    for n in range(count):
        assert bob.post("/api/jobs", json={"name": f"Role {n}", "company": "Acme", "jd_text": JD, **extra}).status_code == 200


def test_the_page_starts_a_queue_for_every_unscored_job_and_asks_how_it_is_going(person, people, monkeypatch):
    bob, fake = ready(person, monkeypatch, [json.dumps(SCORE)] * 3)
    seed(bob, 3)
    bob.post("/api/jobs", json={"name": "No description"})
    started = bob.post("/api/jobs/score-queue")
    assert started.status_code == 200 and started.json()["total"] == 3
    done = wait_for_queue(people["bob"])
    assert done == {"state": "done", "total": 3, "done": 3, "failed": 0, "remaining": 0, "error": None, "first_failure": None}
    assert len(fake.calls) == 3
    assert bob.post("/api/jobs/score-queue").status_code == 400  # nothing left to score


def test_a_job_scored_by_hand_or_deleted_while_waiting_is_skipped(person, people, monkeypatch):
    bob, fake = ready(person, monkeypatch, [json.dumps(SCORE)])
    seed(bob, 2)
    ids = [j["id"] for j in bob.get("/api/jobs").json()]
    with owned.scope(people["bob"]), SessionLocal() as db:
        job = crud.get_job(db, ids[0])
        job.tier = "Strong"
        crud.delete_job(db, crud.get_job(db, ids[1]))
    scoring.enqueue(__import__("shared_python.accounts_gate", fromlist=["Account"]).Account(id=people["bob"], email="bob@example.com"), ids)
    done = wait_for_queue(people["bob"])
    assert done["state"] == "done" and done["done"] == 0 and done["failed"] == 0 and fake.calls == []


def test_one_jobs_unreadable_answer_is_counted_and_the_queue_goes_on(person, people, monkeypatch):
    bob, fake = ready(person, monkeypatch, ["not json at all", json.dumps(SCORE)])
    seed(bob, 2)
    bob.post("/api/jobs/score-queue")
    done = wait_for_queue(people["bob"])
    assert done["state"] == "done" and done["done"] == 1 and done["failed"] == 1 and done["first_failure"]
    assert sorted(j["tier"] or "-" for j in bob.get("/api/jobs").json()) == ["-", "Good"]


def test_a_cut_off_answer_leaves_the_job_untouched_and_is_counted_as_failed(person, people, monkeypatch):
    bob, _ = ready(person, monkeypatch, [Cut("{\"tier\": \"Go")])
    seed(bob, 1)
    bob.post("/api/jobs/score-queue")
    done = wait_for_queue(people["bob"])
    assert done["failed"] == 1 and done["done"] == 0 and bob.get("/api/jobs").json()[0]["tier"] is None


def test_the_hourly_allowance_ends_the_queue_with_its_words_and_leaves_the_rest_unscored(person, people, monkeypatch):
    monkeypatch.setattr(config, "SCORES_PER_HOUR", 2)
    bob, fake = ready(person, monkeypatch, [json.dumps(SCORE)] * 5)
    seed(bob, 5)
    bob.post("/api/jobs/score-queue")
    done = wait_for_queue(people["bob"])
    assert done["state"] == "error" and done["done"] == 2 and done["remaining"] == 0 and "2 times an hour" in done["error"]
    assert len(fake.calls) == 2 and sum(1 for j in bob.get("/api/jobs").json() if not j["tier"]) == 3


def test_no_key_ends_the_queue_at_once_without_spending_anything(person, people, monkeypatch):
    bob, fake = ready(person, monkeypatch, [], with_key=False)
    seed(bob, 3)
    bob.post("/api/jobs/score-queue")
    done = wait_for_queue(people["bob"])
    assert done["state"] == "error" and "Anthropic key" in done["error"] and done["failed"] == 0 and fake.calls == []


def test_a_person_allowed_the_system_key_has_each_queued_score_counted(person, people, monkeypatch):
    bob, fake = ready(person, monkeypatch, [json.dumps(SCORE)] * 2, with_key=False)
    with owned.unscoped(), SessionLocal() as db:
        crud.set_house_key(db, db.query(models.User).filter_by(email="bob@example.com").one(), True)
    seed(bob, 2)
    bob.post("/api/jobs/score-queue")
    assert wait_for_queue(people["bob"])["done"] == 2
    with owned.unscoped(), SessionLocal() as db:
        uses = db.query(models.HouseKeyUse).filter_by(user_id=people["bob"]).all()
        assert [u.kind for u in uses] == ["score", "score"] and set(fake.keys) == {SERVER_KEY}


def test_stopping_ends_the_queue_after_the_job_it_is_on(person, people, monkeypatch):
    bob, fake = ready(person, monkeypatch, [json.dumps(SCORE)] * 3)
    seed(bob, 3)
    gate = __import__("threading").Event()
    real = scoring.score_one

    def slow(db, account, job):
        gate.wait(5)
        return real(db, account, job)

    monkeypatch.setattr(scoring, "score_one", slow)
    bob.post("/api/jobs/score-queue")
    assert bob.delete("/api/jobs/score-queue").json()["state"] == "running"  # it finishes the job in hand first
    gate.set()
    done = wait_for_queue(people["bob"])
    assert done["state"] == "stopped" and done["done"] == 1 and done["remaining"] == 0


def test_jobs_pushed_while_it_runs_join_the_same_queue_and_are_not_added_twice(person, people, monkeypatch):
    bob, fake = ready(person, monkeypatch, [json.dumps(SCORE)] * 3)
    token = make_token(bob)["token"]
    gate = __import__("threading").Event()
    real = scoring.score_one

    def slow(db, account, job):
        gate.wait(5)
        return real(db, account, job)

    monkeypatch.setattr(scoring, "score_one", slow)
    assert push(token, [posting(1), posting(2)]).json()["queued"] == 2
    again = push(token, [posting(2), posting(3)]).json()
    assert again["queued"] == 1  # 2 was already waiting
    gate.set()
    done = wait_for_queue(people["bob"])
    assert done["total"] == 3 and done["done"] == 3 and len(fake.calls) == 3


def test_each_persons_queue_is_their_own(person, people, monkeypatch):
    bob, _ = ready(person, monkeypatch, [json.dumps(SCORE)])
    carol = person("carol@example.com")
    seed(bob, 1)
    bob.post("/api/jobs/score-queue")
    wait_for_queue(people["bob"])
    assert carol.get("/api/jobs/score-queue/status").json() == {"state": "idle"}
    assert carol.post("/api/jobs/score-queue").status_code == 400


def test_a_finished_queue_can_be_cleared_once_the_page_has_shown_it(person, people, monkeypatch):
    bob, _ = ready(person, monkeypatch, [json.dumps(SCORE)])
    seed(bob, 1)
    bob.post("/api/jobs/score-queue")
    wait_for_queue(people["bob"])
    assert bob.delete("/api/jobs/score-queue?clear=true").json() == {"state": "idle"}


def test_removing_a_persons_data_removes_their_tokens(person, people):
    bob = person("bob@example.com")
    token = make_token(bob)["token"]
    owner = person(ADMIN)
    bob_id_ = people["bob"]
    assert owner.patch(f"/api/users/{bob_id_}", json={"status": "removed"}).status_code == 200
    assert owner.delete(f"/api/users/{bob_id_}/data").status_code == 200
    with owned.unscoped(), SessionLocal() as db:
        assert db.query(models.PushToken).count() == 0
    assert pusher(token).get("/api/push/ping").status_code == 401
