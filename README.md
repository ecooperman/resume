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
uvicorn app.main:app --reload --port 8040
```

Then open http://localhost:8040.

## Deploy

Follows the same pattern as the other apps in this directory — see
`../DEPLOYMENT.md`. Port `8040`, service name `resume`, hostname
`www.evancooperman.com` (left behind the existing wildcard Cloudflare Access
gate for now, not public). `deploy/resume.service` and
`.github/workflows/deploy.yml` are already set up; the deploy step runs
`build.py` itself so pushing an updated `resume.yaml` is all it takes to
republish.

### One-time droplet setup

1. **GitHub secrets** — on the `resume` repo: `DO_HOST`, `DO_USER=deploy`,
   `DO_SSH_KEY` (same values as your other app repos).

2. **Sudoers** — add this app's service to the `deploy` user's narrow
   restart-only sudo rights:
   ```bash
   sudo visudo -f /etc/sudoers.d/deploy-restart
   ```
   Add `resume` to the existing line, e.g.:
   ```
   deploy ALL=(root) NOPASSWD: /bin/systemctl restart time-management, /bin/systemctl restart social-planning, /bin/systemctl restart resume
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
   exit   # back to your own sudo-capable user
   ```

4. **Playwright's OS-level dependencies (root, one-time only)** — the
   `deploy` user's sudo is scoped to just `systemctl restart`, so it *can't*
   run `apt-get install` itself. Install the shared libraries Chromium needs
   once, as yourself, then sanity-check the full build as the `deploy` user:
   ```bash
   sudo /opt/apps/resume/venv/bin/playwright install-deps chromium
   sudo -iu deploy bash -c 'cd /opt/apps/resume && source venv/bin/activate && python build.py'
   ```
   Confirm all three files landed: `ls -la /opt/apps/resume/site/`. Routine
   deploys only re-run `playwright install chromium` (cache hit, no root
   needed) before `python build.py`, already in
   `.github/workflows/deploy.yml`.

5. **systemd unit**:
   ```bash
   sudo cp /opt/apps/resume/deploy/resume.service /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now resume
   curl -m 5 http://127.0.0.1:8040   # sanity check before wiring up the tunnel
   ```

6. **cloudflared ingress** — add to `/etc/cloudflared/config.yml`, *above*
   the catch-all `- service: http_status:404` line:
   ```yaml
     - hostname: www.evancooperman.com
       service: http://localhost:8040
   ```
   If `www.evancooperman.com` doesn't already have a DNS record pointing
   somewhere else, route it and restart:
   ```bash
   sudo cloudflared tunnel route dns home-apps www.evancooperman.com
   sudo systemctl restart cloudflared
   ```
   If a `www` record already exists (e.g. pointing at something else), you'll
   need to update/replace that record in the Cloudflare dashboard instead of
   using `route dns`.

7. **Access** — nothing to do. The existing wildcard Access application
   (`*.evancooperman.com`) already covers `www`, so this stays behind the
   same email + one-time-PIN login as your other apps, per "leave it locked
   down for now."

8. **Cache Rule (bypass)** — Caching → Cache Rules → add
   `www.evancooperman.com` to the existing bypass rule (or create one), so a
   `resume.yaml` update actually shows up without a manual "Purge Everything"
   after each deploy.

9. Push to `main` — CI takes it from here on future updates.
