"""The suite runs against a throwaway database, and the real migrations build the schema the models describe. The ownership
migration is also tried on data shaped like the real thing (resumes and codes from before accounts)."""
import os
import sqlite3
from pathlib import Path

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine

from app import database

ROOT = Path(__file__).resolve().parent.parent
BEFORE = "ad0a7ca7e069"


def config():
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    return cfg


def migrate(url, action, revision):
    os.environ["DATABASE_URL"] = url  # migrations/env.py reads it
    try:
        getattr(command, action)(config(), revision)
    finally:
        os.environ["DATABASE_URL"] = str(database.DATABASE_URL)


def test_the_suite_never_points_at_the_real_database():
    assert str(database.engine.url) == database.DATABASE_URL
    assert "access.db" not in database.DATABASE_URL.replace("tmp", "")


def test_migrations_build_what_the_models_describe(tmp_path):
    url = f"sqlite:///{tmp_path / 'migrated.db'}"
    migrate(url, "upgrade", "head")
    with create_engine(url).connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), database.Base.metadata)
    assert diff == []


def old_database(tmp_path):
    path = tmp_path / "old.db"
    url = f"sqlite:///{path}"
    migrate(url, "upgrade", BEFORE)
    connection = sqlite3.connect(path)
    for slug, name, default in (("evan", "Evan", 1), ("rach", "Rach", 0)):
        connection.execute(
            "INSERT INTO people (slug, name, resume_yaml, is_default, created_at, updated_at) VALUES (?, ?, 'basics: {}', ?, '2026-01-01', '2026-01-01')",
            (slug, name, default),
        )
    connection.execute("INSERT INTO access_codes (code, label, person_id, created_at, use_count) VALUES ('c1', 'one', 1, '2026-01-01', 4)")
    connection.execute("INSERT INTO access_codes (code, label, person_id, created_at, use_count) VALUES ('c2', 'orphan', NULL, '2026-01-01', 0)")
    connection.commit()
    connection.close()
    return path, url


def test_existing_resumes_and_codes_are_kept_published_and_parked_on_the_placeholder(tmp_path):
    path, url = old_database(tmp_path)
    migrate(url, "upgrade", "head")
    c = sqlite3.connect(path)
    legacy = c.execute("SELECT id FROM users WHERE email = 'legacy-owner@resume.invalid'").fetchone()[0]
    assert c.execute("SELECT slug, is_default, published, owner_id FROM people ORDER BY id").fetchall() == [("evan", 1, 1, legacy), ("rach", 0, 1, legacy)]
    assert c.execute("SELECT code, person_id, owner_id, use_count FROM access_codes ORDER BY id").fetchall() == [("c1", 1, legacy, 4), ("c2", None, legacy, 0)]
    assert c.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1
    c.close()


def test_the_first_administrator_inherits_them_and_a_stranger_does_not(tmp_path, monkeypatch):
    path, url = old_database(tmp_path)
    migrate(url, "upgrade", "head")
    from sqlalchemy.orm import sessionmaker
    from shared_python import owned

    engine = create_engine(url)
    Session = sessionmaker(bind=engine)
    from app import crud, models

    with owned.unscoped(), Session() as db:
        admin = crud.adopt_or_create_admin(db, "Owner@Example.com")
        stranger = crud.add_user(db, "bob@example.com")[0]
        admin_id, stranger_id = admin.id, stranger.id
    with owned.scope(stranger_id), Session() as db:
        assert db.query(models.Person).count() == 0 and db.query(models.AccessCode).count() == 0
    with owned.scope(admin_id), Session() as db:
        assert sorted(p.slug for p in db.query(models.Person).all()) == ["evan", "rach"]
        assert db.query(models.AccessCode).count() == 2


def test_downgrade_and_upgrade_again_keeps_everything(tmp_path):
    path, url = old_database(tmp_path)
    migrate(url, "upgrade", "head")
    migrate(url, "downgrade", BEFORE)
    c = sqlite3.connect(path)
    assert c.execute("SELECT COUNT(*) FROM people").fetchone()[0] == 2 and c.execute("SELECT COUNT(*) FROM access_codes").fetchone()[0] == 2
    assert "users" not in [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
    c.close()
    migrate(url, "upgrade", "head")
    c = sqlite3.connect(path)
    assert c.execute("SELECT COUNT(*) FROM people WHERE published = 1").fetchone()[0] == 2
    c.close()
