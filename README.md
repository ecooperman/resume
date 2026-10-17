# resume

Resume site with matching PDF/DOCX downloads, generated from a single
source of truth, with contact info hidden unless the visitor has a valid
access code. Codes are managed through a small admin panel on its own
hostname/port, or via a CLI.

```
templates/resume.html.j2 <- the design (HTML/CSS)
build.py                 <- every person's resume data -> site/{public,private}/<slug>/{index.html,resume.pdf,resume.docx}
app/
  render.py                <- the actual rendering pipeline (build.py and app/admin.py both use it)
  main.py                 <- public resume app (port 8040): serves public/<slug>/ or private/<slug>/
  admin.py                 <- admin API (port 8041, separate process - see "Access codes" below)
  models.py, crud.py      <- access_codes + people tables (SQLAlchemy) - see "People" below
admin_static/              <- no-build-step admin frontend (served by app/admin.py)
manage.py                 <- CLI alternative to the admin panel
migrations/                <- Alembic, same pattern as the other apps here
```

## Editing your resume

Edit it through the admin page's People section (not a file - see "People"
below for why), then regenerate the public site:

```bash
python build.py
```

This regenerates two full trees per person under `site/`, each keyed by
their slug:
- **`private/<slug>/`** - everything, including `basics.email`/`phone`/`location`
- **`public/<slug>/`** - those three fields stripped out before rendering,
  nothing else touched

Each tree gets its own `index.html` (styled from `templates/resume.html.j2`),
`resume.pdf` (that same page printed by headless Chromium via Playwright, so
it's always visually in sync with the web version for free), and
`resume.docx` (built separately with `python-docx`, since Word can't render
CSS - styled to look clean, not to be a pixel copy of the HTML).

`app/main.py` decides which tree to hand back on every single request to
`/`, `/resume.pdf`, and `/resume.docx` independently - there's no session,
so a direct link to `/resume.pdf?code=...` is checked exactly the same way
as loading the page first. No PDF/DOCX generation happens per-request; this
script just pre-builds every person's site up front. An admin-UI edit to a
person also triggers a rebuild of just their own tree directly (see
`app/admin.py`'s `_rebuild_person_site`), so a change is live immediately -
`python build.py` only needs running by hand for a from-scratch rebuild or
recovering from a failed automatic one.

By default `/` serves the current default person (`is_default=True`, exactly
one at a time) - add `?person=<slug>` to view or download anyone else's
**public** page instead, e.g. `resume.evancooperman.com/?person=rach`. A
valid `?code=...` overrides this entirely: since a code is tied to one
specific person (see "Access codes" below), it's looked up first and is
authoritative on its own - whoever it belongs to, full private view, no
`?person=` needed or even considered.

Any entry under `experience` can carry `include_in_resume: false` to leave
it off the printed Experience section while keeping it in the YAML - useful
for an early/less-relevant role you don't want listed but still want
available as context (e.g. time-management's "Generate Resume" feature can
still draw on it when writing a cover letter). Omit the key entirely for
the normal case; every job without it is included as before.

## Constructing your resume.yaml

[`example-resume.yaml`](example-resume.yaml) in this repo is a fully
populated, fictional reference showing every field the app understands
(every field used in `templates/resume.html.j2` and `app/render.py`'s DOCX
builder) - use it as the schema to match, not something to fill in by hand
top to bottom.

The easiest way to actually produce your own: hand Claude everything you
have - old resume files (PDF/DOCX/text, any number of versions/years),
your LinkedIn profile (LinkedIn will export your own profile as a PDF from
its "Save to PDF" option - Claude can't fetch a profile from a bare URL,
LinkedIn blocks scraping), and any other loose context about your skills,
clients, side projects, or accomplishments that isn't written down
anywhere formal - and ask it to produce a `resume.yaml` matching this
file's structure. A prompt along these lines works well:

> I want you to build a resume.yaml for me, matching the structure of the
> attached example-resume.yaml exactly. I'm attaching my old resumes and
> my LinkedIn profile PDF, plus some extra context below. Include every
> piece of experience, every skill, and every accomplishment you can find
> or that I mention - don't trim anything down to "resume length" or
> decide something's not worth including. I'll narrow it down myself
> later using include_in_resume: false, or by tailoring per job - for now
> I'd rather have too much in the source file than too little.
>
> [paste/attach old resumes, LinkedIn PDF, and any other context here]

The "include everything, don't trim" instruction matters: this file isn't
your printed resume, it's the *source of truth* a printed resume (and
time-management's per-job tailored resumes and cover letters) gets
generated from. An old job, a certification, a side project, or a skill
that wouldn't make a generic one-page resume can still be exactly the
right thing to surface for one specific job posting - keeping it in the
YAML (with `include_in_resume: false` if you don't want it on your default
printed version) means it's still there to draw on later, instead of lost
because it got cut during the first pass.

### Self-service: generating it without touching Claude directly

The admin UI's **"Generate a resume.yaml from documents"** section (on the
People page) does the above for you, so someone using this app doesn't
need a Claude account or to write a prompt themselves:

1. On your own `linkedin.com/in/...` profile page, open the
   job-application-chrome-extension's popup and click **"Export LinkedIn
   profile"** (only shown on a profile page). Expand any "Show all"
   links in Experience/Education/etc. first so nothing's collapsed off
   the page - it downloads a `linkedin-profile-export-<date>.json`.
2. In the admin UI, upload that JSON plus any other documents you have
   (old resumes as PDF/DOCX, LinkedIn's own "Get a copy of your data"
   export, plain-text notes) and optionally add a sentence or two of
   extra context, then click **Generate**.
3. The generated YAML lands in the "Add person" form's textarea below -
   review and edit it (this is still just a first draft from messy
   source material - check names, dates, and anything that looks off)
   before filling in a name/slug and clicking **Add person**.

Under the hood this is the exact same Claude call described above (see
`app/generate.py`), just with the prompt and file-reading done server-side
instead of by hand. It needs `ANTHROPIC_API_KEY` set (see "Local setup"
below) - without it, the endpoint returns a clear error rather than a
crash. The LinkedIn export itself never touches LinkedIn's servers or
scrapes anyone else's data - it's the extension reading your own already-
authenticated profile page's DOM, the same legitimate pattern the
extension's job-scoring features already use, not automated scraping.

## Access codes

The public page (`resume.evancooperman.com` with no `?code=`) never
includes phone/email/location. Passing a valid code - `?code=<token>` -
unlocks the full version, on the page and in both downloads.

Every code is tied to exactly one person (`AccessCode.person_id`), chosen
when it's created - it unlocks *that* person's private info and nothing
else, regardless of what (if anything) `?person=<slug>` on the request
names, and regardless of who's currently the default person. A code's own
link is just `?code=<token>` - no `?person=` needed, since the code already
says who it's for.

**Admin panel** (day-to-day way to manage codes): `resume-admin.evancooperman.com`,
behind the normal Cloudflare Access login like your other apps. Pick who a
new code is for, create it, copy the shareable link, and revoke, all from
the browser - no SSH needed.

It's a genuinely separate FastAPI app/process/port from the public resume
(`app/admin.py`, port 8041), not just a different route on the same app.
That's deliberate: `resume.evancooperman.com` is intentionally
Cloudflare-Access-**Bypassed** so the public/redacted page loads with no
login. If the admin routes lived on that same app/port, they'd be reachable
un-authenticated too - Cloudflare's per-hostname policy doesn't stop a
request once it's already reached your origin. Putting admin on its own
port means there's no route from the public hostname to it at all; the
isolation is structural, not app logic that has to stay correct forever.

**CLI alternative** - same underlying `access.db`, useful for scripting or
if you're already SSHed in. Run from `/opt/apps/resume` on the droplet with
the venv active (a code only works against the DB it was created in, so
this has to run against the live DB, not your local copy):

```bash
python manage.py create --label "Acme Corp recruiter" --days 30   # --days omitted = never expires; --person omitted = the current default person
python manage.py create --label "Rach's recruiter" --person rach --days 30
python manage.py list
python manage.py revoke <code-or-id>
```

Revoking (either way) is immediate - the next request with that code falls
back to the public/redacted version, same as an invalid or expired one.

## People (resume storage)

Every resume - including the default one this site itself renders - is a
row in this app's own `access.db` (`app/models.Person`: `slug`, `name`,
`resume_yaml`, `is_default`), managed entirely through the "People" section
of the admin page (same hostname/port as the access-code admin below,
gated the same way - Cloudflare Access on this hostname, nothing else).
There is deliberately no file-based resume storage anywhere in this repo
any more - `evan-resume.yaml`/`rach-resume.yaml` were the old approach,
replaced because this repo is public on GitHub (a committed resume file's
raw contact info would be scrapable, bypassing the access-code redaction
below entirely) and because editing a file needed `scp`/SSH access, not
just a browser. Exactly one person has `is_default=True` at a time - that's
who a bare request with no `?person=`/`?code=` (a plain visit to
resume.evancooperman.com, or a bare `/api/resume-data` call) resolves to.
`build.py` itself builds *everyone's* tree, not just the default person's -
see "Editing your resume" above.

First-time bootstrap from an existing file (not needed for ongoing edits -
those go through the People UI):

```bash
python manage.py seed-person --slug evan --name "Evan Cooperman" --file evan-resume.yaml --default
```

## Internal API (for time-management's resume generation)

`app/admin.py` exposes three extra endpoints, purely for the
`time-management` app's "Generate Resume" feature - not meant for browser
use, and not part of the access-code system above:

- `GET /api/resume-people` - every person available to tailor from, as
  `{slug, name, is_default}` - source list for time-management's per-person
  "Resume" dropdown (People, in its Settings page), so a household member
  other than the default person can generate from their own resume (e.g.
  slug `"rach"`) instead.
- `GET /api/resume-data?person=<slug>` - the given person's resume data (by
  slug - defaults to whoever `is_default=True` is if `person` is omitted),
  as JSON. 404s on an unknown slug.
- `POST /api/render` - `{"data": {...resume-shaped dict...}, "format":
  "pdf"|"docx"}` -> renders it via the same pipeline as `build.py`
  (`app/render.py`), into a throwaway temp directory that never touches
  `site/` or the `people` table - returns the file bytes. Used to turn a
  *tailored* resume (someone else's data shaped like a resume yaml file, not
  necessarily this one) into a real PDF/DOCX.

All three require an `X-Internal-Token` header matching the
`INTERNAL_API_TOKEN` environment variable. None are reachable through
Cloudflare (this port has no tunnel route - see "Access" below), so the
token is defense in depth
against another process on the same droplet, not the primary access control.
**Must be set to the exact same value as `INTERNAL_API_TOKEN` on the
`time-management` app** - see that app's README. Locally, export it in your
shell before running `uvicorn app.admin:app`; on the droplet, set it in
`/etc/systemd/system/resume-admin.service` (never commit the real value -
see `deploy/resume-admin.service` for the placeholder line).

`app/admin.py` also exposes `POST /api/generate-resume` - the browser-
facing "Generate a resume.yaml from documents" feature (see "Self-service"
above under "Constructing your resume.yaml"). Unlike the three above, this
one is gated the same way as the rest of the admin UI (Cloudflare Access on
this hostname, no internal token) since it's meant to be used from a
browser, not called by another app. It needs its own `ANTHROPIC_API_KEY`
environment variable set (same convention as `time-management`'s own - a
separate key is fine, they're unrelated processes) - export it in your
shell locally, set it on `resume-admin.service` on the droplet. Without it,
the endpoint returns a clear 503 instead of a crash.

## Local setup

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
playwright install chromium   # one-time, downloads the headless browser binary
alembic upgrade head          # creates access.db
python build.py
uvicorn app.main:app --reload --port 8040
```

Then open http://localhost:8040. To also run the admin panel locally:
`uvicorn app.admin:app --reload --port 8041` in another terminal, then open
http://localhost:8041.

## Deploying updates

Push to `main` - `.github/workflows/deploy.yml` pulls, reinstalls
dependencies, runs `alembic upgrade head` (real migrations against the
live `access.db`, same as the DB-backed apps in this directory - see
`../DEPLOYMENT.md`), rebuilds both trees, and restarts **both** services
(`resume` and `resume-admin`). Access codes you've already created aren't
touched by any of this.

## One-time droplet setup

Most of this is already done for the live site - kept here for reference
and for anyone rebuilding from scratch. Steps marked **(admin)** are only
for the second service.

1. **GitHub secrets** - on the `resume` repo: `DO_HOST`, `DO_USER=deploy`,
   `DO_SSH_KEY` (same values as your other app repos). Shared by both
   services since they deploy from the same repo/workflow.

2. **Sudoers** - add both service names to the `deploy` user's narrow
   restart-only sudo rights:
   ```bash
   sudo visudo -f /etc/sudoers.d/deploy-restart
   ```
   ```
   deploy ALL=(root) NOPASSWD: /bin/systemctl restart time-management, /bin/systemctl restart social-planning, /bin/systemctl restart resume, /bin/systemctl restart resume-admin
   ```
   Then verify: `sudo visudo -c`.

3. **Clone + venv**:
   ```bash
   sudo -iu deploy
   cd /opt/apps
   git clone https://github.com/<your-github-user>/resume.git
   cd resume
   python3 -m venv venv && source venv/bin/activate
   pip install -r requirements.txt
   playwright install chromium   # downloads the browser binary, no root needed
   alembic upgrade head          # creates access.db
   exit   # back to your own sudo-capable user
   ```

4. **Playwright's OS-level dependencies (root, one-time only)** - the
   `deploy` user's sudo is scoped to just `systemctl restart`, so it *can't*
   run `apt-get install` itself. Install the shared libraries Chromium needs
   once, as yourself, then sanity-check the full build as the `deploy` user:
   ```bash
   sudo /opt/apps/resume/venv/bin/playwright install-deps chromium
   sudo -iu deploy /opt/apps/resume/venv/bin/playwright install chromium
   sudo -iu deploy /opt/apps/resume/venv/bin/python /opt/apps/resume/build.py
   ```
   Call the venv's binaries by absolute path rather than
   `sudo -iu deploy bash -c 'source venv/bin/activate && ...'` - it's easy for
   that pattern to silently run as root instead (wrong user's Playwright
   browser cache, `site/` output ends up root-owned and unreadable by the
   `deploy`-run service) or to skip activation and hit
   `Command 'playwright' not found`. `build.py` and `manage.py` both resolve
   their paths off their own file location, so calling them by absolute path
   from anywhere is safe. If either of those mistakes already happened,
   reset with `sudo chown -R deploy:deploy /opt/apps/resume` and redo the
   two commands above.

   Confirm both trees landed: `ls -la /opt/apps/resume/site/public/
   /opt/apps/resume/site/private/`. Routine deploys only re-run
   `playwright install chromium` (cache hit, no root needed) before
   `python build.py`, already in `.github/workflows/deploy.yml`.

5. **systemd units** - set the real `INTERNAL_API_TOKEN` value in
   `resume-admin.service` before installing it (matching whatever you set on
   `time-management.service` - see that repo's one-time setup), then:
   ```bash
   sudo cp /opt/apps/resume/deploy/resume.service /etc/systemd/system/
   sudo cp /opt/apps/resume/deploy/resume-admin.service /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now resume
   sudo systemctl enable --now resume-admin
   curl -m 5 http://127.0.0.1:8040   # sanity check before wiring up the tunnel
   curl -m 5 http://127.0.0.1:8041/api/codes   # (admin) should return []
   ```

6. **cloudflared ingress** - add both to `/etc/cloudflared/config.yml`,
   *above* the catch-all `- service: http_status:404` line:
   ```yaml
     - hostname: resume.evancooperman.com
       service: http://localhost:8040
     - hostname: resume-admin.evancooperman.com
       service: http://localhost:8041
   ```
   Route DNS and restart (skip `route dns` for either hostname if it
   already has a record pointing somewhere else - update it in the
   dashboard instead):
   ```bash
   sudo cloudflared tunnel route dns home-apps resume.evancooperman.com
   sudo cloudflared tunnel route dns home-apps resume-admin.evancooperman.com
   sudo systemctl restart cloudflared
   ```

7. **Access - the two hostnames are opposite of each other, on purpose.**
   - `resume.evancooperman.com`: access control is handled at the app level
     (the redaction + code system above), not Cloudflare Access, since the
     whole point is for strangers to load the public/redacted page without
     an email OTP. The existing wildcard Access application
     (`*.evancooperman.com`) covers this hostname by default, which would
     block *everyone* - so it needs a `Bypass` policy (or its own Access
     application with a Bypass action) carved out in the Zero Trust
     dashboard, scoped to just this hostname.
   - `resume-admin.evancooperman.com`: leave it alone. It should stay
     covered by the wildcard Access app like every other app here - same
     email + one-time-PIN login. Don't add a Bypass policy for this one.

   Do the Bypass carve-out deliberately, on the specific hostname, not by
   loosening the wildcard app itself - so admin (and your other apps) stay
   exactly as locked down as they are today.

8. **Cache Rule (bypass)** - Caching → Cache Rules → add
   `resume.evancooperman.com` to the existing bypass rule (or create one).
   This matters even more now than for a purely static site: without it,
   Cloudflare could cache one visitor's response (public or unlocked) and
   serve it to the next visitor regardless of their own code. The admin
   hostname doesn't need this - it's not meant to be cached at all, and its
   responses are JSON/dynamic rather than static assets.

9. Push to `main` - CI takes it from here on future updates.
