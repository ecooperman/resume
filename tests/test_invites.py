"""Asking for someone to be let in: anyone with an account may ask for a friend (a limit each), an administrator approves or declines on the
Invites page, and an approved person gets an account and an email. Nobody sees anyone else's requests; only an administrator sees the queue."""
import pytest

from app import config, models, notify
from app.database import SessionLocal
from tests.test_accounts import ADMIN, key, people, person, production  # noqa: F401 - fixtures


@pytest.fixture(autouse=True)
def mail(production, monkeypatch):  # after `production`, which installs its own recorder for the same function
    """Record what would have been emailed (nothing may reach the network)."""
    sent = {"let_in": [], "asked": []}
    monkeypatch.setattr(notify, "tell_let_in", lambda to, by: sent["let_in"].append((to, by)))
    monkeypatch.setattr(notify, "invite_requested", lambda by, invitee: sent["asked"].append((by, invitee)))
    return sent


def ask(who, email):
    return who.post("/api/app-invites", json={"email": email})


def queue(admin):
    return admin.get("/api/admin/invites").json()


def test_a_person_asks_for_a_friend_and_only_they_and_an_administrator_can_see_it(person, people, mail):
    bob, carol, owner = person("bob@example.com"), person("carol@example.com"), person(ADMIN)
    got = ask(bob, "Dana@Example.com ")
    assert got.status_code == 200 and got.json()["email"] == "dana@example.com" and got.json()["status"] == "pending"
    assert [i["email"] for i in bob.get("/api/app-invites").json()] == ["dana@example.com"]
    assert carol.get("/api/app-invites").json() == []  # not carol's business
    waiting = queue(owner)
    assert len(waiting) == 1 and waiting[0]["invited_by_email"] == "bob@example.com"
    assert mail["asked"] == [("bob@example.com", "dana@example.com")] and mail["let_in"] == []  # the administrator is told; dana is not, yet


def test_approving_gives_the_friend_an_account_and_one_email_and_the_asker_sees_it(person, people, mail):
    bob, owner = person("bob@example.com"), person(ADMIN)
    invite = ask(bob, "dana@example.com").json()
    assert person("dana@example.com").get("/api/me").status_code == 403  # not in yet
    done = owner.post(f"/api/admin/invites/{invite['id']}/approve")
    assert done.status_code == 200 and done.json()["status"] == "approved"
    assert person("dana@example.com").get("/api/me").json()["email"] == "dana@example.com"
    assert mail["let_in"] == [("dana@example.com", "bob@example.com")]
    assert bob.get("/api/app-invites").json()[0]["status"] == "approved"
    assert queue(owner) == []
    assert owner.post(f"/api/admin/invites/{invite['id']}/approve").status_code == 409  # already decided


def test_declining_keeps_the_slot_used_and_blocks_asking_again_until_the_administrator_allows_it(person, people, mail):
    bob, owner = person("bob@example.com"), person(ADMIN)
    invite = ask(bob, "dana@example.com").json()
    assert owner.post(f"/api/admin/invites/{invite['id']}/decline").json()["status"] == "declined"
    again = ask(bob, "dana@example.com")
    assert again.status_code == 409 and "declined" in again.json()["detail"]
    assert bob.get("/api/me").json()["invites_left"] == config.INVITES_PER_USER - 1  # being refused does not hand the slot back
    declined = owner.get("/api/admin/invites/declined").json()
    assert [i["email"] for i in declined] == ["dana@example.com"] and mail["let_in"] == []
    assert owner.post(f"/api/admin/invites/{declined[0]['id']}/reinstate").status_code == 200
    assert bob.get("/api/me").json()["invites_left"] == config.INVITES_PER_USER  # the request is gone, so the invite is back
    assert ask(bob, "dana@example.com").status_code == 200


def test_each_person_has_a_limit_and_withdrawing_a_waiting_request_gives_one_back(person, people, monkeypatch):
    monkeypatch.setattr(config, "INVITES_PER_USER", 2)
    bob = person("bob@example.com")
    first = ask(bob, "a@example.com").json()
    assert ask(bob, "b@example.com").status_code == 200 and bob.get("/api/me").json()["invites_left"] == 0
    over = ask(bob, "c@example.com")
    assert over.status_code == 403 and "used all 2" in over.json()["detail"]
    assert bob.delete(f"/api/app-invites/{first['id']}").status_code == 200
    assert bob.get("/api/me").json()["invites_left"] == 1 and ask(bob, "c@example.com").status_code == 200


def test_an_administrators_own_request_is_approved_at_once_and_has_no_limit(person, people, mail, monkeypatch):
    monkeypatch.setattr(config, "INVITES_PER_USER", 1)
    owner = person(ADMIN)
    assert owner.get("/api/me").json()["invites_left"] is None
    for name in ("x", "y", "z"):
        got = ask(owner, f"{name}@example.com")
        assert got.status_code == 200 and got.json()["status"] == "approved"
    assert [to for to, _ in mail["let_in"]] == ["x@example.com", "y@example.com", "z@example.com"] and mail["asked"] == []
    assert person("y@example.com").get("/api/me").status_code == 200


@pytest.mark.parametrize("email,status,words", [
    ("not an email", 400, "email address"),
    ("carol@example.com", 409, "already sign in"),  # has an account
    ("eve@example.com", 409, "removed"),  # was removed: only an administrator can restore them
    (ADMIN, 409, "already sign in"),
])
def test_requests_that_make_no_sense_are_refused_with_a_reason(person, people, email, status, words):
    got = ask(person("bob@example.com"), email)
    assert got.status_code == status and words in got.json()["detail"]


def test_the_same_person_cannot_be_asked_for_twice_by_anyone_whatever_the_capitals(person, people):
    ask(person("bob@example.com"), "dana@example.com")
    again = ask(person("carol@example.com"), "DANA@example.com")
    assert again.status_code == 409 and "already waiting" in again.json()["detail"]


def test_an_ordinary_account_cannot_read_the_queue_or_decide_anything(person, people):
    bob, owner = person("bob@example.com"), person(ADMIN)
    invite = ask(bob, "dana@example.com").json()
    for method, path in (("GET", "/api/admin/invites"), ("GET", "/api/admin/invites/declined"), ("POST", f"/api/admin/invites/{invite['id']}/approve"),
                         ("POST", f"/api/admin/invites/{invite['id']}/decline"), ("POST", f"/api/admin/invites/{invite['id']}/reinstate")):
        assert bob.request(method, path).status_code == 403, (method, path)
    assert queue(owner)[0]["email"] == "dana@example.com"  # nothing changed


def test_you_can_withdraw_only_your_own_waiting_request(person, people):
    bob, carol, owner = person("bob@example.com"), person("carol@example.com"), person(ADMIN)
    mine = ask(bob, "dana@example.com").json()
    assert carol.delete(f"/api/app-invites/{mine['id']}").status_code == 404  # looks like one that does not exist
    owner.post(f"/api/admin/invites/{mine['id']}/approve")
    assert bob.delete(f"/api/app-invites/{mine['id']}").status_code == 409  # already decided
    assert bob.delete("/api/app-invites/99999").status_code == 404


def test_someone_an_administrator_added_meanwhile_is_not_emailed_a_second_time(person, people, mail):
    bob, owner = person("bob@example.com"), person(ADMIN)
    invite = ask(bob, "dana@example.com").json()
    assert owner.post("/api/users?notify=false", json={"email": "dana@example.com"}).status_code == 200
    assert owner.post(f"/api/admin/invites/{invite['id']}/approve").json()["status"] == "approved"
    assert mail["let_in"] == []  # they already had access


def test_deleting_a_removed_persons_data_deletes_their_requests_too(person, people):
    bob, owner = person("bob@example.com"), person(ADMIN)
    ask(bob, "dana@example.com")
    bob_id = next(u["id"] for u in owner.get("/api/users").json() if u["email"] == "bob@example.com")
    assert owner.patch(f"/api/users/{bob_id}", json={"status": "removed"}).status_code == 200
    assert owner.delete(f"/api/users/{bob_id}/data").status_code == 200
    with SessionLocal() as db:
        assert db.query(models.Invite).count() == 0


def test_a_stranger_gets_nothing_from_the_invite_routes_and_the_invites_page_is_for_everyone_with_an_account(person, people):
    dana = person("dana@example.com")
    assert dana.get("/api/app-invites").status_code == 403 and ask(dana, "x@example.com").status_code == 403
    assert person("bob@example.com").get("/invites.html").status_code == 200
    assert "Invite someone" in person("bob@example.com").get("/invites.html").text
