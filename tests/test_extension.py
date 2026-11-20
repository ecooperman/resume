"""The Chrome extension's install page and download: the newest zip in extension/ is what is served (by version number, not by name), only
signed-in people can get it, and the zip we actually ship is whole (its manifest agrees with its name and every file the extension loads is in it)."""
import io
import re
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import admin, config, extension
from tests.test_accounts import key, people, person, production  # noqa: F401 - fixtures

HOST = "https://resume-admin.example.org"


def make_zip(folder: Path, version: str, marker: str = "x"):
    path = folder / f"job-scoring-extension-{version}.zip"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("manifest.json", f'{{"version": "{version}"}}')
        z.writestr("marker.txt", marker)
    return path


@pytest.fixture()
def folder(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "EXTENSION_DIR", tmp_path)
    return tmp_path


def test_the_highest_version_wins_by_number_not_by_name(folder, person, people):
    make_zip(folder, "0.9.0", "old"), make_zip(folder, "0.10.0", "newest"), make_zip(folder, "0.2.1", "mid")
    bob = person("bob@example.com")
    info = bob.get("/api/extension").json()
    assert info["available"] is True and info["version"] == "0.10.0" and info["filename"] == "job-scoring-extension-0.10.0.zip"
    got = bob.get(info["download_url"])
    assert got.status_code == 200 and got.headers["content-type"] == "application/zip"
    assert 'filename="job-scoring-extension-0.10.0.zip"' in got.headers["content-disposition"] and got.headers["cache-control"] == "no-store"
    assert zipfile.ZipFile(io.BytesIO(got.content)).read("marker.txt") == b"newest"


def test_only_release_named_files_count(folder, person, people):
    make_zip(folder, "0.3.0")
    (folder / "job-scoring-extension-9.9.9.zip.bak").write_text("no")
    (folder / "notes.zip").write_text("no")
    (folder / "job-scoring-extension-7.0.0.zip").mkdir()  # a folder with a release's name is not a file
    (folder / "job-scoring-extension-latest.zip").write_text("no")
    bob = person("bob@example.com")
    assert bob.get("/api/extension").json()["version"] == "0.3.0"


def test_with_nothing_posted_the_page_says_so_and_the_download_is_a_404(folder, person, people):
    bob = person("bob@example.com")
    assert bob.get("/api/extension").json() == {"available": False}
    assert bob.get("/extension/download").status_code == 404
    assert extension.newest() is None


def test_a_missing_folder_is_the_same_as_an_empty_one(tmp_path, monkeypatch, person, people):
    monkeypatch.setattr(config, "EXTENSION_DIR", tmp_path / "does-not-exist")
    assert person("bob@example.com").get("/api/extension").json() == {"available": False}


def test_signed_out_visitors_get_nothing(folder, production):
    make_zip(folder, "0.2.0")
    out = TestClient(admin.app, base_url=HOST, follow_redirects=False)
    for path in ("/api/extension", "/extension/download", "/extension.html"):
        assert out.get(path).status_code in (302, 401, 403), path


def test_the_install_page_tells_people_how_and_warns_about_linkedin(folder, person, people):
    page = person("bob@example.com").get("/extension.html")
    assert page.status_code == 200
    text = re.sub(r"<[^>]+>", " ", page.text)
    for needed in ("Developer mode", "Load unpacked", "manifest.json", "Connect the Chrome extension", "Test connection", "LinkedIn's terms", "one page at a time"):
        assert needed in text, needed
    assert "/extension.js" in page.text


def test_the_jobs_page_points_to_the_install_page(person, people):
    assert 'href="/extension.html"' in person("bob@example.com").get("/jobs.html").text


# --- the zip we actually ship -------------------------------------------------------------------------------------------------------------


def shipped():
    found = list((Path(config.__file__).parent.parent / "extension").glob("job-scoring-extension-*.zip"))
    assert found, "the extension zip is not in resume/extension/ (run the extension repo's package.py)"
    return found


def test_there_is_exactly_one_shipped_zip_and_it_is_whole():
    zips = shipped()
    assert len(zips) == 1, [z.name for z in zips]  # older ones are removed by package.py
    z = zipfile.ZipFile(zips[0])
    names = set(z.namelist())
    manifest = __import__("json").loads(z.read("manifest.json"))
    assert zips[0].name == f"job-scoring-extension-{manifest['version']}.zip"  # the name and the manifest agree
    assert not [n for n in names if n.startswith(".git") or n.endswith(".DS_Store") or n.endswith(".py") or "venv" in n]
    needed = {manifest["background"]["service_worker"], manifest["action"]["default_popup"], manifest["options_page"]}
    # every script the popup injects into pages must be in the zip, or that button silently does nothing
    needed |= set(re.findall(r'"((?:lib|content)/[^"]+\.js)"', z.read("popup/popup.js").decode()))
    assert needed <= names, sorted(needed - names)
    assert "lib/jobsApp.js" in names and zipfile.ZipFile(zips[0]).testzip() is None
