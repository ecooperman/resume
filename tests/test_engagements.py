"""Engagements (client and project work, one structured form) and notes are stored per person and private to them: another person
with a valid account can read, change, delete or even guess nothing of them. The form's rules are enforced on the server, not
just the page."""
import pytest

from tests.test_accounts import ADMIN, key, people, person, production  # noqa: F401 - fixtures

FULL = {
    "employer": "Acme", "client": "Blue Harbor", "client_does": "Sells health plans", "industry": "Health insurance",
    "regulated": "yes", "regulators": "HIPAA", "technologies": ["Go", "Kafka"], "role": "Tech lead", "start": "Mar 2023", "end": "Present",
    "outcomes": ["Cut processing from 4 hours to 12 minutes"], "confidential": False,
}


def add(who, **changes):
    resp = who.post("/api/engagements", json={**FULL, **changes})
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_an_engagement_is_saved_and_listed_with_every_field(person, people):
    bob = person("bob@example.com")
    saved = add(bob)
    assert {k: v for k, v in saved.items() if k not in ("id", "position")} == FULL
    assert bob.get("/api/engagements").json() == [saved]


def test_only_the_client_is_required_and_the_rest_has_sensible_defaults(person, people):
    saved = add(person("bob@example.com"), employer="", client_does="", industry="", regulated="unknown", regulators="", technologies=[], role="", start="", end="", outcomes=[])
    assert saved["regulated"] == "unknown" and saved["technologies"] == [] and saved["confidential"] is False
    bare = person("bob@example.com").post("/api/engagements", json={"client": "Only a client"})
    assert bare.status_code == 200 and bare.json()["employer"] == ""


@pytest.mark.parametrize("bad", [
    {"client": ""}, {"client": "   "}, {"client": "x" * 301}, {"regulated": "maybe"}, {"technologies": ["t"] * 31}, {"technologies": ["x" * 61]},
    {"outcomes": ["o"] * 11}, {"outcomes": ["x" * 301]}, {"role": "x" * 301}, {"client": 5, "technologies": "Go"},
])
def test_the_forms_rules_are_enforced_by_the_server(person, people, bad):
    bob = person("bob@example.com")
    assert bob.post("/api/engagements", json={**FULL, **bad}).status_code == 422
    assert bob.get("/api/engagements").json() == []


def test_text_is_cleaned_technologies_deduplicated_and_regulators_only_kept_when_regulated(person, people):
    bob = person("bob@example.com")
    saved = add(bob, client="  Blue\n\tHarbor  ", technologies=["Go", " go ", "Go", "", "Kafka"], outcomes=["  a  ", ""], regulated="no", regulators="HIPAA")
    assert saved["client"] == "Blue Harbor" and saved["technologies"] == ["Go", "go", "Kafka"] and saved["outcomes"] == ["a"]
    assert saved["regulators"] == ""  # "not regulated" cannot also name a regulator


def test_engagements_keep_their_order_and_can_be_edited_and_removed(person, people):
    bob = person("bob@example.com")
    first, second = add(bob, client="One"), add(bob, client="Two")
    assert [e["client"] for e in bob.get("/api/engagements").json()] == ["One", "Two"]
    changed = bob.put(f"/api/engagements/{first['id']}", json={**FULL, "client": "One renamed", "confidential": True}).json()
    assert changed["client"] == "One renamed" and changed["confidential"] is True and changed["position"] == first["position"]
    assert bob.delete(f"/api/engagements/{second['id']}").status_code == 200
    assert bob.delete(f"/api/engagements/{second['id']}").status_code == 404
    assert [e["client"] for e in bob.get("/api/engagements").json()] == ["One renamed"]


def test_there_is_a_limit_so_a_person_cannot_fill_the_database(person, people, monkeypatch):
    from app import builder

    monkeypatch.setattr(builder, "MAX_ENGAGEMENTS", 3)
    bob = person("bob@example.com")
    for n in range(3):
        add(bob, client=f"c{n}")
    over = bob.post("/api/engagements", json=FULL)
    assert over.status_code == 409 and "up to 3" in over.json()["detail"]
    assert person("carol@example.com").post("/api/engagements", json=FULL).status_code == 200  # their own allowance


def test_another_person_can_neither_see_change_nor_delete_them(person, people):
    bob, carol = person("bob@example.com"), person("carol@example.com")
    mine = add(bob, client="BOBS-SECRET-CLIENT")
    assert carol.get("/api/engagements").json() == []
    assert "BOBS-SECRET" not in carol.get("/api/engagements").text
    assert carol.put(f"/api/engagements/{mine['id']}", json={**FULL, "client": "HIJACKED"}).status_code == 404
    assert carol.delete(f"/api/engagements/{mine['id']}").status_code == 404
    assert person(ADMIN).get("/api/engagements").json() == []  # an administrator is just another owner here
    assert bob.get("/api/engagements").json()[0]["client"] == "BOBS-SECRET-CLIENT"


def test_new_engagements_belong_to_the_signed_in_person_whatever_the_request_says(person, people):
    bob, carol = person("bob@example.com"), person("carol@example.com")
    bob.post("/api/engagements", json={**FULL, "owner_id": people["carol"], "id": 999})
    assert carol.get("/api/engagements").json() == [] and len(bob.get("/api/engagements").json()) == 1


def test_notes_are_kept_per_person_and_bounded(person, people):
    bob, carol = person("bob@example.com"), person("carol@example.com")
    assert bob.get("/api/builder/notes").json() == {"notes": "", "keep_files": True}
    assert bob.put("/api/builder/notes", json={"notes": "  leave out my first job \x00 "}).json() == {"notes": "leave out my first job"}
    bob.put("/api/builder/notes", json={"notes": "changed"})
    assert bob.get("/api/builder/notes").json() == {"notes": "changed", "keep_files": True} and carol.get("/api/builder/notes").json() == {"notes": "", "keep_files": True}
    assert bob.put("/api/builder/notes", json={"notes": "x" * 5001}).status_code == 422


def test_removing_a_user_deletes_their_engagements_and_notes_with_the_rest(person, people):
    from app import crud, models
    from app.database import SessionLocal
    from shared_python import owned

    bob = person("bob@example.com")
    add(bob)
    bob.put("/api/builder/notes", json={"notes": "mine"})
    with SessionLocal() as db:
        assert crud.delete_user_data(db, people["bob"])["engagements"] == 1
    with owned.scope(people["bob"]), SessionLocal() as db:
        assert db.query(models.Engagement).count() == 0 and db.query(models.BuilderNotes).count() == 0
