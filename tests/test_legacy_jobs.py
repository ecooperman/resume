"""Moving a person's jobs out of time-management's database (manage.py import-jobs): read-only on the old file, never overwrites what is already
here, ties each job to the resume its label names, and keeps one account's jobs from ever landing in another's."""
import argparse
import hashlib
import sqlite3

import pytest

import manage
from app import config, crud, legacy_jobs, models
from tests.conftest import resume_yaml

SCHEMA = """
create table users (id integer primary key, email text not null);
create table people (id integer primary key, owner_id integer, name text, resume_person_slug text);
create table jobs (id integer primary key, owner_id integer, name text, url text, company text, company_url text, applied boolean, person_id integer,
  salary text, tier text, summary text, matched text, gaps text, strengths text, scored_at text, jd_text text, generated_resume_yaml text,
  generated_cover_letter text, resume_generated_at text, created_at text);
"""
COLUMNS = ("id", "owner_id", "name", "url", "company", "applied", "person_id", "tier", "summary", "jd_text", "generated_resume_yaml",
           "generated_cover_letter", "resume_generated_at", "created_at", "scored_at")


@pytest.fixture()
def old(tmp_path):
    """A time-management database: Evan (user 1, labels: Me -> slug 'evan', Rach -> slug 'rach', Nobody -> no slug) and Zed (user 2)."""
    path = tmp_path / "tasks.db"
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    conn.executemany("insert into users values (?, ?)", [(1, "Evan@Example.com"), (2, "zed@example.com")])
    conn.executemany("insert into people values (?, ?, ?, ?)", [(1, 1, "Me", "evan"), (2, 1, "Rach", "rach"), (3, 1, "Nobody", None), (4, 2, "Zed", "evan")])

    def add(**row):
        row = {"owner_id": 1, "applied": 0, **row}
        conn.execute(f"insert into jobs ({', '.join(row)}) values ({', '.join('?' * len(row))})", list(row.values()))

    add(name="Staff Eng", url="https://j/1", company="Acme", person_id=1, tier="Strong", summary="Great", jd_text="Python", applied=1,
        generated_resume_yaml="basics: {name: Evan}", generated_cover_letter="Dear Acme", resume_generated_at="2026-10-02 09:30:00.123456",
        created_at="2026-09-01 08:00:00.000000", scored_at="2026-09-02 08:00:00")
    add(name="Eng", url="https://j/2", company="Globex", person_id=2, tier="Weak")  # Rach's resume: not one of Evan's here
    add(name="Unlabelled", url="https://j/3", person_id=3)
    add(name="Plain", url="https://j/4")
    add(name="No link", url="")  # skipped
    add(name="Zeds", url="https://j/9", owner_id=2, person_id=4)  # another account's: never moved by Evan's import
    conn.commit()
    conn.close()
    return path


@pytest.fixture()
def evan(make_user, as_user):
    uid = make_user("evan@example.com")
    with as_user(uid) as s:
        crud.create_person(s, "evan", "Evan", resume_yaml("Evan"), allow_default=False)
    return uid


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(as_user, uid, path, **kw):
    with as_user(uid) as s:
        return legacy_jobs.import_jobs(s, str(path), "evan@example.com", **kw)


def test_jobs_come_across_with_everything_a_label_becoming_the_resume_and_the_old_file_untouched(old, evan, as_user):
    before = digest(old)
    result = run(as_user, evan, old)
    assert result == {"created": 4, "merged": 0, "skipped": 1, "over_limit": 0, "labels_unmatched": ["rach"], "dry_run": False}
    with as_user(evan) as s:
        jobs = {j.url: j for j in s.query(models.Job).all()}
        evans_resume = crud.get_person_by_slug(s, "evan").id
        assert sorted(jobs) == ["https://j/1", "https://j/2", "https://j/3", "https://j/4"]  # Zed's job is not here
        one = jobs["https://j/1"]
        assert one.name == "Staff Eng" and one.company == "Acme" and one.applied is True and one.tier == "Strong" and one.jd_text == "Python"
        assert one.generated_resume_yaml == "basics: {name: Evan}" and one.generated_cover_letter == "Dear Acme"
        assert one.resume_generated_at.isoformat().startswith("2026-10-02T09:30:00") and one.created_at.isoformat().startswith("2026-09-01T08:00")
        assert one.scored_at is not None and one.person_id == evans_resume  # label Me -> resume slug evan
        assert jobs["https://j/2"].person_id is None and jobs["https://j/3"].person_id is None and jobs["https://j/4"].applied is False
    assert digest(old) == before  # time-management's database was only read


def test_a_job_already_here_only_has_its_gaps_filled_and_applied_is_kept_from_either_side(old, evan, as_user):
    with as_user(evan) as s:  # came over earlier by the extension's CSV: scored differently, described, not applied, no letter
        s.add(models.Job(name="Staff Engineer (newer title)", url="https://j/1", tier="Good", summary="Mine", notes="my note", applied=False))
        s.add(models.Job(name="Eng", url="https://j/2", applied=True))
        s.commit()
    result = run(as_user, evan, old)
    assert result["merged"] == 2 and result["created"] == 2
    with as_user(evan) as s:
        one = s.query(models.Job).filter_by(url="https://j/1").one()
        assert one.name == "Staff Engineer (newer title)" and one.tier == "Good" and one.summary == "Mine" and one.notes == "my note"  # nothing replaced
        assert one.jd_text == "Python" and one.generated_cover_letter == "Dear Acme" and one.company == "Acme"  # the gaps were filled
        assert one.applied is True  # applied on the old side
        assert one.person_id == crud.get_person_by_slug(s, "evan").id
        assert s.query(models.Job).filter_by(url="https://j/2").one().applied is True  # applied here stays
        assert s.query(models.Job).count() == 4


def test_running_it_twice_changes_nothing_the_second_time(old, evan, as_user):
    run(as_user, evan, old)
    again = run(as_user, evan, old)
    assert again["created"] == 0 and again["merged"] == 4
    with as_user(evan) as s:
        assert s.query(models.Job).count() == 4


def test_a_dry_run_reports_and_keeps_nothing(old, evan, as_user):
    result = run(as_user, evan, old, dry_run=True)
    assert result["created"] == 4 and result["dry_run"] is True
    with as_user(evan) as s:
        assert s.query(models.Job).count() == 0


def test_the_jobs_land_in_the_named_account_and_nobody_elses(old, evan, make_user, as_user):
    other = make_user("carol@example.com")
    run(as_user, evan, old)
    with as_user(other) as s:
        assert s.query(models.Job).count() == 0


def test_the_job_limit_is_respected(old, evan, as_user, monkeypatch):
    monkeypatch.setattr(config, "MAX_JOBS_PER_ACCOUNT", 2)
    result = run(as_user, evan, old)
    assert result["created"] == 2 and result["over_limit"] == 2


def test_a_missing_file_or_an_owner_not_in_it_says_so(old, evan, as_user, tmp_path):
    with as_user(evan) as s:
        with pytest.raises(legacy_jobs.LegacyImportError, match="no account"):
            legacy_jobs.import_jobs(s, str(old), "nobody@example.com")
        with pytest.raises(legacy_jobs.LegacyImportError, match="Could not read"):
            legacy_jobs.import_jobs(s, str(tmp_path / "missing.db"), "evan@example.com")
    assert not (tmp_path / "missing.db").exists()  # opening read-only never creates the file


def test_the_command_line_moves_one_persons_jobs_and_reports(old, evan, capsys, as_user):
    manage.cmd_import_jobs(argparse.Namespace(source=str(old), owner="Evan@example.com", dry_run=False))
    out = capsys.readouterr().out
    assert "4 added" in out and "1 skipped" in out and "rach" in out
    with as_user(evan) as s:
        assert s.query(models.Job).count() == 4
    with pytest.raises(SystemExit):
        manage.cmd_import_jobs(argparse.Namespace(source=str(old), owner="nobody@example.com", dry_run=False))
