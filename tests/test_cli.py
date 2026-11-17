"""The command-line tools run on the server as the operator, not as a signed-in person: manage.py (access codes, seeding) and
build.py (the deploy-time site build). They look past the per-person filter on purpose, so what they may touch is tested."""
import argparse

import pytest

import build
import manage
from app import config, crud, models
from tests.conftest import resume_yaml


@pytest.fixture()
def two(make_user, as_user, db):
    admin_id = db.query(models.User).filter(models.User.email == config.DEV_USER_EMAIL).first().id
    bob = make_user("bob@example.com")
    with as_user(admin_id) as s:
        evan = crud.create_person(s, "evan", "Evan", resume_yaml("Evan"), is_default=True)
        crud.update_person(s, evan.id, published=True)
        crud.create_person(s, "draft", "Draftie", resume_yaml("Draftie"))
    with as_user(bob) as s:
        crud.create_person(s, "u7-aaaaaaaaaa", "Bobby", resume_yaml("Bobby"), allow_default=False)
    return {"admin": admin_id, "bob": bob}


def test_build_makes_pages_only_for_published_people(two, site_dir):
    build.SITE = site_dir
    build.main()
    assert sorted(p.name for p in (site_dir / "public").iterdir()) == ["evan"]
    assert sorted(p.name for p in (site_dir / "private").iterdir()) == ["evan"]


def test_a_code_made_on_the_command_line_belongs_to_the_owner_of_the_person_it_unlocks(two, capsys, as_user):
    manage.cmd_create(argparse.Namespace(person="evan", label="Acme", days=30))
    assert "Acme" in capsys.readouterr().out
    with as_user(two["admin"]) as s:
        assert [c.label for c in s.query(models.AccessCode).all()] == ["Acme"]
    with as_user(two["bob"]) as s:
        assert s.query(models.AccessCode).count() == 0


def test_the_default_person_is_the_one_a_code_is_made_for_when_none_is_named(two, capsys):
    manage.cmd_create(argparse.Namespace(person=None, label="Default", days=None))
    assert "person: Evan" in capsys.readouterr().out


def test_list_and_revoke_see_every_code(two, capsys, as_user):
    manage.cmd_create(argparse.Namespace(person="evan", label="One", days=None))
    capsys.readouterr()
    manage.cmd_list(argparse.Namespace())
    out = capsys.readouterr().out
    assert "One" in out and "Evan" in out
    code = out.split()[-1]
    manage.cmd_revoke(argparse.Namespace(code_or_id=code))
    assert "Revoked" in capsys.readouterr().out


def test_seeding_a_person_gives_them_an_owner(tmp_path, capsys, db, as_user, make_user):
    file = tmp_path / "r.yaml"
    file.write_text(resume_yaml("Seed"))
    manage.cmd_seed_person(argparse.Namespace(slug="seed", name="Seed", file=str(file), default=True, owner=None))
    assert "Created person" in capsys.readouterr().out
    admin_id = db.query(models.User).filter(models.User.email == config.DEV_USER_EMAIL).first().id
    with as_user(admin_id) as s:
        assert [p.slug for p in s.query(models.Person).all()] == ["seed"]
    other = make_user("bob@example.com")
    manage.cmd_seed_person(argparse.Namespace(slug="bobs", name="Bobs", file=str(file), default=True, owner="bob@example.com"))
    with as_user(other) as s:
        got = s.query(models.Person).all()
        assert [p.slug for p in got] == ["bobs"] and got[0].is_default is False  # only an administrator's resume can be the default


def test_seeding_for_someone_with_no_account_is_refused(tmp_path, capsys):
    file = tmp_path / "r.yaml"
    file.write_text(resume_yaml("Seed"))
    with pytest.raises(SystemExit):
        manage.cmd_seed_person(argparse.Namespace(slug="x", name="X", file=str(file), default=False, owner="nobody@example.com"))
