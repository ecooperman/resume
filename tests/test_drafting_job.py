"""Drafting runs in the background (app/drafts.py): starting returns at once, the page asks how it is going, the finished text waits to be
collected, and nobody can see or disturb anyone else's job. Also: an answer that was cut off for lack of room is never used."""
import threading
import time

from app import drafts, generate, limits
from tests.test_accounts import ADMIN, key, people, person, production  # noqa: F401 - fixtures
from tests.test_builder import KEY_A, KEY_B, Cut, Fake, GOOD_YAML, accepts, key_storage, run_draft  # noqa: F401 - key_storage is an autouse fixture
from tests.dashboard_keys import save_key  # noqa: E402


class Gate:
    """Stands in for Claude but waits for the test to say go, so a draft can be seen while it is still running."""

    def __init__(self, monkeypatch, text=GOOD_YAML):
        self.go, self.calls = threading.Event(), []
        outer = self

        class Client:
            def __init__(self, api_key=None, **kw):
                self.messages = self

            def create(self, **kw):
                outer.calls.append(kw)
                outer.go.wait(10)
                return type("R", (), {"content": [type("B", (), {"type": "text", "text": text})()], "stop_reason": "end_turn"})()

        monkeypatch.setattr(generate.anthropic, "Anthropic", Client)


def ready(person, monkeypatch, email="bob@example.com", key_value=KEY_A):
    accepts(monkeypatch)
    who = person(email)
    save_key(who, key_value)
    who.post("/api/engagements", json={"client": "Acme"})
    return who


def wait_for(who, states, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        job = who.get("/api/generate-resume/status").json()
        if job["state"] in states:
            return job
        time.sleep(0.01)
    raise AssertionError(f"never reached {states}: {job}")


def test_starting_a_draft_answers_at_once_and_the_result_is_collected_from_the_status(person, people, monkeypatch):
    gate = Gate(monkeypatch)
    bob = ready(person, monkeypatch)
    started = bob.post("/api/generate-resume")
    assert started.status_code == 200 and started.json()["state"] == "running" and "resume_yaml" not in started.json()  # the answer did not wait for Claude
    assert bob.get("/api/generate-resume/status").json()["state"] == "running"
    gate.go.set()
    done = wait_for(bob, {"done"})
    assert done["resume_yaml"] == GOOD_YAML.strip() or done["resume_yaml"] == GOOD_YAML
    assert bob.get("/api/builder/draft").json()["draft"]["resume_yaml"].strip() == GOOD_YAML.strip()  # also kept as their draft, so a reload finds it
    assert bob.delete("/api/generate-resume/status").json() == {"state": "idle"}
    assert bob.get("/api/generate-resume/status").json() == {"state": "idle"}


def test_only_one_draft_at_a_time_and_the_slot_is_given_back_when_it_ends(person, people, monkeypatch):
    gate = Gate(monkeypatch)
    bob = ready(person, monkeypatch)
    assert bob.post("/api/generate-resume").status_code == 200
    again = bob.post("/api/generate-resume")
    assert again.status_code == 429 and "already running" in again.json()["detail"] and len(gate.calls) <= 1
    gate.go.set()
    wait_for(bob, {"done"})
    assert bob.post("/api/generate-resume").status_code == 200  # free again
    wait_for(bob, {"done"})


def test_a_running_draft_is_not_cleared_by_the_page_and_a_finished_one_is_not_lost_by_asking(person, people, monkeypatch):
    gate = Gate(monkeypatch)
    bob = ready(person, monkeypatch)
    bob.post("/api/generate-resume")
    assert bob.delete("/api/generate-resume/status").json()["state"] == "running"  # clearing only collects what has ended
    gate.go.set()
    wait_for(bob, {"done"})
    assert bob.get("/api/generate-resume/status").json()["state"] == "done"  # asking twice (a second tab, a reload) finds it both times
    assert bob.get("/api/generate-resume/status").json()["state"] == "done"


def test_nobody_sees_or_clears_anyone_elses_draft(person, people, monkeypatch):
    gate = Gate(monkeypatch)
    bob = ready(person, monkeypatch)
    carol = person("carol@example.com")
    bob.post("/api/generate-resume")
    gate.go.set()
    wait_for(bob, {"done"})
    assert carol.get("/api/generate-resume/status").json() == {"state": "idle"}
    carol.delete("/api/generate-resume/status")
    assert bob.get("/api/generate-resume/status").json()["state"] == "done"  # carol's clear did nothing to bob's
    assert "Built things" not in carol.get("/api/generate-resume/status").text


def test_someone_who_keeps_no_files_still_gets_the_draft_and_nothing_is_stored(person, people, monkeypatch):
    gate = Gate(monkeypatch)
    bob = ready(person, monkeypatch)
    bob.put("/api/builder/keep", json={"keep": False})
    bob.post("/api/generate-resume", files=[("sources", ("cv.txt", b"one two three"))])
    gate.go.set()
    assert "basics" in wait_for(bob, {"done"})["resume_yaml"]
    assert bob.get("/api/builder/draft").json() == {"draft": None}  # delivered to the page only, never kept on the server


def test_a_failed_draft_shows_its_plain_message_and_a_surprise_shows_none_of_its_details(person, people, monkeypatch):
    bob = ready(person, monkeypatch)
    Fake(monkeypatch, [generate.anthropic.RateLimitError.__new__(generate.anthropic.RateLimitError)])
    failed = run_draft(bob)
    assert failed.status_code == 400 and "rate or spending limit" in failed.json()["detail"]
    Fake(monkeypatch, [RuntimeError("SECRET-INTERNAL-DETAIL /opt/apps/resume/app/x.py")])
    surprise = run_draft(bob)
    assert surprise.status_code == 400 and "Something went wrong while drafting" in surprise.json()["detail"]
    assert "SECRET-INTERNAL-DETAIL" not in surprise.text and limits._running == set()  # the slot is given back even then


def test_an_answer_cut_off_for_lack_of_room_is_never_used(person, people, monkeypatch):
    bob = ready(person, monkeypatch)
    Fake(monkeypatch, [Cut(GOOD_YAML)])  # parses fine as YAML, which is exactly why it must be refused on the stop reason
    resp = run_draft(bob)
    assert resp.status_code == 400 and "cut off" in resp.json()["detail"]
    assert bob.get("/api/builder/draft").json() == {"draft": None}


def test_the_draft_request_leaves_room_for_thinking_and_asks_for_moderate_effort(person, people, monkeypatch):
    bob = ready(person, monkeypatch)
    fake = Fake(monkeypatch, [GOOD_YAML])
    run_draft(bob)
    call = fake.calls[0]
    assert call["max_tokens"] == generate.DRAFT_MAX_TOKENS == 20_000  # thinking counts against this, and 12,000 once ran out mid-resume
    assert call["output_config"] == {"effort": "medium"}


def test_a_finished_job_nobody_collects_is_forgotten_after_an_hour(person, people, monkeypatch):
    bob = ready(person, monkeypatch)
    Fake(monkeypatch, [GOOD_YAML])
    run_draft_no_clear = bob.post("/api/generate-resume")
    assert run_draft_no_clear.status_code == 200
    wait_for(bob, {"done"})
    account_id = next(iter(drafts._jobs))
    assert drafts.status(account_id)["state"] == "done"
    assert drafts.status(account_id, now=time.time() + drafts.KEEP_SECONDS + 5) == {"state": "idle"}


# --- the model and the draft limit are settings --------------------------------------------------------------------------------


def test_the_draft_limit_is_a_setting_with_sensible_bounds():
    from app import config

    assert config.bounded_int(None, 20_000, 4_000, 21_000) == 20_000  # not set
    assert config.bounded_int("18,000", 20_000, 4_000, 21_000) == 18_000 and config.bounded_int("18_000", 20_000, 4_000, 21_000) == 18_000
    assert config.bounded_int("lots", 20_000, 4_000, 21_000) == 20_000  # not a number: the default, never a crash at start-up
    assert config.bounded_int("100000", 20_000, 4_000, 21_000) == 21_000  # above the SDK's ceiling for a request that is not streamed
    assert config.bounded_int("10", 20_000, 4_000, 21_000) == 4_000


def test_the_defaults_are_todays_model_and_limit():
    from app import config, generate

    assert generate.MODEL == config.ANTHROPIC_MODEL == "claude-opus-5" and generate.DRAFT_MAX_TOKENS == 20_000
