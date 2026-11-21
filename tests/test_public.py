"""The public resume site has no sign-in on purpose, and ordinary people's resumes share its database. So the rule is strict:
only a resume an administrator PUBLISHED can ever be shown here, by name, by default, or through an access code, and the
public page never shows contact details. These tests plant a private resume and try every way to reach it."""
import re
from datetime import timedelta

import pytest

from app import clock, crud, models
from tests.conftest import resume_yaml


def make(as_user, user_id, slug, name, *, published, default=False, email_name=None):
    """A resume for `user_id` with its trees built the way the admin app builds them (only if published)."""
    from app import admin

    with as_user(user_id) as db:
        person = crud.create_person(db, slug, name, resume_yaml(email_name or name), is_default=default)
        if published:
            person = crud.update_person(db, person.id, published=True)
        admin._rebuild_person_site(person)
        return person.id


@pytest.fixture()
def world(make_user, as_user, db):
    """An administrator with a published default ('evan') and a published second ('rach'); Bob with a private resume."""
    from app import config

    admin_id = db.query(models.User).filter(models.User.email == config.DEV_USER_EMAIL).first().id
    bob = make_user("bob@example.com")
    return {"admin_id": admin_id, "bob": bob}


def build_world(as_user, world):
    ids = {
        "evan": make(as_user, world["admin_id"], "evan", "Evan", published=True, default=True),
        "rach": make(as_user, world["admin_id"], "rach", "Rach", published=True),
        "draft": make(as_user, world["admin_id"], "draft", "Draftie", published=False),
        "bob": make(as_user, world["bob"], "u9-0123456789", "Bobby", published=False),
    }
    return ids


def test_the_default_published_person_is_the_front_page_without_contact_details(public, as_user, world):
    build_world(as_user, world)
    page = public.get("/")
    assert page.status_code == 200 and "Evan" in page.text and "example.com" not in page.text and "555" not in page.text


def test_a_published_person_is_found_by_slug(public, as_user, world):
    build_world(as_user, world)
    assert "Rach" in public.get("/", params={"person": "rach"}).text


@pytest.mark.parametrize("path", ["/", "/resume.pdf", "/resume.docx"])
def test_a_person_nobody_published_is_a_404_by_slug_whatever_the_page(public, as_user, world, path):
    build_world(as_user, world)
    for slug in ("draft", "u9-0123456789"):
        resp = public.get(path, params={"person": slug})
        assert resp.status_code == 404, (path, slug)
        assert "Bobby" not in resp.text and "Draftie" not in resp.text


def test_even_planted_files_are_never_served_for_someone_unpublished(public, as_user, world, site_dir):
    ids = build_world(as_user, world)
    for tree in ("public", "private"):
        folder = site_dir / tree / "u9-0123456789"
        folder.mkdir(parents=True)
        (folder / "index.html").write_text("BOBBY-SECRET-PAGE")
    assert public.get("/", params={"person": "u9-0123456789"}).status_code == 404
    assert "BOBBY" not in public.get("/", params={"person": "u9-0123456789"}).text
    assert ids["bob"]


def test_an_unknown_slug_and_a_private_one_look_exactly_alike(public, as_user, world):
    build_world(as_user, world)
    unknown, private = public.get("/", params={"person": "nobody-here"}), public.get("/", params={"person": "draft"})
    assert (unknown.status_code, unknown.json()) == (private.status_code, private.json())


def test_with_no_published_default_the_front_page_is_a_404(public, as_user, world):
    make(as_user, world["admin_id"], "evan", "Evan", published=False)
    assert public.get("/").status_code == 404


def test_a_default_that_is_not_published_is_never_shown(public, as_user, world, db):
    ids = build_world(as_user, world)
    with as_user(world["admin_id"]) as s:
        s.query(models.Person).filter(models.Person.id == ids["evan"]).update({"published": False})
        s.commit()
    assert public.get("/").status_code == 404


def code_for(as_user, owner_id, person_id, **kw):
    with as_user(owner_id) as db:
        return crud.generate_code(db, label="recruiter", person_id=person_id, **kw).code


def test_a_valid_code_unlocks_the_private_page_and_downloads_of_a_published_person(public, as_user, world):
    ids = build_world(as_user, world)
    code = code_for(as_user, world["admin_id"], ids["rach"])
    page = public.get("/", params={"code": code})
    assert page.status_code == 200 and "Rach" in page.text and "Rach@example.com" in page.text
    pdf = public.get("/resume.pdf", params={"code": code})
    assert pdf.content == b"PDF Rach Rach@example.com" and "Rach_Resume.pdf" in pdf.headers["content-disposition"]
    assert public.get("/resume.docx", params={"code": code}).content.startswith(b"DOCX Rach")


def test_using_a_code_is_counted_even_with_nobody_signed_in(public, as_user, world, db):
    ids = build_world(as_user, world)
    code = code_for(as_user, world["admin_id"], ids["rach"])
    for _ in range(3):
        public.get("/", params={"code": code})
    from shared_python import owned

    with owned.unscoped():
        row = db.query(models.AccessCode).filter(models.AccessCode.code == code).first()
        assert row.use_count == 3 and row.last_used_at is not None


def test_a_code_for_a_person_nobody_published_shows_nothing_of_theirs(public, as_user, world):
    ids = build_world(as_user, world)
    code = code_for(as_user, world["admin_id"], ids["draft"])
    for path in ("/", "/resume.pdf", "/resume.docx"):
        resp = public.get(path, params={"code": code})
        assert "Draftie" not in resp.text and b"Draftie" not in resp.content
        assert "Draftie@example.com" not in resp.text


def test_a_code_made_for_a_private_persons_resume_cannot_unlock_it(public, as_user, world):
    ids = build_world(as_user, world)
    # Not creatable through the app (administrators cannot even see this resume); forced here to prove the site refuses anyway.
    code = code_for(as_user, world["bob"], ids["bob"])
    for path in ("/", "/resume.pdf", "/resume.docx"):
        resp = public.get(path, params={"code": code, "person": "u9-0123456789"})
        assert resp.status_code == 404 and b"Bobby" not in resp.content


@pytest.mark.parametrize("path", ["/", "/resume.pdf"])
def test_revoked_expired_and_made_up_codes_fall_back_to_the_public_page(public, as_user, world, db, path):
    ids = build_world(as_user, world)
    revoked = code_for(as_user, world["admin_id"], ids["rach"])
    expired = code_for(as_user, world["admin_id"], ids["rach"])
    with as_user(world["admin_id"]) as s:
        crud.revoke_code(s, revoked)
        row = s.query(models.AccessCode).filter(models.AccessCode.code == expired).first()
        row.expires_at = clock.utcnow() - timedelta(days=1)
        s.commit()
    for code in (revoked, expired, "made-up"):
        resp = public.get(path, params={"code": code})
        assert resp.status_code == 200 and b"Rach@example.com" not in resp.content and b"555" not in resp.content


def test_every_answer_here_is_marked_not_to_be_cached(public, as_user, world):
    build_world(as_user, world)
    for path in ("/", "/resume.pdf", "/resume.docx"):
        assert public.get(path).headers["cache-control"] == "private, no-store"


def test_a_slug_cannot_reach_other_files_on_the_server(public, as_user, world):
    build_world(as_user, world)
    for slug in ("../private/evan", "..%2Fprivate%2Fevan", "evan/../../app", "/etc/passwd", "evan\x00"):
        resp = public.get("/", params={"person": slug})
        assert resp.status_code == 404, slug


def test_only_the_three_pages_exist_here_and_none_of_them_changes_anything(public):
    assert public.post("/", json={}).status_code == 405
    assert public.get("/api/people").status_code == 404
    assert public.get("/api/users").status_code == 404


def test_public_pages_carry_the_tab_icon_from_the_production_host_whatever_the_environment():
    import yaml
    from app import config, render
    data = yaml.safe_load((config.ROOT / "example-resume.yaml").read_text()) if hasattr(config, "ROOT") else yaml.safe_load(open("example-resume.yaml"))
    for html in (render.render_html(data, show_download_bar=False), render.render_cover_letter_html(data["basics"], "Dear team,\n\nHello.")):
        assert 'href="https://static.evancooperman.com/logo.svg"' in html and 'href="https://static.evancooperman.com/favicon.png"' in html
        assert "127.0.0.1" not in html  # never the local host, which would be baked into the built site
