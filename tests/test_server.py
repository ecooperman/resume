"""app/server.py runs the public resume site and the admin app in one process, telling them apart by the Host header. What matters: the
admin app is reachable only on its own hostnames and stays exactly as locked down; nothing of the admin app can be reached on a public
hostname (an unknown host lands on the PUBLIC app, never the admin one); and each app still starts and stops as it did alone."""
from contextlib import asynccontextmanager

import pytest
from fastapi.testclient import TestClient

from app import admin, config, main, server
from tests.test_accounts import key, production  # noqa: F401 - fixtures

ADMIN = "admin.example.org"
PUBLIC = "resume.example.org"


@pytest.fixture(autouse=True)
def hosts(monkeypatch):
    monkeypatch.setattr(config, "ADMIN_HOSTS", {ADMIN, "resume-admin.localhost"})


def client(host, **kw):
    return TestClient(server.app, base_url=f"https://{host}", follow_redirects=False, **kw)


@pytest.mark.parametrize("header,expected", [
    ("admin.example.org", "admin.example.org"),
    ("ADMIN.Example.ORG:443", "admin.example.org"),
    ("admin.example.org.", "admin.example.org"),
    ("resume-admin.localhost:8040", "resume-admin.localhost"),
    ("[::1]:8040", "::1"),
    ("127.0.0.1:8040", "127.0.0.1"),
])
def test_the_hostname_is_read_whatever_the_case_port_or_trailing_dot(header, expected):
    assert server.request_host({"headers": [(b"host", header.encode())]}) == expected
    assert server.request_host({"headers": []}) == ""


def test_the_admin_app_answers_only_on_its_own_hostnames_and_still_asks_for_a_sign_in(production):
    signed_out = client(ADMIN).get("/api/people")
    assert signed_out.status_code in (302, 401, 403), signed_out.status_code  # the admin app's own gate, exactly as when it had its own port
    for header in ("ADMIN.example.org:443", "admin.example.org.", "resume-admin.localhost:8040"):
        assert client(header).get("/api/people", headers={"host": header}).status_code in (302, 401, 403), header


@pytest.mark.parametrize("host", [PUBLIC, "127.0.0.1:8040", "localhost:8040", "evil.example.com", "admin.example.org.evil.com"])
def test_every_other_host_gets_the_public_site_which_has_none_of_the_admin_routes(host):
    for path in ("/api/people", "/api/me", "/build.html", "/api/generate-resume/status", "/api/users", "/api/codes"):
        assert client(PUBLIC).get(path, headers={"host": host}).status_code == 404, (host, path)


def test_no_admin_only_route_can_be_reached_on_a_public_hostname():
    """A sweep of every plain path the admin app has that the public app does not: none may answer on a public host."""
    public_paths = {r.path for r in main.app.routes}
    only_admin = sorted({r.path for r in admin.app.routes if getattr(r, "methods", None) and "{" not in r.path and r.path not in public_paths})
    assert len(only_admin) > 20, only_admin  # the sweep really covered the admin app
    c = client(PUBLIC)
    for path in only_admin:
        for method in ("GET", "POST", "DELETE"):
            assert c.request(method, path).status_code in (404, 405), (method, path)


def test_the_public_site_never_asks_anyone_to_sign_in():
    through_server = client(PUBLIC).get("/")
    alone = TestClient(main.app, follow_redirects=False).get("/")
    assert through_server.status_code == alone.status_code and "location" not in through_server.headers


def test_both_apps_start_and_stop_as_they_would_alone(monkeypatch):
    seen = []

    def recorder(name):
        @asynccontextmanager
        async def lifespan(_app):
            seen.append(f"{name} up")
            yield
            seen.append(f"{name} down")

        return lifespan

    monkeypatch.setattr(main.app.router, "lifespan_context", recorder("public"))
    monkeypatch.setattr(admin.app.router, "lifespan_context", recorder("admin"))
    with TestClient(server.app):
        assert sorted(seen) == ["admin up", "public up"]
    assert sorted(seen) == ["admin down", "admin up", "public down", "public up"]
