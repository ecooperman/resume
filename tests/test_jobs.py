"""Jobs: private to their owner, pasted in or imported from the job-scoring Chrome extension's CSV. A re-import refreshes scores and never touches
what the person tracked."""
import pytest

from app import config, models
from app.database import SessionLocal
from tests.test_accounts import ADMIN, bobs_resume, key, people, person, production  # noqa: F401 - fixtures

CSV = """url,title,company,salary,tier,summary,matched,gaps,strengths,scored_at,jd_text
https://jobs.example/1,Staff Engineer,Acme,"$200k",Strong,Great fit,Python,,Mentoring,2026-11-01T10:00:00Z,Build things for Acme
https://jobs.example/2,Engineer,Globex,,Weak,Poor fit,,Go,,,Globex wants Go
https://jobs.example/3,,Nowhere,,,,,,,,
,No link,Nobody,,,,,,,,
"""


def upload(who, text=CSV, **data):
    return who.post("/api/jobs/import", files={"file": ("jobs.csv", text.encode(), "text/csv")}, data=data)


def add(who, name="Staff Engineer", **extra):
    resp = who.post("/api/jobs", json={"name": name, **extra})
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_a_pasted_job_is_saved_trimmed_and_listed_without_its_long_texts(person, people):
    bob = person("bob@example.com")
    job = add(bob, "  Staff Engineer ", company="Acme", url="https://jobs.example/9", jd_text="Long description " * 50, salary="$180k")
    assert job["name"] == "Staff Engineer" and job["tier"] is None and job["jd_text"].startswith("Long description")
    listed = bob.get("/api/jobs").json()
    assert [j["id"] for j in listed] == [job["id"]] and "jd_text" not in listed[0] and listed[0]["has_description"] is True
    assert bob.get(f"/api/jobs/{job['id']}").json()["jd_text"].startswith("Long description")


def test_a_job_needs_a_title_and_the_same_link_cannot_be_added_twice(person, people):
    bob = person("bob@example.com")
    assert bob.post("/api/jobs", json={"name": "   "}).status_code == 400
    add(bob, url="https://jobs.example/1")
    again = bob.post("/api/jobs", json={"name": "Other", "url": "https://jobs.example/1"})
    assert again.status_code == 400 and "already have a job with that link" in again.json()["detail"]
    assert add(bob, "No link A")["url"] is None and add(bob, "No link B")["url"] is None  # a pasted job may have no link; those never collide


def test_you_can_track_and_edit_a_job(person, people):
    bob = person("bob@example.com")
    job = add(bob)
    changed = bob.patch(f"/api/jobs/{job['id']}", json={"applied": True, "notes": "Phone screen Tuesday", "company": "Acme Inc"}).json()
    assert changed["applied"] is True and changed["notes"] == "Phone screen Tuesday" and changed["company"] == "Acme Inc"
    cleared = bob.patch(f"/api/jobs/{job['id']}", json={"notes": ""}).json()
    assert cleared["notes"] is None and cleared["applied"] is True  # only what was sent changes
    assert bob.patch(f"/api/jobs/{job['id']}", json={"name": ""}).status_code == 400
    assert bob.delete(f"/api/jobs/{job['id']}").status_code == 200 and bob.get("/api/jobs").json() == []


def test_nobody_else_can_see_change_or_delete_your_jobs(person, people):
    bob, carol = person("bob@example.com"), person("carol@example.com")
    job = add(bob, "BOBS-PRIVATE-JOB", jd_text="BOBS-PRIVATE-DESCRIPTION")
    assert carol.get("/api/jobs").json() == []
    for method, path in (("GET", f"/api/jobs/{job['id']}"), ("PATCH", f"/api/jobs/{job['id']}"), ("DELETE", f"/api/jobs/{job['id']}")):
        resp = carol.request(method, path, json={"notes": "x"} if method == "PATCH" else None)
        assert resp.status_code == 404 and "BOBS-PRIVATE" not in resp.text, (method, path)
    assert bob.get(f"/api/jobs/{job['id']}").json()["name"] == "BOBS-PRIVATE-JOB"  # untouched
    assert person("dana@example.com").get("/api/jobs").status_code == 403  # no account: nothing at all


def test_the_same_link_is_fine_for_two_different_people(person, people):
    add(person("bob@example.com"), url="https://jobs.example/shared")
    add(person("carol@example.com"), url="https://jobs.example/shared")  # per owner, not global


def test_a_job_can_only_be_tied_to_one_of_your_own_resumes(person, people):
    bob, carol = person("bob@example.com"), person("carol@example.com")
    mine = bobs_resume(bob)["id"]
    assert add(bob, person_id=mine)["person_id"] == mine
    stolen = carol.post("/api/jobs", json={"name": "X", "person_id": mine})
    assert stolen.status_code == 400 and "isn't one of yours" in stolen.json()["detail"]


def test_deleting_a_resume_keeps_its_jobs_and_unlinks_them(person, people):
    owner = person(ADMIN)
    first = owner.post("/api/people", json={"name": "First", "slug": "first", "resume_yaml": "basics: {name: A}"}).json()
    second = owner.post("/api/people", json={"name": "Second", "slug": "second", "resume_yaml": "basics: {name: B}"}).json()
    job = add(owner, person_id=second["id"])
    owner.patch(f"/api/people/{first['id']}", json={"is_default": True})
    assert owner.delete(f"/api/people/{second['id']}").status_code == 200
    assert owner.get(f"/api/jobs/{job['id']}").json()["person_id"] is None


def test_importing_the_extensions_csv_adds_jobs_skips_the_incomplete_ones_and_counts(person, people):
    bob = person("bob@example.com")
    got = upload(bob)
    assert got.status_code == 200 and got.json() == {"created": 2, "updated": 0, "skipped": 2}  # one without a title, one without a link
    strong = next(j for j in bob.get("/api/jobs").json() if j["tier"] == "Strong")
    assert strong["name"] == "Staff Engineer" and strong["company"] == "Acme" and strong["scored_at"] == "2026-11-01T10:00:00Z"
    assert bob.get(f"/api/jobs/{strong['id']}").json()["jd_text"] == "Build things for Acme"


def test_a_re_import_refreshes_scores_and_never_touches_what_you_tracked(person, people):
    bob = person("bob@example.com")
    upload(bob)
    job = next(j for j in bob.get("/api/jobs").json() if j["tier"] == "Strong")
    bob.patch(f"/api/jobs/{job['id']}", json={"applied": True, "notes": "Applied Monday"})
    rescored = CSV.replace("Strong,Great fit", "Good,Less great fit")
    assert upload(bob, rescored).json() == {"created": 0, "updated": 2, "skipped": 2}
    after = bob.get(f"/api/jobs/{job['id']}").json()
    assert after["tier"] == "Good" and after["summary"] == "Less great fit"  # refreshed
    assert after["applied"] is True and after["notes"] == "Applied Monday"  # not touched
    assert len(bob.get("/api/jobs").json()) == 2  # no duplicates


def test_the_resume_chosen_at_import_is_set_on_new_jobs_filled_in_on_old_ones_and_never_changed(person, people):
    owner = person(ADMIN)
    one = owner.post("/api/people", json={"name": "One", "slug": "one", "resume_yaml": "basics: {name: A}"}).json()["id"]
    two = owner.post("/api/people", json={"name": "Two", "slug": "two", "resume_yaml": "basics: {name: B}"}).json()["id"]
    pasted = add(owner, "Pasted before", url="https://jobs.example/1")  # already here, tied to no resume
    assert pasted["person_id"] is None
    upload(owner, CSV, person_id=str(one))
    by_url = {j["url"]: j for j in owner.get("/api/jobs").json()}
    assert by_url["https://jobs.example/1"]["person_id"] == one  # filled in on the old one
    assert by_url["https://jobs.example/2"]["person_id"] == one  # set on the new one
    upload(owner, CSV, person_id=str(two))  # a later import under another resume
    assert {j["person_id"] for j in owner.get("/api/jobs").json()} == {one}  # never silently re-tagged


@pytest.mark.parametrize("text,words", [
    ("title,company\nX,Y\n", "'url' column"),
    ("", "'url' column"),
])
def test_a_csv_without_a_link_column_is_refused_in_plain_words(person, people, text, words):
    got = upload(person("bob@example.com"), text)
    assert got.status_code == 400 and words in got.json()["detail"]


def test_import_limits_are_enforced(person, people, monkeypatch):
    bob = person("bob@example.com")
    monkeypatch.setattr(config, "MAX_IMPORT_ROWS", 1)
    assert upload(bob).status_code == 400
    monkeypatch.setattr(config, "MAX_IMPORT_ROWS", 1000)
    monkeypatch.setattr(config, "MAX_IMPORT_BYTES", 20)
    assert upload(bob).status_code == 400
    monkeypatch.setattr(config, "MAX_IMPORT_BYTES", 8 * 1024 * 1024)
    assert bob.post("/api/jobs/import", files={"file": ("x.csv", b"\xff\xfe\x00bad", "text/csv")}).status_code == 400


def test_one_account_can_only_keep_so_many_jobs(person, people, monkeypatch):
    monkeypatch.setattr(config, "MAX_JOBS_PER_ACCOUNT", 2)
    bob = person("bob@example.com")
    add(bob, "A")
    add(bob, "B")
    full = bob.post("/api/jobs", json={"name": "C"})
    assert full.status_code == 400 and "up to 2 jobs" in full.json()["detail"]
    assert upload(bob).json()["created"] == 0  # the import respects the same room


def test_deleting_a_removed_persons_data_deletes_their_jobs_too(person, people):
    bob, owner = person("bob@example.com"), person(ADMIN)
    add(bob, "Mine")
    bob_id = next(u["id"] for u in owner.get("/api/users").json() if u["email"] == "bob@example.com")
    owner.patch(f"/api/users/{bob_id}", json={"status": "removed"})
    assert owner.delete(f"/api/users/{bob_id}/data").status_code == 200
    with SessionLocal() as db:
        assert db.query(models.Job).count() == 0


# --- deleting many at once (the page's "Delete the N jobs showing", after two confirmations) ---------------------------------------------


def test_deleting_the_named_jobs_removes_exactly_those_and_the_rest_stay(person, people):
    bob = person("bob@example.com")
    ids = [add(bob, f"Job {n}", url=f"https://jobs.example/{n}")["id"] for n in range(5)]
    got = bob.post("/api/jobs/delete", json={"job_ids": ids[:3] + [ids[0], 99999]})  # a repeat and one that does not exist change nothing
    assert got.status_code == 200 and got.json() == {"removed": 3}
    assert sorted(j["id"] for j in bob.get("/api/jobs").json()) == ids[3:]
    assert bob.get(f"/api/jobs/{ids[0]}").status_code == 404


def test_deleting_many_can_never_touch_anyone_elses_jobs(person, people):
    bob, carol = person("bob@example.com"), person("carol@example.com")
    bobs = [add(bob, f"Bobs {n}", url=f"https://jobs.example/b{n}")["id"] for n in range(3)]
    mine = add(carol, "Carols", url="https://jobs.example/c")["id"]
    assert carol.post("/api/jobs/delete", json={"job_ids": bobs + [mine]}).json() == {"removed": 1}  # only her own
    assert len(bob.get("/api/jobs").json()) == 3


def test_deleting_many_needs_a_list_of_a_sensible_size(person, people, monkeypatch):
    bob = person("bob@example.com")
    assert bob.post("/api/jobs/delete", json={"job_ids": []}).status_code == 400
    assert bob.post("/api/jobs/delete", json={}).status_code == 422
    monkeypatch.setattr(config, "MAX_JOBS_PER_ACCOUNT", 3)
    assert bob.post("/api/jobs/delete", json={"job_ids": [1, 2, 3, 4]}).status_code == 400
