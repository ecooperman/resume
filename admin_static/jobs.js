// Jobs (jobs.html): the signed-in person's own jobs. Add one, import the Chrome extension's CSV, filter by fit, and open a job to see its
// score, track it (applied, notes) or delete it. Needs Global (shared-assets). The server keeps every job private to its owner.

const JOBS_API = "/api/jobs";
const JSON_HEADERS = { "Content-Type": "application/json" };
const TIERS = ["Strong", "Good", "Possible", "Weak", "Unclear", "Unscored"];
const FILTER_KEY = "resume-job-filters";

let jobs = [];
let me = { is_admin: false };
let resumes = [];
const expanded = new Set(); // which cards are open, kept across re-draws so acting on a job does not collapse it and jump the page
const details = new Map(); // job id -> the full job, fetched when its card first opens

// Which fit tiers are shown, remembered in this browser. Everything is shown until the person chooses otherwise.
let hiddenTiers = new Set();
let hideApplied = false;
try {
  const saved = JSON.parse(localStorage.getItem(FILTER_KEY) || "null");
  if (saved) {
    hiddenTiers = new Set(saved.hidden || []);
    hideApplied = Boolean(saved.hideApplied);
  }
} catch (err) {
  /* private window or blocked storage: filters just are not remembered */
}
function saveFilters() {
  try {
    localStorage.setItem(FILTER_KEY, JSON.stringify({ hidden: [...hiddenTiers], hideApplied }));
  } catch (err) {
    /* see above */
  }
}

const tierOf = (job) => job.tier || "Unscored";
const when = (iso) => (iso ? new Date(iso).toLocaleDateString() : "");

function tierBadge(tier) {
  return Global.el("span", { class: `tier-badge tier-${tier.toLowerCase()}`, text: tier });
}

// --- one job's score, as laid out by the extension: matched / strengths / gaps are lists joined with "|" ------------------------------------

function scoreBlock(job, full) {
  const block = Global.el("div", { class: "job-score" });
  if (job.salary) block.appendChild(Global.el("p", { class: "job-score-salary", text: job.salary }));
  if (job.summary) block.appendChild(Global.el("p", { class: "job-score-summary", text: job.summary }));
  for (const [label, value] of [["Matched", job.matched], ["Strengths", job.strengths], ["Gaps", job.gaps]]) {
    if (!value) continue;
    const items = value.split("|").map((s) => s.trim()).filter(Boolean);
    block.appendChild(Global.el("div", { class: "job-score-section" }, [Global.el("div", { class: "job-score-label", text: label }), Global.el("ul", {}, items.map((t) => Global.el("li", { text: t })))]));
  }
  if (!job.tier && !job.summary) block.appendChild(Global.el("p", { class: "hint", text: "Not scored yet." }));
  return block;
}

// The job description, editable (a job pasted without one can get it here): scoring and tailoring read it.
function descriptionEditor(job, full) {
  const text = Global.el("textarea", { class: "job-jd", rows: "8", "aria-label": `Job description for ${job.name}`, placeholder: "Paste the posting's text here. Scoring and the tailored resume read it." });
  text.value = full.jd_text || "";
  const save = Global.el("button", { type: "button", class: "secondary-btn", "data-btn-icon": "check", text: "Save description" });
  save.addEventListener("click", () => patchJob(job, { jd_text: text.value }, "Description saved."));
  const field = Global.el("div", { class: "field hidden" }, [text, save]);
  const toggle = Global.el("button", { type: "button", class: "secondary-btn", "data-btn-icon": "eye", text: full.jd_text ? "Show or edit the job description" : "Add the job description" });
  toggle.addEventListener("click", () => field.classList.toggle("hidden"));
  if (!full.jd_text) field.classList.remove("hidden");
  return Global.el("div", { class: "job-description" }, [toggle, field]);
}

// --- scoring ---------------------------------------------------------------------------------------------------------------------------

async function scoreJob(job, area) {
  const updated = await Global.withLoading(area, Global.fetchJSON(`${JOBS_API}/${job.id}/score`, { method: "POST" }));
  details.set(job.id, updated);
  jobs = jobs.map((j) => (j.id === job.id ? { ...j, ...updated } : j));
  return updated;
}

// --- a card -----------------------------------------------------------------------------------------------------------------------------

function jobCard(job) {
  const card = Global.el("div", { class: "item-card" + (expanded.has(job.id) ? " expanded" : ""), "data-id": job.id });
  const summary = Global.el("button", { type: "button", class: "item-summary job-summary", "aria-expanded": String(expanded.has(job.id)) });
  summary.appendChild(Global.el("span", { class: "item-summary-title", text: job.name }));
  if (job.company) summary.appendChild(Global.el("span", { class: "item-badge item-badge-muted", text: job.company }));
  summary.appendChild(tierBadge(tierOf(job)));
  if (job.applied) summary.appendChild(Global.el("span", { class: "item-badge item-badge-accent", text: "Applied" }));
  summary.appendChild(Global.el("span", { class: "item-chevron", "aria-hidden": "true", text: "▸" }));

  const body = Global.el("div", { class: "item-details" + (expanded.has(job.id) ? "" : " hidden") });
  Global.wireAccordionToggle(card, summary, body);
  card.append(summary, body);

  const open = async () => {
    expanded.add(job.id);
    if (!details.has(job.id)) {
      try {
        details.set(job.id, await Global.withLoading(body, Global.fetchJSON(`${JOBS_API}/${job.id}`)));
      } catch (err) {
        expanded.delete(job.id);
        Global.showMessage(err.message, "error");
        return;
      }
    }
    body.replaceChildren(jobDetail(job, details.get(job.id)));
    Global.iconifyButtons && Global.iconifyButtons(body);
  };
  summary.addEventListener("click", () => {
    if (card.classList.contains("expanded")) open();
    else expanded.delete(job.id);
  });
  if (expanded.has(job.id)) open();
  return card;
}

async function patchJob(job, changes, done) {
  try {
    const updated = await Global.fetchJSON(`${JOBS_API}/${job.id}`, { method: "PATCH", headers: JSON_HEADERS, body: JSON.stringify(changes) });
    details.set(job.id, updated);
    jobs = jobs.map((j) => (j.id === job.id ? { ...j, ...updated } : j));
    if (done) Global.showMessage(done, "success");
    render();
  } catch (err) {
    Global.showMessage(err.message, "error");
  }
}

function jobDetail(job, full) {
  const wrap = Global.el("div", { class: "item-details-inner" });
  const links = [];
  if (job.url) links.push(Global.el("a", { href: job.url, target: "_blank", rel: "noopener noreferrer", text: "Open the posting" }));
  if (job.scored_at) links.push(Global.el("span", { class: "hint", text: `Scored ${when(job.scored_at)}` }));
  if (job.created_at) links.push(Global.el("span", { class: "hint", text: `Added ${when(job.created_at)}` }));
  if (links.length) wrap.appendChild(Global.el("div", { class: "job-links" }, links));
  const scoreArea = Global.el("div", { class: "job-score-area" }, [scoreBlock(job, full)]);
  wrap.appendChild(scoreArea);
  wrap.appendChild(descriptionEditor(job, full));
  const scoreBtn = Global.el("button", { type: "button", class: "secondary-btn", "data-btn-icon": "zap", text: job.tier ? "Score again" : "Score this job" });
  scoreBtn.disabled = !full.jd_text;
  scoreBtn.title = full.jd_text ? "" : "Add the job description first";
  scoreBtn.addEventListener("click", async () => {
    scoreBtn.disabled = true;
    try {
      const updated = await scoreJob(job, scoreArea);
      Global.showMessage(`Scored: ${updated.tier}.`, "success");
      render();
    } catch (err) {
      Global.showMessage(err.message, "error");
      scoreBtn.disabled = false;
    }
  });

  const applied = Global.el("input", { type: "checkbox", id: `applied-${job.id}` });
  applied.checked = job.applied;
  applied.addEventListener("change", () => patchJob(job, { applied: applied.checked }, applied.checked ? "Marked as applied." : "Marked as not applied."));
  wrap.appendChild(Global.el("div", { class: "field checkbox-field" }, [applied, Global.el("label", { for: `applied-${job.id}`, text: "I've applied to this one" })]));

  const notes = Global.el("textarea", { rows: "3", maxlength: "5000", "aria-label": `Notes on ${job.name}`, placeholder: "Notes: who you spoke to, what's next..." });
  notes.value = full.notes || "";
  const saveNotes = Global.el("button", { type: "button", class: "secondary-btn", "data-btn-icon": "check", text: "Save notes" });
  saveNotes.addEventListener("click", () => patchJob(job, { notes: notes.value }, "Notes saved."));
  wrap.appendChild(Global.el("div", { class: "field" }, [Global.el("label", { text: "Notes" }), notes]));

  const actions = Global.el("div", { class: "item-actions" }, [scoreBtn, saveNotes]);
  const remove = Global.el("button", { type: "button", class: "danger-btn", text: "Delete this job" });
  remove.addEventListener("click", async () => {
    if (!confirm(`Delete "${job.name}"${job.company ? ` at ${job.company}` : ""}? Its score and any resume made for it go too.`)) return;
    try {
      await Global.fetchJSON(`${JOBS_API}/${job.id}`, { method: "DELETE" });
      jobs = jobs.filter((j) => j.id !== job.id);
      expanded.delete(job.id);
      details.delete(job.id);
      Global.showMessage("Deleted.", "success");
      render();
    } catch (err) {
      Global.showMessage(err.message, "error");
    }
  });
  actions.appendChild(remove);
  wrap.appendChild(actions);
  return wrap;
}

// --- the list and its filters ----------------------------------------------------------------------------------------------------------

const RANK = { Strong: 0, Good: 1, Possible: 2, Unclear: 3, Weak: 4, Unscored: 5 };

function visibleJobs() {
  return jobs
    .filter((j) => !hiddenTiers.has(tierOf(j)) && !(hideApplied && j.applied))
    .sort((a, b) => RANK[tierOf(a)] - RANK[tierOf(b)] || b.id - a.id); // best fit first, newest first within a tier
}

function renderFilters() {
  const host = document.getElementById("job-filters");
  const counts = {};
  for (const j of jobs) counts[tierOf(j)] = (counts[tierOf(j)] || 0) + 1;
  const chips = TIERS.filter((t) => counts[t]).map((tier) => {
    const on = !hiddenTiers.has(tier);
    const chip = Global.el("button", { type: "button", class: "job-filter" + (on ? " on" : ""), "aria-pressed": String(on), text: `${tier} (${counts[tier]})` });
    chip.addEventListener("click", () => {
      if (on) hiddenTiers.add(tier);
      else hiddenTiers.delete(tier);
      saveFilters();
      render();
    });
    return chip;
  });
  const hide = Global.el("input", { type: "checkbox", id: "hide-applied" });
  hide.checked = hideApplied;
  hide.addEventListener("change", () => {
    hideApplied = hide.checked;
    saveFilters();
    render();
  });
  host.replaceChildren(...chips, Global.el("label", { class: "job-filter-applied", for: "hide-applied" }, [hide, " Hide the ones I've applied to"]));
  host.classList.toggle("hidden", jobs.length === 0);
}

// Score every job that has a description but no score yet, one after another, with a count; the person can stop. A problem that would hit every
// job (no key, the hourly allowance) stops it with the reason; one job's own problem is skipped and reported at the end.
let bulkRunning = false;
let bulkStop = false;

function renderBulk() {
  const host = document.getElementById("bulk-score");
  const todo = jobs.filter((j) => !j.tier && j.has_description);
  host.classList.toggle("hidden", todo.length === 0 && !bulkRunning);
  if (bulkRunning) return;
  const button = Global.el("button", { type: "button", class: "secondary-btn", "data-btn-icon": "zap", text: `Score the ${todo.length} job${todo.length === 1 ? "" : "s"} with no score yet` });
  button.addEventListener("click", () => scoreAll(todo.map((j) => j.id)));
  host.replaceChildren(button, Global.el("span", { class: "status", id: "bulk-status", role: "status" }));
  Global.iconifyButtons && Global.iconifyButtons(host);
}

async function scoreAll(ids) {
  bulkRunning = true;
  bulkStop = false;
  const host = document.getElementById("bulk-score");
  const stop = Global.el("button", { type: "button", class: "cancel-btn", text: "Stop" });
  stop.addEventListener("click", () => {
    bulkStop = true;
    stop.disabled = true;
  });
  const status = Global.el("span", { class: "status", role: "status" });
  host.replaceChildren(status, stop);
  let done = 0;
  const skipped = [];
  let fatal = "";
  for (const id of ids) {
    if (bulkStop) break;
    status.textContent = `Scoring ${done + skipped.length + 1} of ${ids.length}... (about 15 seconds each; keep this page open)`;
    try {
      await scoreJob({ id }, document.getElementById("jobs"));
      done++;
      render();
    } catch (err) {
      if (/key|times an hour|runs on the system key/i.test(err.message)) {
        fatal = err.message;
        break;
      }
      skipped.push(err.message);
    }
  }
  bulkRunning = false;
  render();
  const parts = [`Scored ${done}.`];
  if (skipped.length) parts.push(`${skipped.length} skipped: ${skipped[0]}`);
  if (bulkStop) parts.push("Stopped.");
  if (fatal) parts.push(`Stopped: ${fatal}`);
  Global.showMessage(parts.join(" "), fatal || skipped.length ? "error" : "success");
}

function render() {
  renderFilters();
  renderBulk();
  const host = document.getElementById("jobs");
  const shown = visibleJobs();
  if (!jobs.length) {
    host.replaceChildren(Global.el("p", { class: "hint", text: "No jobs yet. Add one above, or import a CSV from the Chrome extension." }));
  } else if (!shown.length) {
    host.replaceChildren(Global.el("p", { class: "hint", text: "Every job is hidden by the filters above." }));
  } else {
    host.replaceChildren(...shown.map(jobCard));
  }
  Global.iconifyButtons && Global.iconifyButtons(host);
}

async function load() {
  try {
    jobs = await Global.withLoading(document.getElementById("jobs"), Global.fetchJSON(JOBS_API));
    render();
  } catch (err) {
    document.getElementById("jobs").replaceChildren(Global.el("p", { class: "hint is-error", text: err.message }));
  }
}

// --- adding a job, and importing the extension's CSV -----------------------------------------------------------------------------------

function toggleForm(buttonId, formId, focusId) {
  const button = document.getElementById(buttonId);
  const form = document.getElementById(formId);
  const close = () => {
    form.classList.add("hidden");
    button.classList.remove("hidden");
  };
  button.addEventListener("click", () => {
    form.classList.remove("hidden");
    button.classList.add("hidden");
    if (focusId) document.getElementById(focusId).focus();
  });
  return close;
}

const closeAdd = toggleForm("show-add-job", "add-job-form", "job-name");
document.getElementById("cancel-add-job").addEventListener("click", closeAdd);
document.getElementById("add-job-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const body = {
    name: document.getElementById("job-name").value,
    company: document.getElementById("job-company").value,
    url: document.getElementById("job-url").value,
    salary: document.getElementById("job-salary").value,
    jd_text: document.getElementById("job-jd").value,
  };
  const submit = e.target.querySelector("button[type=submit]");
  submit.disabled = true;
  try {
    const job = await Global.fetchJSON(JOBS_API, { method: "POST", headers: JSON_HEADERS, body: JSON.stringify(body) });
    e.target.reset();
    closeAdd();
    jobs = [job, ...jobs];
    details.set(job.id, job);
    expanded.add(job.id);
    Global.showMessage(`Added "${job.name}".`, "success");
    render();
  } catch (err) {
    Global.showMessage(err.message, "error");
  }
  submit.disabled = false;
});

const closeImport = toggleForm("show-import", "import-card", null);
document.getElementById("cancel-import").addEventListener("click", closeImport);
document.getElementById("import-btn").addEventListener("click", async () => {
  const file = document.getElementById("import-file").files[0];
  const status = document.getElementById("import-status");
  if (!file) {
    status.textContent = "Choose the CSV file first.";
    return;
  }
  const data = new FormData();
  data.append("file", file);
  const resume = document.getElementById("import-resume");
  if (resume.value) data.append("person_id", resume.value);
  status.textContent = "Importing...";
  try {
    const result = await Global.withLoading(document.getElementById("import-card"), Global.fetchJSON(`${JOBS_API}/import`, { method: "POST", body: data }));
    status.textContent = "";
    document.getElementById("import-file").value = "";
    closeImport();
    Global.showMessage(`Imported: ${result.created} new, ${result.updated} refreshed, ${result.skipped} skipped (no link or title).`, "success");
    load();
  } catch (err) {
    status.textContent = err.message;
  }
});

async function start() {
  try {
    me = await Global.fetchJSON("/api/me");
    if (me.is_admin) {
      // An administrator may have several resumes: say which one an import is for. Everyone else has one, so nothing to choose.
      resumes = await Global.fetchJSON("/api/people");
      if (resumes.length > 1) {
        const select = document.getElementById("import-resume");
        select.replaceChildren(Global.el("option", { value: "", text: "My default resume" }), ...resumes.map((p) => Global.el("option", { value: String(p.id), text: p.name })));
        document.getElementById("import-resume-field").classList.remove("hidden");
      }
    }
  } catch (err) {
    /* the page's own requests will say what is wrong */
  }
  Global.iconifyButtons && Global.iconifyButtons(document.body);
  load();
}
start();
