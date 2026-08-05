# resume

Static resume site with matching PDF/DOCX downloads, generated from a single
source of truth.

```
resume.yaml              <- edit this, everything else is derived
templates/resume.html.j2 <- the design (HTML/CSS)
build.py                 <- resume.yaml -> site/index.html, resume.pdf, resume.docx
app/                      <- thin FastAPI static file server for site/
```

## Editing your resume

Edit `resume.yaml`, then:

```bash
python build.py
```

This regenerates everything in `site/`:
- `index.html` — the page, styled from `templates/resume.html.j2`
- `resume.pdf` — that same page, printed by a headless Chromium (Playwright),
  so it's always visually in sync with the web version for free
- `resume.docx` — built separately with `python-docx` (Word can't render CSS,
  so this is styled to look clean, not to be a pixel copy of the HTML)

## Local setup

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
playwright install chromium   # one-time, downloads the headless browser binary
python build.py
uvicorn app.main:app --reload --port 8030
```

Then open http://localhost:8030.

## Deploy

Follows the same pattern as the other apps in this directory — see
`../DEPLOYMENT.md`. Port `8030`, service name `resume`, intended hostname
`resume.evancooperman.com`. `deploy/resume.service` and
`.github/workflows/deploy.yml` are already set up; the deploy step runs
`build.py` itself so pushing an updated `resume.yaml` is all it takes to
republish.
