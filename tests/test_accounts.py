"""Several people can use the resume admin, and each person's resume is completely private to them.

The tests that matter most are the two-person ones: one person makes a resume, then ANOTHER person with a valid account tries
every route of the app against it (reading, changing, deleting, downloading, pointing a code at it) and must get nothing and
change nothing. Alongside: who can get in at all (default-deny), that no sign-in trick makes one person another, what only an
administrator may do (choose slugs, the default, publishing, access codes, the server's AI key, the Users page), and that
time-management's server-to-server calls act as the administrator and nothing else."""
import re
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from shared_python import auth, owned, settings

from app import access, admin, config, crud, models, notify
from tests.conftest import resume_yaml

ISSUER = "https://login.example.com"
CLIENT = "client-123"
FRONT = "https://dashboard.example.org"
ADMIN = "owner@example.com"
BOB_MARK = "Bob-Secret-Resume"
OWNER_MARK = "Owner-Resume"


@pytest.fixture(scope="module")
def key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture()
def production(monkeypatch, key):
    monkeypatch.setattr(settings, "ENV", "production")
    monkeypatch.setattr(config, "ADMIN_EMAILS", {ADMIN})
    monkeypatch.setattr(settings, "OIDC_ISSUER", ISSUER)
    monkeypatch.setattr(settings, "OIDC_AUDIENCE", CLIENT)
    monkeypatch.setattr(settings, "OIDC_LOGIN_URL", FRONT + "/auth/login")
    monkeypatch.setattr(auth, "_oidc_signing_key", lambda _t: key.public_key())
    sent = []
    monkeypatch.setattr(notify, "tell_let_in", lambda to, inviter: sent.append((to, inviter)))
    return sent


def session(key, email, **kw):
    claims = {"iss": ISSUER, "aud": CLIENT, "exp": int(time.time()) + 300, "email": email, "email_verified": True}
    claims.update(kw)
    return jwt.encode(claims, key, algorithm="RS256")


@pytest.fixture()
def person(key, production):
    def make(email, **kw):
        c = TestClient(admin.app, base_url="https://resume-admin.example.org", follow_redirects=False)
        c.cookies.set("oidc_session", session(key, email, **kw))
        return c

    return make


@pytest.fixture()
def people(make_user):
    """Bob and Carol have accounts; Dana does not; Eve's access was removed."""
    return {"bob": make_user("bob@example.com"), "carol": make_user("carol@example.com"),
            "eve": make_user("eve@example.com", status=models.USER_REMOVED)}


def bobs_resume(bob):
    resp = bob.post("/api/people", json={"name": BOB_MARK, "resume_yaml": resume_yaml("Bob")})
    assert resp.status_code == 200, resp.text
    return resp.json()


def owners_resume(owner, slug="owner"):
    resp = owner.post("/api/people", json={"name": OWNER_MARK, "slug": slug, "resume_yaml": resume_yaml("Owner")})
    assert resp.status_code == 200, resp.text
    return resp.json()


def every_route():
    for shown, operations in admin.app.openapi()["paths"].items():
        for method in operations:
            if method.upper() not in ("HEAD", "OPTIONS"):
                yield method.upper(), shown


def fill(shown, ids):
    return re.sub(r"\{([^}]+)\}", lambda m: str(ids.get(m.group(1), 1)), shown)


# --- who gets in (default-deny) -----------------------------------------------------------------------------------------------


def test_a_person_with_an_account_gets_in(person, people):
    assert person("bob@example.com").get("/api/me").json()["email"] == "bob@example.com"


def test_signed_in_is_not_enough_a_person_nobody_let_in_gets_nothing_from_any_route(person, people):
    dana = person("dana@example.com")
    leaked = []
    for method, shown in every_route():
        resp = dana.request(method, fill(shown, {}), json={} if method in ("POST", "PATCH", "PUT") else None)
        if resp.status_code != 403:
            leaked.append((method, shown, resp.status_code))
    assert not leaked, leaked
    page = dana.get("/")
    assert page.status_code == 403 and "have access yet" in page.text and "dana@example.com" in page.text


def test_a_person_whose_access_was_removed_gets_nothing_and_is_told_so(person, people):
    eve = person("eve@example.com")
    assert eve.get("/api/people").status_code == 403 and eve.get("/api/me").status_code == 403
    assert "access was removed" in eve.get("/").text


def test_nobody_signed_out_gets_anything(person, people):
    signed_out = TestClient(admin.app, base_url="https://resume-admin.example.org", follow_redirects=False)
    answered = []
    for method, shown in every_route():
        resp = signed_out.request(method, fill(shown, {}), json={} if method in ("POST", "PATCH", "PUT") else None)
        if resp.status_code not in (401, 302):
            answered.append((method, shown, resp.status_code))
    for path in ("/", "/build.html", "/build.js", "/codes.html", "/users.html", "/people.js", "/style.css", "/nope"):
        if signed_out.get(path).status_code != 302:
            answered.append(("GET", path))
    assert not answered, answered


def test_the_first_administrator_to_sign_in_gets_an_account_and_a_stranger_posing_as_one_does_not(person, people):
    assert person(ADMIN).get("/api/me").json()["is_admin"] is True
    assert person("owner+x@example.com").get("/api/me").status_code == 403  # a plus alias is a different person
    assert person("Owner@Example.com").get("/api/me").json()["email"] == ADMIN  # case does not matter


# --- no sign-in trick makes one person another --------------------------------------------------------------------------------


def test_a_token_signed_by_anyone_else_is_refused(person, people):
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    forged = TestClient(admin.app, base_url="https://resume-admin.example.org", follow_redirects=False)
    forged.cookies.set("oidc_session", session(other, "bob@example.com"))
    assert forged.get("/api/people").status_code == 401


@pytest.mark.parametrize("kw", [{"aud": "someone-else"}, {"iss": "https://evil.example.com"}, {"email_verified": False}, {"exp": 1}])
def test_a_token_for_the_wrong_app_issuer_unverified_or_expired_is_refused(person, people, kw):
    assert person("bob@example.com", **kw).get("/api/people").status_code == 401


def test_a_claim_of_identity_in_a_header_or_cookie_is_never_trusted(person, people):
    bob = person("bob@example.com")
    bobs_resume(bob)
    sneaky = TestClient(admin.app, base_url="https://resume-admin.example.org", follow_redirects=False)
    for headers, cookies in (({"Cf-Access-Authenticated-User-Email": "bob@example.com"}, {}), ({}, {"dev_user": "bob@example.com"})):
        for name, value in cookies.items():
            sneaky.cookies.set(name, value)
        assert sneaky.get("/api/people", headers=headers).status_code == 401


def test_a_body_or_query_naming_another_person_changes_nothing(person, people):
    bob, carol = person("bob@example.com"), person("carol@example.com")
    mine = bobs_resume(bob)
    resp = carol.post("/api/people?owner_id=%d" % people["bob"], json={"name": "Carol", "resume_yaml": resume_yaml("Carol"), "owner_id": people["bob"]})
    assert resp.status_code == 200
    assert [p["id"] for p in bob.get("/api/people").json()] == [mine["id"]]
    assert [p["name"] for p in carol.get("/api/people").json()] == ["Carol"]


# --- whose resume they see: the two-person tests ---------------------------------------------------------------------------------


def test_each_person_sees_only_their_own_resume(person, people):
    bob, carol, owner = person("bob@example.com"), person("carol@example.com"), person(ADMIN)
    bobs_resume(bob)
    owners_resume(owner)
    assert [p["name"] for p in bob.get("/api/people").json()] == [BOB_MARK]
    assert [p["name"] for p in owner.get("/api/people").json()] == [OWNER_MARK]
    assert carol.get("/api/people").json() == []


def test_another_person_can_neither_read_nor_change_nor_delete_nor_download_anything_through_any_route(person, people):
    bob, carol, owner = person("bob@example.com"), person("carol@example.com"), person(ADMIN)
    mine = bobs_resume(bob)
    before = bob.get(f"/api/people/{mine['id']}").json()
    ids = {"person_id": mine["id"], "code_id": 1, "user_id": people["bob"]}
    bodies = {"PATCH": {"name": "HIJACKED", "resume_yaml": resume_yaml("Hijacked")}, "POST": {"name": "HIJACKED"}, "PUT": {}}
    touched = 0
    for attacker in (carol, owner):
        for method, shown in every_route():
            if "{person_id}" not in shown:
                continue
            resp = attacker.request(method, fill(shown, ids), json=bodies.get(method))
            assert resp.status_code in (403, 404), (method, shown, resp.status_code)
            assert BOB_MARK not in resp.text and "Bob" not in resp.text, (method, shown)
            touched += 1
    assert touched >= 8
    assert bob.get(f"/api/people/{mine['id']}").json() == before


def test_an_administrator_is_just_another_owner_and_cannot_read_a_persons_resume_through_the_app(person, people):
    bob, owner = person("bob@example.com"), person(ADMIN)
    bobs_resume(bob)
    assert owner.get("/api/people").json() == []
    assert BOB_MARK not in owner.get("/").text


def test_a_code_cannot_be_pointed_at_someone_elses_resume(person, people):
    bob, owner = person("bob@example.com"), person(ADMIN)
    mine = bobs_resume(bob)
    assert owner.post("/api/codes", json={"person_id": mine["id"], "label": "sneaky"}).status_code == 404
    assert owner.get("/api/codes").json() == []


def test_removing_a_person_locks_them_out_at_once_and_keeps_their_resume_private_until_deleted(person, people):
    bob, owner = person("bob@example.com"), person(ADMIN)
    bobs_resume(bob)
    assert owner.patch(f"/api/users/{people['bob']}", json={"status": "removed"}).status_code == 200
    assert bob.get("/api/people").status_code == 403
    assert owner.get("/api/people").json() == []  # still not the administrator's to read
    owner.patch(f"/api/users/{people['bob']}", json={"status": "active"})
    assert [p["name"] for p in bob.get("/api/people").json()] == [BOB_MARK]


# --- an ordinary account: one private resume, nothing public ------------------------------------------------------------------------


def test_an_ordinary_account_gets_one_private_resume_with_a_slug_nobody_chose(person, people, site_dir):
    bob = person("bob@example.com")
    made = bobs_resume(bob)
    assert re.fullmatch(r"u\d+-[0-9a-f]{10}", made["slug"]) and made["published"] is False and made["is_default"] is False
    assert not site_dir.exists()  # nothing is built for the public site
    second = bob.post("/api/people", json={"name": "Another", "resume_yaml": resume_yaml("Another")})
    assert second.status_code == 409 and "already have a resume" in second.json()["detail"]


@pytest.mark.parametrize("extra", [{"slug": "mine"}, {"is_default": True}, {"published": True}])
def test_an_ordinary_account_cannot_choose_a_slug_set_the_default_or_publish(person, people, extra):
    resp = person("bob@example.com").post("/api/people", json={"name": "Bob", "resume_yaml": resume_yaml("Bob"), **extra})
    assert resp.status_code == 403 and "Only an administrator" in resp.json()["detail"]
    assert person("bob@example.com").get("/api/people").json() == []


@pytest.mark.parametrize("change", [{"is_default": True}, {"published": True}])
def test_an_ordinary_account_cannot_publish_or_make_itself_the_default_later(person, people, change, site_dir):
    bob = person("bob@example.com")
    mine = bobs_resume(bob)
    assert bob.patch(f"/api/people/{mine['id']}", json=change).status_code == 403
    after = bob.get(f"/api/people/{mine['id']}").json()
    assert after["published"] is False and after["is_default"] is False and not site_dir.exists()


def test_an_ordinary_account_can_edit_download_and_delete_its_own_resume(person, people):
    bob = person("bob@example.com")
    mine = bobs_resume(bob)
    edited = bob.patch(f"/api/people/{mine['id']}", json={"name": "Bob B", "resume_yaml": resume_yaml("Bobby")}).json()
    assert edited["name"] == "Bob B" and "Bobby" in edited["resume_yaml"]
    pdf = bob.get(f"/api/people/{mine['id']}/download?format=pdf")
    assert pdf.status_code == 200 and pdf.content.startswith(b"PDF Bobby") and "attachment" in pdf.headers["content-disposition"]
    assert pdf.headers["cache-control"] == "private, no-store"
    assert bob.get(f"/api/people/{mine['id']}/download?format=docx").content.startswith(b"DOCX Bobby")
    assert bob.get(f"/api/people/{mine['id']}/download?format=exe").status_code == 422
    assert bob.delete(f"/api/people/{mine['id']}").status_code == 200
    assert bob.get("/api/people").json() == []
    assert bob.post("/api/people", json={"name": "Again", "resume_yaml": resume_yaml("Again")}).status_code == 200


def test_a_resume_must_be_valid_yaml_with_basics(person, people):
    bob = person("bob@example.com")
    for bad in ("not: [valid", "just: a mapping"):
        assert bob.post("/api/people", json={"name": "Bob", "resume_yaml": bad}).status_code == 400
    assert bob.get("/api/people").json() == []


# --- what an administrator may do that others may not --------------------------------------------------------------------------------


def test_only_an_administrator_may_use_codes_the_users_page_and_the_servers_ai_key(person, people, monkeypatch):
    bob = person("bob@example.com")
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "server-key")
    for method, path, body in (
        ("GET", "/api/codes", None), ("POST", "/api/codes", {"label": "x"}), ("POST", "/api/codes/1/revoke", None),
        ("GET", "/api/users", None), ("POST", "/api/users", {"email": "z@example.com"}), ("PATCH", f"/api/users/{people['carol']}", {"status": "removed"}),
        ("DELETE", f"/api/users/{people['eve']}/data", None), ("GET", "/api/users/suggestions", None),
    ):
        assert bob.request(method, path, json=body).status_code == 403, (method, path)
    gen = bob.post("/api/generate-resume", files={"sources": ("a.txt", b"hello")})
    assert gen.status_code == 400 and "your own Anthropic key" in gen.json()["detail"]  # it would spend the server's key


def test_ordinary_accounts_are_sent_back_to_their_own_resume_from_the_administrators_pages(person, people):
    bob, owner = person("bob@example.com"), person(ADMIN)
    for path in ("/codes.html", "/users.html"):
        resp = bob.get(path)
        assert resp.status_code == 302 and resp.headers["location"] == "/"
        assert owner.get(path).status_code == 200
    assert bob.get("/").status_code == 200


def test_an_administrator_chooses_slugs_publishes_and_sets_the_default(person, people, site_dir):
    owner = person(ADMIN)
    made = owners_resume(owner)
    assert made["slug"] == "owner" and made["published"] is True and made["is_default"] is True
    assert (site_dir / "public" / "owner" / "index.html").exists() and (site_dir / "private" / "owner" / "index.html").exists()
    assert owner.post("/api/people", json={"name": "No slug", "resume_yaml": resume_yaml("N")}).status_code == 400
    second = owner.post("/api/people", json={"name": "Second", "slug": "second", "resume_yaml": resume_yaml("Second"), "published": False}).json()
    assert second["published"] is False and not (site_dir / "public" / "second").exists()
    assert owner.patch(f"/api/people/{second['id']}", json={"published": True}).json()["published"] is True
    assert (site_dir / "public" / "second" / "index.html").exists()
    assert owner.patch(f"/api/people/{second['id']}", json={"published": False}).json()["published"] is False
    assert not (site_dir / "public" / "second").exists() and not (site_dir / "private" / "second").exists()
    assert owner.patch(f"/api/people/{second['id']}", json={"is_default": True}).json() == owner.get(f"/api/people/{second['id']}").json()
    assert owner.get(f"/api/people/{second['id']}").json()["published"] is True  # the default is always public
    assert owner.get(f"/api/people/{made['id']}").json()["is_default"] is False


def test_a_slug_in_use_by_anyone_cannot_be_used_again_without_saying_whose(person, people):
    bob, owner = person("bob@example.com"), person(ADMIN)
    taken = bobs_resume(bob)["slug"]
    resp = owner.post("/api/people", json={"name": "Clash", "slug": taken, "resume_yaml": resume_yaml("Clash")})
    assert resp.status_code == 400 and "already exists" in resp.json()["detail"] and "Bob" not in resp.text


def test_two_people_may_have_the_same_name(person, people):
    for email in ("bob@example.com", "carol@example.com"):
        assert person(email).post("/api/people", json={"name": "Sam", "resume_yaml": resume_yaml("Sam")}).status_code == 200


# --- the data from before accounts ---------------------------------------------------------------------------------------------------


def test_the_first_administrator_takes_over_the_resumes_that_existed_before_accounts_and_nobody_else_does(person, people, make_user, as_user):
    legacy = make_user(crud.LEGACY_OWNER_EMAIL)
    with as_user(legacy) as db:
        crud.create_person(db, "evan", "Evan", resume_yaml("Evan"), is_default=True)
    assert person("bob@example.com").get("/api/people").json() == []
    owner = person(ADMIN)
    assert [p["slug"] for p in owner.get("/api/people").json()] == ["evan"]
    with owned.unscoped():
        from app.database import SessionLocal

        with SessionLocal() as db:
            assert crud.get_user_by_email(db, crud.LEGACY_OWNER_EMAIL) is None  # the placeholder became the administrator
    assert person("carol@example.com").get("/api/people").json() == []


# --- time-management's server-to-server calls -------------------------------------------------------------------------------------


@pytest.fixture()
def with_token(monkeypatch):
    monkeypatch.setenv("INTERNAL_API_TOKEN", "internal-secret")
    return {"X-Internal-Token": "internal-secret"}


def internal(headers=None):
    return TestClient(admin.app, base_url="https://resume-admin.example.org", follow_redirects=False, headers=headers or {})


def test_the_internal_token_acts_as_the_administrator_and_sees_only_the_administrators_resumes(person, people, with_token):
    owners_resume(person(ADMIN))
    bobs_resume(person("bob@example.com"))
    listed = internal(with_token).get("/api/resume-people")
    assert listed.status_code == 200 and [p["slug"] for p in listed.json()["people"]] == ["owner"]
    data = internal(with_token).get("/api/resume-data")
    assert data.status_code == 200 and data.json()["basics"]["name"] == "Owner"
    assert "Bob" not in listed.text + data.text


def test_the_internal_token_cannot_reach_a_persons_resume_by_slug(person, people, with_token):
    slug = bobs_resume(person("bob@example.com"))["slug"]
    owners_resume(person(ADMIN))
    assert internal(with_token).get("/api/resume-data", params={"person": slug}).status_code == 404


def test_the_internal_token_renders_what_it_is_given(person, people, with_token):
    owners_resume(person(ADMIN))
    pdf = internal(with_token).post("/api/render", json={"data": {"basics": {"name": "Tailored"}}, "format": "pdf"})
    assert pdf.status_code == 200 and pdf.content == b"PDF Tailored"


@pytest.mark.parametrize("method,path", [
    ("GET", "/api/people"), ("POST", "/api/people"), ("GET", "/api/users"), ("GET", "/api/codes"), ("POST", "/api/generate-resume"),
    ("GET", "/api/me"), ("GET", "/"), ("DELETE", "/api/people/1"),
])
def test_the_internal_token_opens_nothing_but_the_four_agreed_calls(person, people, with_token, method, path):
    owners_resume(person(ADMIN))
    assert internal(with_token).request(method, path, json={}).status_code in (401, 302, 403)


def test_a_wrong_or_missing_token_or_none_configured_opens_nothing(person, people, monkeypatch):
    owners_resume(person(ADMIN))
    assert internal().get("/api/resume-people").status_code == 401
    monkeypatch.setenv("INTERNAL_API_TOKEN", "internal-secret")
    assert internal({"X-Internal-Token": "wrong"}).get("/api/resume-people").status_code == 401
    monkeypatch.delenv("INTERNAL_API_TOKEN")
    assert internal({"X-Internal-Token": ""}).get("/api/resume-people").status_code == 401
    assert internal({"X-Internal-Token": "internal-secret"}).get("/api/resume-people").status_code == 401


def test_a_signed_in_person_still_needs_the_token_for_the_internal_calls(person, people, with_token):
    bob = person("bob@example.com")
    assert bob.get("/api/resume-people").status_code == 403
    assert bob.get("/api/resume-data").status_code == 403
    assert person(ADMIN).get("/api/resume-people").status_code == 403  # even the administrator, without it


# --- the rest of the gate ---------------------------------------------------------------------------------------------------------


def test_nothing_in_the_admin_app_is_public():
    assert access.PUBLIC_PATHS == ()


def test_every_route_that_changes_something_refuses_a_request_from_another_app_or_site(person, people):
    bob = person("bob@example.com")
    tried = 0
    for method, shown in every_route():
        if method not in ("POST", "PUT", "PATCH", "DELETE"):
            continue
        for site in ("same-site", "cross-site"):
            resp = bob.request(method, fill(shown, {}), json={}, headers={"Sec-Fetch-Site": site, "Origin": "https://lists.example.org"})
            assert resp.status_code == 403 and "didn't come from this app" in resp.text, (method, shown, site)
            tried += 1
    assert tried > 8
    assert bob.get("/api/people").json() == []


def test_me_says_who_is_signed_in_and_where_log_out_goes(person, people):
    me = person("bob@example.com").get("/api/me").json()
    assert me["email"] == "bob@example.com" and me["logout_url"] == FRONT + "/auth/logout" and me["is_admin"] is False
    assert person(ADMIN).get("/api/me").json()["is_admin"] is True


def test_locally_the_dev_user_is_an_administrator_with_an_account(admin_client, client_as, make_user):
    assert admin_client.get("/api/me").json()["is_admin"] is True
    make_user("bob@example.com")
    assert client_as("bob@example.com").get("/api/me").json()["is_admin"] is False
    assert client_as("nobody@example.com").get("/api/me").status_code == 403


def test_no_code_in_the_app_bypasses_the_owner_filter_except_where_it_says_so():
    owned.assert_no_raw_sql(["app"])


def test_every_table_of_a_persons_data_has_an_owner_and_the_filter_covers_it():
    for model in (models.Person, models.AccessCode):
        assert issubclass(model, owned.OwnedMixin) and not model.__table__.c.owner_id.nullable
