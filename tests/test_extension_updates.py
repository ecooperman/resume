"""The extension says which version it is (a header on every call it makes); the app answers with the newest version posted and whether the installed one is
older, remembers the version on the token so the Jobs page can show a banner, and never claims an update for an extension that did not say."""
import pytest

from app import config, extension
from tests.test_accounts import ADMIN, key, people, person, production  # noqa: F401 - fixtures
from tests.test_extension import make_zip
from tests.test_push import HOST, make_token, posting, pusher
from tests.test_builder import key_storage  # noqa: F401 - autouse fixture
from tests.test_job_scoring import server_key  # noqa: F401 - autouse fixture


@pytest.fixture()
def posted(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "EXTENSION_DIR", tmp_path)
    make_zip(tmp_path, "0.3.0")
    return tmp_path


def ping(token, version=None):
    c = pusher(token)
    if version is not None:
        c.headers["X-Extension-Version"] = version
    return c.get("/api/push/ping")


@pytest.mark.parametrize("text,expected", [("0.3.0", (0, 3, 0)), ("0.10.0", (0, 10, 0)), (" 1.2 ", (1, 2)), ("", None), ("0.3.0-beta", None), ("a.b", None), ("1" * 30, None), (None, None), ("1.2.3.4.5", None)])
def test_only_plain_dotted_numbers_count_as_a_version(text, expected):
    assert extension.parse_version(text) == expected
    assert extension.parse_version("0.10.0") > extension.parse_version("0.9.0")


@pytest.mark.parametrize("installed,update", [("0.2.0", True), ("0.2.9", True), ("0.3.0", False), ("0.3.1", False), ("1.0.0", False), (None, False), ("junk", False)])
def test_an_older_extension_is_told_a_newer_one_is_out_and_everything_else_is_not(posted, person, people, installed, update):
    token = make_token(person("bob@example.com"))["token"]
    got = ping(token, installed).json()["extension"]
    assert got == {"latest": "0.3.0", "update_available": update, "page": "/extension.html"}


def test_with_nothing_posted_there_is_never_an_update(tmp_path, monkeypatch, person, people):
    monkeypatch.setattr(config, "EXTENSION_DIR", tmp_path)
    token = make_token(person("bob@example.com"))["token"]
    assert ping(token, "0.1.0").json()["extension"] == {"latest": None, "update_available": False, "page": "/extension.html"}


def test_the_reply_to_a_push_says_it_too(posted, person, people):
    token = make_token(person("bob@example.com"))["token"]
    c = pusher(token)
    c.headers["X-Extension-Version"] = "0.2.0"
    got = c.post("/api/push/jobs", json={"jobs": [posting(1)], "score": False}).json()
    assert got["created"] == 1 and got["extension"]["update_available"] is True and got["extension"]["latest"] == "0.3.0"


def test_the_token_remembers_the_version_and_the_jobs_page_can_say_it_is_behind(posted, person, people):
    bob = person("bob@example.com")
    old, current = make_token(bob, "Old laptop"), make_token(bob, "New laptop")
    assert all(t["extension_version"] is None and t["update_available"] is False for t in bob.get("/api/tokens").json())  # nothing reported yet: no claim
    ping(old["token"], "0.2.0")
    ping(current["token"], "0.3.0")
    rows = {t["name"]: t for t in bob.get("/api/tokens").json()}
    assert rows["Old laptop"]["extension_version"] == "0.2.0" and rows["Old laptop"]["update_available"] is True and rows["Old laptop"]["extension_latest"] == "0.3.0"
    assert rows["New laptop"]["extension_version"] == "0.3.0" and rows["New laptop"]["update_available"] is False
    ping(old["token"], "0.3.0")  # it was updated
    assert {t["name"]: t["update_available"] for t in bob.get("/api/tokens").json()}["Old laptop"] is False


def test_a_made_up_version_is_never_stored_and_no_header_leaves_the_last_one(posted, person, people):
    bob = person("bob@example.com")
    token = make_token(bob)["token"]
    ping(token, "<script>alert(1)</script>")
    assert bob.get("/api/tokens").json()[0]["extension_version"] is None
    ping(token, "0.2.0")
    ping(token)  # an old extension that sends nothing
    assert bob.get("/api/tokens").json()[0]["extension_version"] == "0.2.0"


def test_one_persons_versions_are_not_visible_to_another(posted, person, people):
    bob, carol = person("bob@example.com"), person("carol@example.com")
    ping(make_token(bob, "Bobs")["token"], "0.2.0")
    assert carol.get("/api/tokens").json() == []


def test_the_preflight_allows_the_version_header_without_a_token(person, people):
    resp = pusher().options("/api/push/ping", headers={"Origin": "chrome-extension://abc", "Access-Control-Request-Method": "GET",
                                                      "Access-Control-Request-Headers": "authorization, x-extension-version"})
    assert resp.status_code == 204 and "x-extension-version" in resp.headers["access-control-allow-headers"]
