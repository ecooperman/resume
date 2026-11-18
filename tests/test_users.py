"""The Users page's routes: an administrator lets people in, removes and restores them, and deletes a removed person's data."""
import sqlite3

import pytest

from app import crud, models, notify
from tests.conftest import resume_yaml

URL = "/api/users"


@pytest.fixture()
def sent(monkeypatch):
    told = []
    monkeypatch.setattr(notify, "tell_let_in", lambda to, inviter: told.append((to, inviter)))
    return told


def user_id(admin_client, email):
    return next(u["id"] for u in admin_client.get(URL).json() if u["email"] == email)


def test_an_administrator_lets_someone_in_and_they_are_told_by_email(admin_client, client_as, sent):
    added = admin_client.post(URL, json={"email": "  Zed@Example.com "}).json()
    assert added["email"] == "zed@example.com" and added["status"] == "active"
    assert sent == [("zed@example.com", "dev@localhost.test")]
    assert client_as("zed@example.com").get("/api/me").status_code == 200


def test_the_dashboard_can_add_someone_without_each_app_emailing_them(admin_client, sent):
    assert admin_client.post(URL + "?notify=false", json={"email": "zed@example.com"}).status_code == 200
    assert sent == []


def test_adding_a_removed_person_restores_them_and_tells_them_but_someone_already_in_is_left_alone(admin_client, make_user, sent):
    make_user("eve@example.com", status=models.USER_REMOVED)
    assert admin_client.post(URL, json={"email": "eve@example.com"}).json()["status"] == "active"
    assert len(sent) == 1
    admin_client.post(URL, json={"email": "eve@example.com"})
    assert len(sent) == 1


@pytest.mark.parametrize("email", ["nope", "a@b", "legacy-owner@resume.invalid", "", "two words@example.com"])
def test_a_bad_address_is_refused(admin_client, sent, email):
    assert admin_client.post(URL, json={"email": email}).status_code in (400, 422)
    assert sent == []


def test_the_placeholder_that_holds_old_resumes_is_never_listed_or_reachable(admin_client, make_user):
    legacy = make_user(crud.LEGACY_OWNER_EMAIL)
    assert crud.LEGACY_OWNER_EMAIL not in [u["email"] for u in admin_client.get(URL).json()]
    assert admin_client.patch(f"{URL}/{legacy}", json={"status": "removed"}).status_code == 404
    assert admin_client.delete(f"{URL}/{legacy}/data").status_code == 404


def test_an_administrators_access_cannot_be_changed_or_removed_from_the_users_page(admin_client, monkeypatch):
    from app import config

    me = user_id(admin_client, "dev@localhost.test")
    assert admin_client.patch(f"{URL}/{me}", json={"status": "removed"}).status_code == 409
    assert admin_client.delete(f"{URL}/{me}/data").status_code == 409


def test_removing_and_restoring_a_person_does_not_touch_what_they_own(admin_client, client_as, make_user):
    bob = client_as("bob@example.com")
    bob_id = make_user("bob@example.com")
    bob.post("/api/people", json={"name": "Bob", "resume_yaml": resume_yaml("Bob")})
    admin_client.patch(f"{URL}/{bob_id}", json={"status": "removed"})
    assert bob.get("/api/people").status_code == 403
    admin_client.patch(f"{URL}/{bob_id}", json={"status": "active"})
    assert [p["name"] for p in bob.get("/api/people").json()] == ["Bob"]


def test_deleting_a_removed_persons_data_deletes_exactly_theirs_and_their_built_files(admin_client, client_as, make_user, site_dir, as_user):
    bob_id, carol_id = make_user("bob@example.com"), make_user("carol@example.com")
    bob_slug = client_as("bob@example.com").post("/api/people", json={"name": "Bob", "resume_yaml": resume_yaml("Bob")}).json()["slug"]
    client_as("carol@example.com").post("/api/people", json={"name": "Carol", "resume_yaml": resume_yaml("Carol")})
    admin_client.post("/api/people", json={"name": "Owner", "slug": "owner", "resume_yaml": resume_yaml("Owner")})
    with as_user(bob_id) as db:  # planted: files and a code of Bob's
        person = db.query(models.Person).first()
        crud.generate_code(db, label="x", person_id=person.id)
    for tree in ("public", "private"):
        (site_dir / tree / bob_slug).mkdir(parents=True)
        (site_dir / tree / bob_slug / "index.html").write_text("bob")
    assert admin_client.delete(f"{URL}/{bob_id}/data").status_code == 409  # not removed yet
    admin_client.patch(f"{URL}/{bob_id}", json={"status": "removed"})
    deleted = admin_client.delete(f"{URL}/{bob_id}/data").json()["deleted"]
    assert deleted == {"people": 1, "codes": 1, "keys": 0, "engagements": 0}
    assert not (site_dir / "public" / bob_slug).exists() and not (site_dir / "private" / bob_slug).exists()
    assert (site_dir / "public" / "owner").exists()  # the administrator's files stay
    assert [p["name"] for p in client_as("carol@example.com").get("/api/people").json()] == ["Carol"]
    assert [p["name"] for p in admin_client.get("/api/people").json()] == ["Owner"]
    with as_user(bob_id) as db:
        assert db.query(models.Person).count() == 0 and db.query(models.AccessCode).count() == 0


def fake_apps(tmp_path, monkeypatch, **emails_by_folder):
    """Other apps' databases next to this one, each with the given people (see shared_python.directory)."""
    for folder, (filename, emails) in emails_by_folder.items():
        (tmp_path / folder).mkdir(exist_ok=True)
        connection = sqlite3.connect(tmp_path / folder / filename)
        connection.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT NOT NULL)")
        connection.executemany("INSERT INTO users (email) VALUES (?)", [(e,) for e in emails])
        connection.commit()
        connection.close()
    monkeypatch.setenv("PEOPLE_APPS_DIR", str(tmp_path))


def test_suggestions_are_everyone_from_any_app_minus_people_already_here(admin_client, make_user, tmp_path, monkeypatch):
    make_user("here@example.com")
    fake_apps(tmp_path, monkeypatch, lists=("gifts.db", ["here@example.com", "ann@example.com"]), finances=("finances.db", ["bob@example.com"]))
    got = admin_client.get(URL + "/suggestions").json()
    assert [p["email"] for p in got] == ["ann@example.com", "bob@example.com", "dev@localhost.test"] or "here@example.com" not in [p["email"] for p in got]
    assert "ann@example.com" in [p["email"] for p in got] and "here@example.com" not in [p["email"] for p in got]
