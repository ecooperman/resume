"""The Start here page explains the whole process to a new person. It is for anyone with an account, every link on it must go somewhere real, and its wording
must not name the app (the app is likely to be renamed), so it keeps reading correctly afterwards."""
import re

from fastapi.testclient import TestClient

from app import admin
from tests.test_accounts import key, people, person, production  # noqa: F401 - fixtures

PROCESS = ("s-key", "s-resume", "s-extension", "s-jobs", "s-scored", "s-tailor", "s-track")


def page(who):
    resp = who.get("/welcome.html")
    assert resp.status_code == 200, resp.status_code
    return resp.text


def test_anyone_with_an_account_gets_the_page_with_every_step_of_the_process(person, people):
    for email in ("bob@example.com", "carol@example.com"):
        text = page(person(email))
        assert "Start here" in text and "The big picture" in text and "What it costs" in text and "Who can see your things" in text
        for step in PROCESS:
            assert f'id="{step}"' in text, step


def test_nobody_without_an_account_or_signed_out_gets_it(person, people):
    assert person("dana@example.com").get("/welcome.html").status_code == 403
    assert person("eve@example.com").get("/welcome.html").status_code == 403
    signed_out = TestClient(admin.app, base_url="https://resume-admin.example.org", follow_redirects=False)
    assert signed_out.get("/welcome.html").status_code != 200


def test_every_link_to_a_page_of_this_app_works(person, people):
    bob = person("bob@example.com")
    links = set(re.findall(r'href="(/[^"#?]*)"', page(bob)))
    assert {"/build.html", "/jobs.html", "/extension.html"} <= links
    for link in sorted(links):
        assert bob.get(link).status_code == 200, link
    for script in re.findall(r'src="(/[^"?]+)', page(bob)):
        assert bob.get(script).status_code == 200, script


def test_the_wording_does_not_name_the_app_so_a_rename_does_not_date_it(person, people):
    text = re.sub(r"<[^>]+>", " ", page(person("bob@example.com"))).lower()
    assert "resume app" not in text and "jobs app" not in text


def test_the_menu_has_start_here_first_and_a_brand_new_person_is_sent_there_once(person, people):
    nav = person("bob@example.com").get("/nav.js").text
    assert nav.index("Start here") < nav.index("My resume")
    front = person("bob@example.com").get("/people.js").text
    assert "resume-welcome-seen" in front and "/welcome.html" in front
    assert "resume-welcome-seen" in person("bob@example.com").get("/welcome.js").text  # visiting it is what stops the redirect
