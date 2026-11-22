"""Send feedback now goes to the dashboard's one inbox (shared_python.feedback_relay); the Jobs app keeps no inbox of its own. These cover what is left
here: the old page's redirect, the shared form posting through this app's gate, and the old messages still going with a person's data."""
import pytest

from app import crud, models
from app.database import SessionLocal
from tests.test_accounts import ADMIN, key, people, person, production  # noqa: F401 - fixtures


def test_the_old_page_now_opens_the_shared_form_on_the_front_page_and_still_needs_an_account(person, people):
    resp = person("bob@example.com").get("/contact.html", follow_redirects=False)
    assert resp.status_code == 302 and resp.headers["location"] == "/?feedback=1"
    assert person("dana@example.com").get("/contact.html", follow_redirects=False).status_code == 403


def test_the_shared_send_feedback_form_posts_here_and_is_passed_to_the_dashboard_as_jobs(person, people, monkeypatch):
    import requests
    from shared_python import settings

    calls = []

    class Reply:
        status_code = 200

        def json(self):
            return {"id": 1}

    monkeypatch.setattr(settings, "DASHBOARD_API_BASE", "http://127.0.0.1:8090")
    monkeypatch.setattr(requests, "post", lambda url, json=None, headers=None, timeout=None, allow_redirects=True: calls.append((url, json)) or Reply())
    assert person("bob@example.com").post("/api/feedback", json={"kind": "idea", "body": "Please add more"}).status_code == 200
    sent = [c for c in calls if c[0].endswith("/internal/feedback")]
    assert sent == [("http://127.0.0.1:8090/internal/feedback", {"app": "resume", "kind": "idea", "body": "Please add more", "page": None})]
    assert person("dana@example.com").post("/api/feedback", json={"kind": "idea", "body": "Please add more"}).status_code == 403  # no account here
    assert len([c for c in calls if c[0].endswith("/internal/feedback")]) == 1


def test_the_old_inbox_is_gone(person, people):
    owner = person(ADMIN)
    for method, path in (("GET", "/api/admin/messages"), ("GET", "/api/contact"), ("POST", "/api/contact")):
        assert owner.request(method, path).status_code in (404, 405), (method, path)
    assert "new_messages" not in owner.get("/api/me").json()


def test_deleting_a_persons_data_still_deletes_their_old_messages_and_nobody_elses(person, people):
    with SessionLocal() as db:
        for who, body in (("bob", "bob's"), ("carol", "carol's")):
            db.add(models.Message(user_id=people[who], kind="idea", body=body))
        db.commit()
        crud.delete_user_data(db, people["bob"])
        assert [m.body for m in db.query(models.Message).all()] == ["carol's"]
