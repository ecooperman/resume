"""The suite runs against a throwaway SQLite file built by the real migrations, so it exercises the schema the app ships with
and can never touch the real access.db: DATABASE_URL is set (and the schema built) before the apps are imported, because
app/database.py reads it when it loads. Every table is emptied before each test, and the site/ folder is a temporary one.

Rendering (Playwright for PDFs, python-docx) is replaced by tiny fakes: the tests here are about who may see what, not about
how a resume looks. Nothing may reach the network: requests and the Claude client are replaced by an error unless a test
supplies its own fake."""
import os
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_db_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_db_file.close()
os.environ["DATABASE_URL"] = f"sqlite:///{_db_file.name}"
os.chdir(ROOT)

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402

_cfg = Config(str(ROOT / "alembic.ini"))
_cfg.set_main_option("script_location", str(ROOT / "migrations"))
command.upgrade(_cfg, "head")

import requests  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from shared_python import owned  # noqa: E402
from shared_python import settings as shared_settings  # noqa: E402

from app import admin, config, crud, generate, main, models, render  # noqa: E402
from app.database import Base, SessionLocal, engine  # noqa: E402

YAML = "basics:\n  name: {name}\n  email: {name}@example.com\n  phone: '555-0100'\n  location: Somewhere\nexperience: []\n"


def resume_yaml(name="Pat"):
    return YAML.format(name=name)


def pytest_sessionfinish(session, exitstatus):
    os.unlink(_db_file.name)


@pytest.fixture(autouse=True)
def clean_db():
    """Empty every table, then give the dev user (an administrator locally) an account and run the test inside THEIR scope:
    the app keeps every person's data private, so even a test's own database calls belong to someone. Use `make_user`,
    `as_user` and `client_as` (below) to bring in a second person."""
    with engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            conn.execute(table.delete())
    with owned.unscoped(), SessionLocal() as session:
        dev = models.User(email=config.DEV_USER_EMAIL)
        session.add(dev)
        session.commit()
        dev_id = dev.id
    with owned.scope(dev_id):
        yield


@pytest.fixture(autouse=True)
def forget_last_seen():
    """Who was recorded as last seen is remembered in memory for an hour; the tests empty the database between them but keep ids."""
    from app import access

    access.seen.reset()


@pytest.fixture(autouse=True)
def site_dir(monkeypatch, tmp_path):
    """Built site files go into a temporary folder, never the real site/."""
    folder = tmp_path / "site"
    for module in (config, admin, main):
        monkeypatch.setattr(module, "SITE_DIR", folder)
    return folder


@pytest.fixture(autouse=True)
def fake_rendering(monkeypatch):
    """Pages and downloads are tiny stand-ins that say whose resume they are, so a test can tell whose file it got."""

    def build_tree(data, out_dir):
        Path(out_dir).mkdir(parents=True, exist_ok=True)
        who = (data.get("basics") or {}).get("name", "?")
        shown = (data.get("basics") or {}).get("email", "")
        (Path(out_dir) / "index.html").write_text(f"<html>{who} {shown}</html>")
        (Path(out_dir) / "resume.pdf").write_bytes(f"PDF {who} {shown}".encode())
        (Path(out_dir) / "resume.docx").write_bytes(f"DOCX {who} {shown}".encode())

    def build_pdf(data, out_dir):
        (Path(out_dir) / "resume.pdf").write_bytes(f"PDF {(data.get('basics') or {}).get('name', '?')}".encode())

    def build_docx(data, out_dir):
        (Path(out_dir) / "resume.docx").write_bytes(f"DOCX {(data.get('basics') or {}).get('name', '?')}".encode())

    monkeypatch.setattr(render, "build_tree", build_tree)
    monkeypatch.setattr(render, "build_pdf", build_pdf)
    monkeypatch.setattr(render, "build_docx", build_docx)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError(f"a test tried to reach the network: {args[:2]}")

    monkeypatch.setattr(requests, "request", refuse)
    monkeypatch.setattr(requests, "get", refuse)
    monkeypatch.setattr(requests, "post", refuse)
    monkeypatch.setattr(generate.anthropic, "Anthropic", refuse)  # the real drafting code runs; only the AI call is a stand-in
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", None)
    # Whatever sign-in settings the developer's shell has, tests start local, with the dev user as the administrator.
    for name in (
        "CF_TEAM_DOMAIN", "CF_ACCESS_AUD", "OIDC_ISSUER", "OIDC_JWKS_URL", "OIDC_AUDIENCE", "OIDC_COOKIE", "OIDC_CLIENT_ID",
        "OIDC_CLIENT_SECRET", "OIDC_COOKIE_DOMAIN", "OIDC_RETURN_HOSTS", "OIDC_LOGIN_URL", "OIDC_LOGOUT_RETURN_PATH",
    ):
        monkeypatch.setattr(shared_settings, name, None)
    monkeypatch.setattr(shared_settings, "ENV", "local")
    monkeypatch.delenv("INTERNAL_API_TOKEN", raising=False)


@pytest.fixture(autouse=True)
def fresh_draft_limits():
    from app import drafts, limits, tailoring

    limits.reset()
    drafts.reset()
    tailoring.reset()
    yield
    limits.reset()
    drafts.reset()
    tailoring.reset()


@pytest.fixture()
def make_user():
    """make_user("bob@example.com") -> the new person's id (an account: they have been let in)."""

    def make(email, status=models.USER_ACTIVE):
        with owned.unscoped(), SessionLocal() as session:
            user = models.User(email=email, status=status)
            session.add(user)
            session.commit()
            return user.id

    return make


@pytest.fixture()
def as_user():
    """`with as_user(person_id) as db:` a fresh database session working as that person (never mixes with another)."""
    from contextlib import contextmanager

    @contextmanager
    def work(person_id):
        with owned.scope(person_id), SessionLocal() as session:
            yield session

    return work


@pytest.fixture()
def client_as():
    """client_as("bob@example.com") -> an ADMIN-app client making every request as that person (locally the header says who)."""

    def make(email):
        return TestClient(admin.app, headers={"Cf-Access-Authenticated-User-Email": email})

    return make


@pytest.fixture()
def admin_client():
    return TestClient(admin.app)  # locally the dev user, an administrator


@pytest.fixture()
def public():
    """The public resume site (nobody signs in)."""
    return TestClient(main.app)


@pytest.fixture()
def db():
    session = SessionLocal()
    yield session
    session.close()
