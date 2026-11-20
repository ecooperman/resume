"""The job-scoring Chrome extension's download. The extension is built and versioned in its own repo (chrome-extensions/job-application-chrome-extension,
whose package.py writes job-scoring-extension-<version>.zip); the zip is committed into this repo's extension/ folder and this serves the NEWEST one
on the install page (/extension.html). Only signed-in people can reach it, like everything else in the admin app. Nothing a request says picks a
file: the name is always chosen here, from what is in that folder, so there is no path to steer."""
import re
from pathlib import Path
from typing import Optional, Tuple

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from . import config

router = APIRouter(tags=["extension"])

_NAME = re.compile(r"^job-scoring-extension-(\d+(?:\.\d+){0,3})\.zip$")


def newest() -> Optional[Tuple[str, Path]]:
    """(version, path) of the highest-numbered zip in the folder (0.10.0 beats 0.9.0), or None. Anything not named like a release is ignored."""
    found = []
    folder = Path(config.EXTENSION_DIR)
    if folder.is_dir():
        for path in folder.iterdir():
            m = _NAME.match(path.name)
            if m and path.is_file():
                found.append((tuple(int(part) for part in m.group(1).split(".")), m.group(1), path))
    if not found:
        return None
    _, version, path = max(found)
    return version, path


_VERSION = re.compile(r"^\d+(?:\.\d+){0,3}$")


def parse_version(text) -> Optional[Tuple[int, ...]]:
    """A version such as "0.10.0" as numbers that compare properly (0.10.0 is newer than 0.9.0), or None for anything else. The text may come from a request header,
    so only plain dotted numbers are accepted."""
    if not isinstance(text, str) or len(text) > 20 or not _VERSION.match(text.strip()):
        return None
    return tuple(int(part) for part in text.strip().split("."))


def update_info(installed: Optional[str]) -> dict:
    """What an extension (or the Jobs page) is told about updates: the newest version posted, and whether the installed one is older. `installed` is
    what the extension reported about itself (None: it did not say, so no claim is made that it is out of date)."""
    got = newest()
    latest = got[0] if got else None
    have = parse_version(installed)
    return {"latest": latest, "update_available": bool(latest and have and have < parse_version(latest)), "page": "/extension.html"}


@router.get("/api/extension")
def extension_info():
    """What the install page shows: whether there is a download, its version and size."""
    got = newest()
    if got is None:
        return {"available": False}
    version, path = got
    return {"available": True, "version": version, "filename": path.name, "size": path.stat().st_size, "download_url": "/extension/download"}


@router.get("/extension/download")
def download_extension():
    got = newest()
    if got is None:
        raise HTTPException(status_code=404, detail="The extension download isn't available yet.")
    _, path = got
    return FileResponse(path, media_type="application/zip", filename=path.name, headers={"Cache-Control": "no-store"})
