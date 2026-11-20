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
  admin.py                 <- admin API and pages (the signed-in app; see "Access codes" below)
  server.py                <- one process for both: hands each request to main.py or admin.py by its Host header (port 8040)
  models.py, crud.py      <- access_codes, people, accounts and the builder's tables (SQLAlchemy)
  generate.py, builder.py <- the resume builder: reading uploads, the Claude call, stored files/engagements/notes/draft
  apikeys.py, limits.py   <- each person's own Anthropic key (encrypted) and the drafts-per-hour allowance
admin_templates/           <- the admin pages (Build my resume, People, Access codes, Users), Jinja2 so the shared-assets URL is baked in
admin_static/              <- their no-build-step JS and the little CSS that is specific to this app (served by app/admin.py)
manage.py                 <- CLI alternative to the admin panel
migrations/                <- Alembic, same pattern as the other apps here
```

## Accounts (several people, each with a private resume)

The admin app (`app/admin.py`) has accounts, like plans, time and finances: default-deny (signing in is not enough; an administrator
lets people in on the Users page or the dashboard's People page), every resume and access code is owned by one account and filtered to it below the routes
(`shared_python.owned`), and `shared_python.accounts_gate` wraps the whole app. Administrators are `ADMIN_EMAILS`.

- **Jobs** (`app/jobs.py`, the Jobs page, table `jobs`): what a person is considering, private to them (owned; another person's job id is a 404). Added by
  pasting a posting (title, company, link, pay, description) or by importing the job-scoring Chrome extension's CSV (`url, title, company, salary, tier, summary,
  matched, gaps, strengths, scored_at, jd_text`; `|` joins the items in matched/gaps/strengths). Jobs are matched by link per person, so a re-import refreshes the
  score fields only and never touches `applied`, notes or a resume made for the job; the resume label on an import fills in old jobs that have none and never
  re-tags one that does. Limits: `MAX_JOBS_PER_ACCOUNT` 2000, `MAX_IMPORT_ROWS` 1000, `MAX_IMPORT_BYTES` 8 MB, descriptions cut at 60,000 characters. This
  replaces time-management's Jobs page (the direction decided 2026-11-18: one place for the builder, the jobs and the tailored resumes).
- **The system key** (`app/apikeys.py`): an administrator can tick **Can fall back to the system key** on the Users page for a particular person (`PUT
  /api/users/{id}/system-key`, `users.house_key`), who then uses the server's `ANTHROPIC_API_KEY` for the builder when they have not saved a key of their own;
  their own key, if they add one, is always used first, nobody else ever gets the server's, and an administrator's own use is never counted. Every run on it (a draft,
  a pasted-list import, a review) is recorded in `house_key_uses` with the tokens it used (counts only, never any text) and the Users page shows each person's runs
  and an estimated dollar cost this month (tokens times the model's list price, `PRICE_*` in `app/config.py`). No limit by default; set `HOUSE_KEY_CALLS_PER_MONTH` to
  give each such person that many runs a calendar month, after which they are told to add their own key. The page tells them the administrator is covering the cost.
- **Invites** (`app/invites.py`, the Invites page, the same behaviour as Lists and Trips): anyone with an account can ask for a friend to be let in (up to
  `INVITES_PER_USER` different people, default 5; each email counts once whatever became of the request, until a waiting one is withdrawn); an
  administrator approves or declines each (an approved person gets an account and an email, nothing else; this app is behind the dashboard sign-in, so
  nothing is added to Cloudflare) and can allow a declined one again; an administrator's own requests are approved at once and have no limit. A person sees only
  their own requests, the queue is administrators-only, and a resume stays private whoever invited whom. The administrator is told of each request through
  the usual `notify` channels (the periodic digest). `/api/me` carries `invites_left` and, for administrators, `pending_invites` (the sidebar badge). The panel is
  the shared `app-invites.js`; the administrator's queue is `admin_static/invites.js`. A removed person's requests go with their data.
- **An ordinary account** has exactly one resume, with a slug nobody chooses (`u<id>-<random>`), never the site's default, never published. It can edit it,
  download a PDF or Word copy (rendered on the spot, with everything in it), and delete it. It cannot see anyone else's, and neither can an administrator.
- **An administrator** can have several resumes, choose slugs, set the default, publish and un-publish, create access codes, and use the server's Anthropic key
  for the builder when they have not saved one of their own (an ordinary account must bring its own: see "The builder" below).
- **Published** (`people.published`): the public site (`app/main.py`, port 8040, no sign-in) only ever shows a resume that is published, by `?person=`, as the
  default, or through an access code; anything else is a 404 exactly like an unknown slug. The migration published every existing resume (they were all public);
  new ones start unpublished (an administrator's form has a "Show on the public site" tick, on by default). `build.py` only builds published people.
- **time-management's calls** (`/api/resume-people`, `/api/resume-data`, `/api/render`, `/api/render-cover-letter`) carry no sign-in, only `X-Internal-Token`, and act
  as the first administrator; nothing else accepts that token.
- The command-line tools (`manage.py`, `build.py`) are the operator at the keyboard, so they look past the per-person filter on purpose (`owned.unscoped()`);
  `manage.py seed-person` takes `--owner <email>` (default: the first administrator), and only an administrator's resume can ever be the default.
- Tests: `pytest` (no browser needed; PDF and Word rendering are replaced by tiny stand-ins). They include a two-person test that calls every route against another
  person's resume, a sweep of every route signed out and as a stranger, the public-site rules, and the migration on realistic old data.

## Look and feel

The **public resume** (`templates/resume.html.j2`) is its own print-style design: always light, no
theme switch, and untouched by the shared theme - on purpose, since a dark page could put off a
recruiter. The **admin pages** use the shared theme from `shared-assets` (sidebar, cards, icons on
buttons) but start **light** for everyone who has not chosen (`data-theme-default="light"`); the
theme button still offers Dark and Match my device. Set `DATABASE_URL` to point the app and the
migrations at a copy of `access.db`.

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

### The builder: generating it without touching Claude directly

The **Build my resume** page (`/build.html`, in the sidebar) does the above for you, so someone using this app needs no Claude account and
no prompt. Numbered steps:

1. **Your Anthropic key.** Each person brings their own, saved encrypted (`app/apikeys.py`; the droplet needs `KEY_ENCRYPTION_SECRET` in
   `/etc/personal-apps/shared.env`). An administrator with no key of their own falls back to the server's `ANTHROPIC_API_KEY`; nobody else ever does.
2. **LinkedIn's "Get a copy of your data" ZIP**, with the clicks written out. It must be the *larger data archive*: the pick-individual-files
   option leaves out jobs. Only the career files in it are ever read (profile, positions, education, skills, certifications, projects, languages,
   honors, publications, volunteering, courses, email addresses); connections and messages are never opened.
3. **Old resumes** as PDF, Word or text, as many versions as there are.
4. **Engagements**: a form per client or project (which job, what the client does, industry, regulated or not and by whom, technologies, role,
   dates, outcomes, and a don't-name-this-client box for NDAs). Already have them in a list (a notes-app outline, say)? **Paste a list of clients** has Claude propose one engagement per client from it
   (using only what the list says); you tick the ones you want and they are added, then edited with the ordinary form (`POST /api/engagements/import`, which saves nothing, with its own hourly limit). **Remove all engagements** (shown when there are two or more) asks twice, then clears them (`DELETE /api/engagements?confirm=all`, only the caller's own). They ride in resume.yaml as an optional `engagements:` list under the matching job
   (the list itself is never printed, so a later step can tailor the resume to a job). An engagement with outcome lines that is not confidential
   also gets one printed bullet ("Client: ...") written only from the person's own words, unless the job already has a bullet for that client.
5. **Notes**, then **Draft my resume**. With a resume already saved the page defaults to **Update**: the draft is merged INTO the current resume (hand
   edits kept, only what is new or changed added, the saved engagements authoritative) and the review shows exactly which lines it changes.
   **Drafting runs in the background** (`app/drafts.py`): `POST /api/generate-resume` only starts the job and returns at once, and the page asks
   `GET /api/generate-resume/status` every 3 seconds (`DELETE` on it collects the result). A draft takes a minute or two, longer than the 100
   seconds a proxy such as Cloudflare waits for one request, so a single long request used to die with a 5xx while the server finished and saved
   the draft anyway. One job per account, kept in memory (a restart forgets it, and the page then says the draft was interrupted); a finished one
   waits an hour to be collected, so someone who keeps no files on the server still gets it. A page reloaded mid-draft picks the job back up. The
   model's thinking counts against `max_tokens`, so the draft asks for 20,000 at `medium` effort, and a response that stopped for lack of room
   (`stop_reason: max_tokens`) is refused rather than used: a resume cut off after its fifth job still parses as YAML.
6. **Check it, fix it, save it.** The draft opens beside a readable summary; only **Save** makes it the person's resume (an ordinary account replaces its
   one private resume; an administrator adds a person, never published automatically). Under the summary, **Suggestions** are plain rule-based checks (`app/feedback.py`, sent with every
   `POST /api/builder/preview`, so they update as the YAML is edited; no AI, no key): missing email, phone, title or summary, a summary over
   120 words, a job with no title, company or dates (an empty end date prints open-ended), no bullets, more than 8 bullets, a bullet over
   350 characters, repeated bullets, almost no numbers, several jobs marked Present, a job that ends before it starts, jobs whose dates overlap for
   sure (the earlier one ends a month or more after the later one starts; ending in the month the next began is a handover, and a bare year
   never counts against you; most people never hold two jobs at once, so it says to check the dates), a job that has ended but has present-tense bullets (Lead, Own), engagements saved for a job that no bullet mentions,
   a client marked confidential whose name is printed anywhere in the resume (a Fix), and 15+ skills in a group or 40+ in all. Clicking a
   suggestion selects the words it is about in the YAML box (each carries a `find` list, most exact first, ending at a section so it always lands somewhere, and an `nth` for two jobs at one company; the AI review's items carry a `quote`). The page matches ignoring line breaks, quotes and punctuation, so wrapped or quoted bullets are found, and a slightly reworded AI quote is found by its longest run of matching words. **Review with AI**
   (`POST /api/builder/review`, their own key, its own hourly limit apart from drafting) asks Claude, as an editor, for judgment calls the
   rules cannot make (company marketing printed as an accomplishment, a count that does not match its list, vague bullets, tense). It sees
   only what prints (never private `engagements:` or jobs with `include_in_resume: false`), returns `{where, issue, suggestion}` items,
   and changes nothing. The last review is kept in this browser's localStorage (not on the server), with the text it was about, so a reload still shows it and says when the draft has been edited since.

What is kept, per person and private to them (tables `source_files`, `engagements`, `builder_notes`, `builder_drafts`): the *text* of each uploaded
file (never the original; for LinkedIn only the career CSVs), the engagements, the notes, and the unsaved draft, so a reload loses nothing and adding one
engagement then pressing Draft is a full update with no re-upload. A newer LinkedIn ZIP replaces the older one; a file with the same name replaces its
earlier copy; 20 files at most. A **Keep my files and drafts on my account** checkbox (on by default, `builder_notes.keep_files`) is the opt-out:
turning it off deletes what was kept, and from then on files are held in the browser only and sent with each draft (read in memory, nothing stored),
so a refresh starts over; engagements and notes are still saved. All of it is deleted with the person's data.

Limits guard the server: 12 files, 8 MB each, 25 MB total, 40 PDF pages, ZIP entry-count and unpacked-size caps, and 6 drafts an hour per person,
one at a time (`app/limits.py`, in memory, so a restart resets it). The model call itself is in `app/generate.py`. Everything uploaded is treated as material to
read, never as instructions to follow.

Routes (all behind the dashboard sign-in plus an account here, no internal token, since they are for a browser): `/api/api-key`,
`/api/builder/sources` (add, list, remove a kept file), `/api/builder/inspect` (read files without keeping them), `/api/builder/notes`,
`/api/builder/keep`, `/api/builder/draft`, `/api/builder/preview`, `/api/engagements` (plus `/api/engagements/import`), and `POST /api/generate-resume`.

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
behind the dashboard sign-in like the other apps. Pick who a
new code is for, create it, copy the shareable link, and revoke, all from
the browser - no SSH needed.

It's a genuinely separate FastAPI **app** from the public resume (`app/admin.py`, with its own routes, middleware and sign-in gate), served in
the same **process** as the public one (`app/server.py`) and told apart by the `Host` header. That used to be two services (ports 8040 and 8041);
it is one now, because two of everything to run, restart and deploy bought nothing the hostname split does not already give.

The split that matters is the hostnames: `resume.evancooperman.com` is intentionally sign-in-free (Cloudflare-Access-**Bypassed**, so the
public/redacted page loads for strangers), and `resume-admin.evancooperman.com` is gated by the app itself (the dashboard sign-in plus an account
here). Two hostnames keep Cloudflare's per-hostname rules (Bypass, cache) simple, and the two apps share nothing that a request on one could use
to reach the other: a request goes to the admin app only if its `Host` is one of `RESUME_ADMIN_HOSTS` (default `resume-admin.evancooperman.com`
and `resume-admin.localhost`, the latter for the other apps' calls on the droplet); anything else, including an unknown host, goes to the public
app, which has no sign-in-only routes at all. This replaces the old structural guarantee (admin on a port with no tunnel route from the public
hostname) with one in code, so `tests/test_server.py` pins it: every admin-only route is swept on a public host and must not answer, and a
signed-out request on the admin host is still refused. Even a forged `Host: resume-admin...` only reaches the admin app's own gate (default-deny).

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
of the admin page (same hostname/port as the access-code admin above,
gated the same way - the dashboard sign-in plus an account in this app).
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
`/etc/systemd/system/resume.service` (never commit the real value -
see `deploy/resume.service` for the placeholder line).

`app/admin.py` also serves the builder's routes; see "The builder" above.

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
http://localhost:8041. Or run both as one process, as on the droplet:
`uvicorn app.server:app --reload --port 8040`, then open http://localhost:8040 (the public site)
and http://resume-admin.localhost:8040 (the admin app; browsers send `*.localhost` to your own machine).

## Deploying updates

Push to `main` - `.github/workflows/deploy.yml` pulls, reinstalls
dependencies, runs `alembic upgrade head` (real migrations against the
live `access.db`, same as the DB-backed apps in this directory - see
`../DEPLOYMENT.md`), rebuilds both trees, and restarts the `resume` service (it serves both
sites; the old separate `resume-admin` unit is gone, see `../DEPLOYMENT.md`). Access codes you've already created aren't
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

5. **systemd unit** - set the real `INTERNAL_API_TOKEN` value in
   `resume.service` before installing it (matching whatever you set on
   `time-management.service` - see that repo's one-time setup), then:
   ```bash
   sudo cp /opt/apps/resume/deploy/resume.service /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now resume
   curl -m 5 http://127.0.0.1:8040   # the public site, sanity check before wiring up the tunnel
   curl -m 5 -H 'Host: resume-admin.evancooperman.com' http://127.0.0.1:8040/api/codes   # the admin app: refused (302/401/403) without a sign-in
   ```

6. **cloudflared ingress** - add both to `/etc/cloudflared/config.yml`,
   *above* the catch-all `- service: http_status:404` line (both go to the same port: the app tells them apart by hostname):
   ```yaml
     - hostname: resume.evancooperman.com
       service: http://localhost:8040
     - hostname: resume-admin.evancooperman.com
       service: http://localhost:8040
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
   - `resume-admin.evancooperman.com`: never add a Bypass for this one. It
     is gated by the app itself (the dashboard sign-in plus an account here),
     whether or not the Cloudflare Access wildcard still sits in front of it;
     `../DEPLOYMENT.md` says where the cutover to the dashboard sign-in stands.

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

## To do

The running to-do list for all projects is `../TODO.md` (in the personal-projects
folder, one level up; not in git).
