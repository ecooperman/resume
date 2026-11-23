"""Making a resume and cover letter for one job: one AI run on the person's key (or the system key if allowed, counted), done in the background
so the request returns at once, saved on the job only if the whole thing worked, contact details and the 'left off the resume' flag put back
whatever the model did, downloads that render on the spot, and nobody else's job is ever reachable."""
import json
import threading
import time

import pytest
import yaml

from app import config, generate, models, render
from app.database import SessionLocal
from tests.test_accounts import ADMIN, key, people, person, production  # noqa: F401 - fixtures
from tests.test_builder import KEY_A, Cut, Fake, accepts, key_storage  # noqa: F401 - key_storage is an autouse fixture
from tests.test_job_scoring import JD, SERVER_KEY, server_key  # noqa: F401 - server_key is an autouse fixture
from tests.dashboard_keys import save_key  # noqa: E402

RESUME = """basics:
  name: Bob Builder
  title: Engineer
  email: bob@example.com
  phone: '555-0100'
  summary: Builds things.
experience:
  - company: Acme
    title: Dev
    start: 2020
    end: Present
    bullets: [Built a thing, Fixed a thing]
    engagements:
      - client: Secret Bank
        confidential: true
  - company: Side Gig
    title: Contractor
    start: 2018
    end: 2019
    include_in_resume: false
    bullets: [Did side work]
"""

LETTER = "Your Staff Engineer role caught my eye.\n\nI have built things at Acme.\n\nSincerely,\nBob Builder"


def tailored(**changes):
    """What a well-behaved model returns: the resume (reworded) and a letter."""
    data = yaml.safe_load(RESUME)
    data["basics"]["summary"] = "Builds things for staff-level roles."
    data["experience"][0]["bullets"] = ["Fixed a thing", "Built a thing"]
    data.update(changes)
    return json.dumps({"resume": data, "cover_letter": LETTER})


def ready(person, monkeypatch, answers, email="bob@example.com", with_key=True, with_resume=True):
    accepts(monkeypatch)
    fake = Fake(monkeypatch, answers)
    who = person(email)
    if with_key:
        save_key(who, KEY_A)
    if with_resume:
        resp = who.post("/api/people", json={"name": "Bob", "resume_yaml": RESUME})
        assert resp.status_code == 200, resp.text
    return who, fake


def job(who, **extra):
    resp = who.post("/api/jobs", json={"name": "Staff Engineer", "company": "Acme", "jd_text": JD, **extra})
    assert resp.status_code == 200, resp.text
    return resp.json()


def finished(who, job_id, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        state = who.get(f"/api/jobs/{job_id}/tailor").json()
        if state["state"] != "running":
            return state
        time.sleep(0.01)
    raise AssertionError("still running")


def test_it_returns_at_once_then_saves_the_resume_and_letter_on_the_job(person, people, monkeypatch):
    bob, fake = ready(person, monkeypatch, [tailored()])
    j = job(bob)
    started = bob.post(f"/api/jobs/{j['id']}/tailor")
    assert started.status_code == 200 and started.json()["state"] in ("running", "done")
    assert finished(bob, j["id"])["state"] == "done"
    got = bob.get(f"/api/jobs/{j['id']}").json()
    assert got["has_documents"] and got["resume_generated_at"] and got["generated_cover_letter"] == LETTER
    saved = yaml.safe_load(got["generated_resume_yaml"])
    assert saved["basics"]["summary"] == "Builds things for staff-level roles." and saved["experience"][0]["bullets"][0] == "Fixed a thing"
    sent = fake.calls[0]["messages"][0]["content"]
    assert fake.keys[-1] == KEY_A and "Staff Engineer" in sent and "Kubernetes" in sent and "Bob Builder" in sent  # the job, the posting and their resume
    assert "never instructions" in fake.calls[0]["system"]
    assert bob.get("/api/jobs").json()[0]["has_documents"] is True
    assert bob.delete(f"/api/jobs/{j['id']}/tailor").json() == {"state": "idle"}


def test_contact_details_and_the_off_the_resume_flag_are_put_back_whatever_the_model_returned(person, people, monkeypatch):
    data = yaml.safe_load(RESUME)
    data["basics"].update(email="hacked@evil.example", phone="000", summary="New summary.")
    data["experience"] = [{**data["experience"][0], "engagements": [{"client": "Changed"}], "bullets": ["Reworded"]}]  # the side gig was dropped
    bob, _ = ready(person, monkeypatch, [json.dumps({"resume": data, "cover_letter": LETTER})])
    j = job(bob)
    bob.post(f"/api/jobs/{j['id']}/tailor")
    assert finished(bob, j["id"])["state"] == "done"
    saved = yaml.safe_load(bob.get(f"/api/jobs/{j['id']}").json()["generated_resume_yaml"])
    assert saved["basics"]["email"] == "bob@example.com" and saved["basics"]["phone"] == "555-0100"  # contact details are never the model's to change
    assert saved["basics"]["summary"] == "New summary."  # the summary alone may be reworded
    assert saved["experience"][0]["engagements"] == [{"client": "Secret Bank", "confidential": True}] and saved["experience"][0]["bullets"] == ["Reworded"]
    side = [e for e in saved["experience"] if e["company"] == "Side Gig"]
    assert len(side) == 1 and side[0]["include_in_resume"] is False  # a job kept off the printed resume stays off even if dropped


def test_a_fenced_answer_works_and_a_bad_one_gets_one_repair_attempt(person, people, monkeypatch):
    bob, fake = ready(person, monkeypatch, ["Sure! Here you go.", "```json\n" + tailored() + "\n```"])
    j = job(bob)
    bob.post(f"/api/jobs/{j['id']}/tailor")
    assert finished(bob, j["id"])["state"] == "done" and len(fake.calls) == 2
    assert "wasn't usable" in fake.calls[1]["messages"][-1]["content"]


@pytest.mark.parametrize("answer", [json.dumps({"resume": {"basics": {}}, "cover_letter": ""}), json.dumps({"cover_letter": "x"}), "[1]"])
def test_an_answer_that_never_becomes_usable_leaves_the_job_alone_and_says_so(person, people, monkeypatch, answer):
    bob, _ = ready(person, monkeypatch, [answer, answer])
    j = job(bob)
    bob.post(f"/api/jobs/{j['id']}/tailor")
    state = finished(bob, j["id"])
    assert state["state"] == "error" and "wasn't usable" in state["error"]
    assert bob.get(f"/api/jobs/{j['id']}").json()["has_documents"] is False


def test_a_failed_attempt_never_overwrites_the_documents_made_before(person, people, monkeypatch):
    bob, _ = ready(person, monkeypatch, [tailored(), Cut(tailored())])
    j = job(bob)
    bob.post(f"/api/jobs/{j['id']}/tailor")
    assert finished(bob, j["id"])["state"] == "done"
    before = bob.get(f"/api/jobs/{j['id']}").json()
    bob.delete(f"/api/jobs/{j['id']}/tailor")
    bob.post(f"/api/jobs/{j['id']}/tailor")
    state = finished(bob, j["id"])
    assert state["state"] == "error" and "cut off" in state["error"]
    after = bob.get(f"/api/jobs/{j['id']}").json()
    assert after["generated_resume_yaml"] == before["generated_resume_yaml"] and after["resume_generated_at"] == before["resume_generated_at"]


def test_it_needs_a_description_a_saved_resume_and_a_key_and_none_of_the_refusals_reach_the_ai(person, people, make_user, monkeypatch):
    make_user("dave@example.com")
    bob, fake = ready(person, monkeypatch, [tailored()])
    nodesc = bob.post("/api/jobs", json={"name": "No text"}).json()
    r = bob.post(f"/api/jobs/{nodesc['id']}/tailor")
    assert r.status_code == 400 and "description" in r.json()["detail"]
    carol, _ = ready(person, monkeypatch, [tailored()], email="carol@example.com", with_resume=False)
    r = carol.post(f"/api/jobs/{job(carol)['id']}/tailor")
    assert r.status_code == 400 and "Save your resume first" in r.json()["detail"]
    dave, _ = ready(person, monkeypatch, [tailored()], email="dave@example.com", with_key=False)
    r = dave.post(f"/api/jobs/{job(dave)['id']}/tailor")
    assert r.status_code == 400 and "your own Anthropic key" in r.json()["detail"]
    assert fake.calls == []  # nothing above reached the AI


def test_only_one_run_at_a_time_for_a_person(person, people, monkeypatch):
    go = threading.Event()
    bob, _ = ready(person, monkeypatch, [])

    class Slow:
        def __init__(self, api_key=None, **kw):
            self.messages = self

        def create(self, **kw):
            go.wait(10)
            return type("R", (), {"content": [type("B", (), {"type": "text", "text": tailored()})()], "stop_reason": "end_turn"})()

    monkeypatch.setattr(generate.anthropic, "Anthropic", Slow)
    a, b = job(bob), job(bob, url="https://example.com/2")
    assert bob.post(f"/api/jobs/{a['id']}/tailor").json()["state"] == "running"
    assert bob.get(f"/api/jobs/{a['id']}/tailor").json()["state"] == "running"
    r = bob.post(f"/api/jobs/{b['id']}/tailor")
    assert r.status_code == 429 and "already running" in r.json()["detail"]
    go.set()
    assert finished(bob, a["id"])["state"] == "done"


def test_a_run_on_the_system_key_is_counted(person, people, monkeypatch):
    monkeypatch.setattr(config, "HOUSE_KEY_CALLS_PER_MONTH", 0)
    bob, fake = ready(person, monkeypatch, [tailored()], with_key=False)
    with SessionLocal() as db:
        user = db.query(models.User).filter_by(email="bob@example.com").one()
        user.house_key = True
        db.commit()
        uid = user.id
    j = job(bob)
    bob.post(f"/api/jobs/{j['id']}/tailor")
    assert finished(bob, j["id"])["state"] == "done" and fake.keys[-1] == SERVER_KEY
    with SessionLocal() as db:
        uses = db.query(models.HouseKeyUse).filter_by(user_id=uid).all()
        assert [u.kind for u in uses] == ["tailor"] and uses[0].input_tokens == 1000


def test_the_files_download_as_pdf_or_word_and_say_what_they_are_for(person, people, monkeypatch):
    bob, _ = ready(person, monkeypatch, [tailored()])
    j = job(bob)
    assert bob.get(f"/api/jobs/{j['id']}/resume").status_code == 404  # nothing made yet
    assert bob.get(f"/api/jobs/{j['id']}/cover-letter").status_code == 404
    bob.post(f"/api/jobs/{j['id']}/tailor")
    finished(bob, j["id"])
    pdf = bob.get(f"/api/jobs/{j['id']}/resume?format=pdf")
    assert pdf.status_code == 200 and pdf.content.startswith(b"PDF Bob Builder") and pdf.headers["content-type"] == "application/pdf"
    assert 'filename="Staff Engineer - Acme - Resume.pdf"' in pdf.headers["content-disposition"]
    assert bob.get(f"/api/jobs/{j['id']}/resume?format=docx").content.startswith(b"DOCX Bob Builder")
    assert bob.get(f"/api/jobs/{j['id']}/resume?format=exe").status_code == 422

    seen = {}

    def letter_pdf(basics, text, out_dir):
        seen.update(basics=basics, text=text)
        (out_dir / "cover_letter.pdf").write_bytes(b"LETTER PDF")

    monkeypatch.setattr(render, "build_cover_letter_pdf", letter_pdf)
    letter = bob.get(f"/api/jobs/{j['id']}/cover-letter?format=pdf")
    assert letter.content == b"LETTER PDF" and seen["text"] == LETTER and seen["basics"]["name"] == "Bob Builder"  # the letterhead is the resume's contact details
    assert "Cover Letter.pdf" in letter.headers["content-disposition"]


def test_someone_elses_job_is_a_404_for_every_tailoring_route_and_the_ai_is_never_called(person, people, monkeypatch):
    bob, fake = ready(person, monkeypatch, [tailored()])
    j = job(bob)
    bob.post(f"/api/jobs/{j['id']}/tailor")
    finished(bob, j["id"])
    calls = len(fake.calls)
    carol, _ = ready(person, monkeypatch, [tailored()], email="carol@example.com")
    owner = person(ADMIN)
    for who in (carol, owner):
        for method, path in [("post", "tailor"), ("get", "tailor"), ("delete", "tailor"), ("get", "resume"), ("get", "cover-letter")]:
            resp = getattr(who, method)(f"/api/jobs/{j['id']}/{path}")
            assert resp.status_code == 404, (method, path, resp.status_code)
            assert "Bob" not in resp.text and "Builder" not in resp.text
    assert len(fake.calls) == calls
