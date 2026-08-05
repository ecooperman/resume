"""
Pure static file server. All content lives in resume.yaml and is rendered
to site/ by build.py -- this app just serves whatever's in that directory.
Run `python build.py` after editing resume.yaml (also done automatically on
deploy, see .github/workflows/deploy.yml).
"""
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

SITE_DIR = Path(__file__).parent.parent / "site"

app = FastAPI()
app.mount("/", StaticFiles(directory=SITE_DIR, html=True), name="site")
