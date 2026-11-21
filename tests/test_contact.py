"""Send feedback: anyone with an account sends the administrator a message; it is saved and the administrator told; a person sees only
their own, and only an administrator reads (and ticks off) everyone's."""
import pytest

from app import crud, models, notify
from app.database import SessionLocal
from tests.test_accounts import ADMIN, key, people, person, production  # noqa: F401 - fixtures


@pytest.fixture(autouse=True)
def told(production, monkeypatch):  # after `production`
    """Record who the administrator would have been told about (nothing may reach the network)."""
    sent = []
    monkeypatch.setattr(notify, "feedback_received", lambda frm, kind, body: sent.append((frm, kind, body)))
    return sent


def send(who, body="The Score button does nothing", kind="problem", page="/jobs.html"):
    return who.post("/api/contact", json={"kind": kind, "body": body, "page": page})


def test_a_message_is_saved_the_administrator_told_and_only_the_sender_and_the_administrator_can_read_it(person, people, told):
    bob, carol, owner = person("bob@example.com"), person("carol@example.com"), person(ADMIN)
    got = send(bob, "  The Score button does nothing  ")
    assert got.status_code == 200 and got.json()["body"] == "The Score button does nothing" and got.json()["page"] == "/jobs.html"
    assert told == [("bob@example.com", "Problem", "The Score button does nothing")]
    assert [m["body"] for m in bob.get("/api/contact").json()] == ["The Score button does nothing"]
    assert carol.get("/api/contact").json() == []  # not carol's business
    inbox = owner.get("/api/admin/messages").json()
    assert len(inbox) == 1 and inbox[0]["from_email"] == "bob@example.com" and inbox[0]["done_at"] is None


def test_a_message_that_is_empty_too_long_or_of_an_unknown_kind_is_refused_and_a_page_that_is_not_ours_is_dropped(person, people, told):
    bob = person("bob@example.com")
    assert send(bob, "  hi ").status_code == 400 and send(bob, "").status_code == 400
    assert send(bob, "x" * 4001).status_code == 400
    assert send(bob, kind="spam").status_code == 400
    assert send(bob, page="https://evil.example.com/x").json()["page"] is None
    assert send(bob, page="//evil.example.com/x").json()["page"] is None
    assert send(bob, kind="Idea", page=None).json()["kind"] == "idea"
    assert len(told) == 3  # the two with a bad page are still sent (the page is just dropped), and the idea


def test_a_person_can_send_only_ten_messages_a_day(person, people, told):
    bob, carol = person("bob@example.com"), person("carol@example.com")
    assert all(send(bob, f"message {i}").status_code == 200 for i in range(10))
    again = send(bob, "one more")
    assert again.status_code == 429 and "tomorrow" in again.json()["detail"]
    assert send(carol).status_code == 200  # the limit is each person's own
    assert len(told) == 11


def test_only_an_administrator_reads_marks_or_deletes_messages(person, people):
    bob, owner = person("bob@example.com"), person(ADMIN)
    mid = send(bob).json()["id"]
    for method, path in (("GET", "/api/admin/messages"), ("POST", f"/api/admin/messages/{mid}/done"),
                         ("POST", f"/api/admin/messages/{mid}/reopen"), ("DELETE", f"/api/admin/messages/{mid}")):
        assert bob.request(method, path).status_code == 403, (method, path)
    assert owner.get("/api/admin/messages").json()[0]["id"] == mid  # still there


def test_done_moves_a_message_below_the_waiting_ones_and_reopen_brings_it_back(person, people):
    bob, owner = person("bob@example.com"), person(ADMIN)
    first, second = send(bob, "first").json()["id"], send(bob, "second").json()["id"]
    assert [m["id"] for m in owner.get("/api/admin/messages").json()] == [second, first]
    assert owner.get("/api/me").json()["new_messages"] == 2 and "new_messages" not in bob.get("/api/me").json()
    assert owner.post(f"/api/admin/messages/{second}/done").json()["done_at"] is not None
    assert [m["id"] for m in owner.get("/api/admin/messages").json()] == [first, second]
    assert owner.get("/api/me").json()["new_messages"] == 1
    assert bob.get("/api/contact").json()[0]["done_at"] is not None  # the sender sees it was dealt with
    assert owner.post(f"/api/admin/messages/{second}/reopen").json()["done_at"] is None
    assert owner.delete(f"/api/admin/messages/{first}").status_code == 200
    assert [m["id"] for m in owner.get("/api/admin/messages").json()] == [second]
    assert owner.post("/api/admin/messages/999/done").status_code == 404


def test_deleting_a_persons_data_deletes_their_messages_and_nobody_elses(person, people):
    bob, carol = person("bob@example.com"), person("carol@example.com")
    send(bob, "bob's"), send(carol, "carol's")
    with SessionLocal() as db:
        crud.delete_user_data(db, people["bob"])
        assert [m.body for m in db.query(models.Message).all()] == ["carol's"]


def test_the_page_opens_for_anyone_with_an_account_and_not_for_a_stranger(person, people):
    assert person("bob@example.com").get("/contact.html").status_code == 200
    assert "Send feedback" in person("bob@example.com").get("/contact.html").text
    assert person("dana@example.com").get("/contact.html").status_code == 403
