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
  // The spinner covers the whole card (its header, score, description and buttons all stand still while the AI reads), not just the part that will change.
  const card = area.closest(".item-card") || area;
  const updated = await Global.withLoading(card, Global.fetchJSON(`${JOBS_API}/${job.id}/score`, { method: "POST" }));
  details.set(job.id, updated);
  jobs = jobs.map((j) => (j.id === job.id ? { ...j, ...updated } : j));
  return updated;
}

// --- tailoring: a resume and cover letter made for this job. It runs on the server in the background (a minute or two), so this only starts it and
// asks how it is going; the card is covered by the spinner meanwhile, like scoring. ---------------------------------------------------------

const POLL_MS = 3000;
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

// Resolves with the job once the server has finished making its documents; rejects with the server's words if it could not.
async function waitForTailoring(job) {
  for (;;) {
    const state = await Global.fetchJSON(`${JOBS_API}/${job.id}/tailor`);
    if (state.state === "done" || state.state === "idle") break; // idle: finished and already collected (or the server restarted: the job then simply has no new documents)
    if (state.state === "error") {
      await Global.fetchJSON(`${JOBS_API}/${job.id}/tailor`, { method: "DELETE" }).catch(() => {});
      throw new Error(state.error || "The resume couldn't be made.");
    }
    await sleep(POLL_MS);
  }
  await Global.fetchJSON(`${JOBS_API}/${job.id}/tailor`, { method: "DELETE" }).catch(() => {});
  const updated = await Global.fetchJSON(`${JOBS_API}/${job.id}`);
  details.set(job.id, updated);
  jobs = jobs.map((j) => (j.id === job.id ? { ...j, ...updated } : j));
  return updated;
}

const tailoring = new Set(); // job ids being watched, so opening a card again does not start a second poller

async function tailorJob(job, card, alreadyRunning) {
  if (tailoring.has(job.id)) return;
  tailoring.add(job.id);
  const run = async () => {
    if (!alreadyRunning) await Global.fetchJSON(`${JOBS_API}/${job.id}/tailor`, { method: "POST" });
    return waitForTailoring(job);
  };
  try {
    await Global.withLoading(card, run());
    Global.showMessage("Made a resume and cover letter for this job.", "success");
  } catch (err) {
    Global.showMessage(err.message, "error");
  }
  tailoring.delete(job.id);
  render();
}

// The documents made for a job: downloads, the letter to read, and the button to make (or remake) them.
function documentsBlock(job, full, card) {
  const wrap = Global.el("div", { class: "job-documents" });
  const make = Global.el("button", { type: "button", class: "secondary-btn", "data-btn-icon": "zap", text: job.has_documents ? "Make them again" : "Make a resume and cover letter for this job" });
  make.disabled = !full.jd_text;
  make.title = full.jd_text ? "" : "Add the job description first";
  make.addEventListener("click", () => {
    if (job.has_documents && !confirm("Make new ones? They replace the resume and cover letter made for this job before.")) return;
    make.disabled = true;
    tailorJob(job, card.closest(".item-card") || card, false);
  });
  if (!job.has_documents) {
    wrap.append(Global.el("p", { class: "hint", text: "A copy of your resume reworded for this job (nothing invented) and a cover letter, ready to download." }), make);
    return wrap;
  }
  const files = [["Resume", "resume"], ["Cover letter", "cover-letter"]].flatMap(([label, path]) =>
    [["pdf", "PDF"], ["docx", "Word"]].map(([format, name]) => Global.el("a", { class: "secondary-btn", "data-btn-icon": "download", href: `${JOBS_API}/${job.id}/${path}?format=${format}`, text: `${label} (${name})` })));
  const letter = Global.el("pre", { class: "job-letter hidden", text: full.generated_cover_letter || "" });
  const read = Global.el("button", { type: "button", class: "secondary-btn", "data-btn-icon": "eye", text: "Read the cover letter" });
  read.addEventListener("click", () => letter.classList.toggle("hidden"));
  wrap.append(
    Global.el("p", { class: "hint", text: `Made for this job ${when(job.resume_generated_at)}. Read them before you send them: they are written by an AI.` }),
    Global.el("div", { class: "item-actions" }, files),
    Global.el("div", { class: "item-actions" }, [read, make]),
    letter,
  );
  return wrap;
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
    // A run started before a reload (or on another device) is still going on the server: pick it back up.
    Global.fetchJSON(`${JOBS_API}/${job.id}/tailor`).then((state) => { if (state.state === "running") tailorJob(job, card, true); }).catch(() => {});
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

  const documents = documentsBlock(job, full, wrap);
  wrap.appendChild(documents);

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

// Score every job that has a description but no score yet. The server does it in the background (app/scoring.py), so it carries on if this page
// is closed, and the extension's pushes join the same queue. This page only starts it, shows how it is going, and picks up where it is when
// it is opened. A problem that would hit every job (no key, the hourly allowance) ends it with the reason; one job's own problem is counted.
const QUEUE_API = `${JOBS_API}/score-queue`;
let queue = { state: "idle" };
let pollTimer = null;
let lastCount = -1;

function renderBulk() {
  const host = document.getElementById("bulk-score");
  const todo = jobs.filter((j) => !j.tier && j.has_description);
  const running = queue.state === "running";
  host.classList.toggle("hidden", todo.length === 0 && !running);
  if (running) {
    const finished = queue.done + queue.failed;
    const status = Global.el("span", { class: "status", id: "bulk-status", role: "status", text: `Scoring in the background: ${finished} of ${queue.total} done. You can leave this page; it carries on.` });
    const stop = Global.el("button", { type: "button", class: "cancel-btn", text: "Stop" });
    stop.addEventListener("click", async () => {
      stop.disabled = true;
      try {
        queue = await Global.fetchJSON(QUEUE_API, { method: "DELETE" });
      } catch (err) {
        Global.showMessage(err.message, "error");
      }
    });
    host.replaceChildren(status, stop);
    Global.iconifyButtons && Global.iconifyButtons(host);
    return;
  }
  const button = Global.el("button", { type: "button", class: "secondary-btn", "data-btn-icon": "zap", text: `Score the ${todo.length} job${todo.length === 1 ? "" : "s"} with no score yet` });
  button.addEventListener("click", startQueue);
  host.replaceChildren(button, Global.el("span", { class: "status", id: "bulk-status", role: "status" }));
  Global.iconifyButtons && Global.iconifyButtons(host);
}

async function startQueue() {
  try {
    queue = await Global.fetchJSON(QUEUE_API, { method: "POST" });
    lastCount = -1;
    renderBulk();
    poll();
  } catch (err) {
    Global.showMessage(err.message, "error");
  }
}

// Say how a finished queue ended (once), forget it on the server, and refresh the list with the scores.
async function reportEnd(ended) {
  const parts = [`Scored ${ended.done} job${ended.done === 1 ? "" : "s"}.`];
  if (ended.failed) parts.push(`${ended.failed} could not be scored${ended.first_failure ? ` (${ended.first_failure})` : ""}.`);
  if (ended.state === "stopped") parts.push("Stopped.");
  if (ended.state === "error" && ended.error) parts.push(`Stopped: ${ended.error}`);
  Global.showMessage(parts.join(" "), ended.state === "error" || ended.failed ? "error" : "success");
  try {
    await Global.fetchJSON(`${QUEUE_API}?clear=true`, { method: "DELETE" });
  } catch (err) {
    /* it is forgotten after an hour anyway */
  }
}

async function poll() {
  clearTimeout(pollTimer);
  let next;
  try {
    next = await Global.fetchJSON(`${QUEUE_API}/status`);
  } catch (err) {
    pollTimer = setTimeout(poll, 8000);
    return;
  }
  const wasRunning = queue.state === "running";
  queue = next;
  if (queue.state === "running") {
    const count = queue.done + queue.failed;
    if (count !== lastCount) {
      lastCount = count;
      await refreshJobs(); // scores show up as they arrive
    }
    renderBulk();
    pollTimer = setTimeout(poll, 3000);
    return;
  }
  if (queue.state !== "idle") {
    await refreshJobs();
    await reportEnd(queue);
    queue = { state: "idle" };
  } else if (wasRunning) {
    await refreshJobs();
  }
  render();
}

async function refreshJobs() {
  try {
    jobs = await Global.fetchJSON(JOBS_API);
    for (const id of [...details.keys()]) if (!jobs.some((j) => j.id === id)) details.delete(id);
    render();
  } catch (err) {
    /* the next poll tries again */
  }
}

function render() {
  renderFilters();
  renderBulk();
  const host = document.getElementById("jobs");
  const shown = visibleJobs();
  const count = document.getElementById("job-count");
  count.textContent = `Showing ${shown.length} of ${jobs.length} job${jobs.length === 1 ? "" : "s"}`;
  count.classList.toggle("hidden", jobs.length === 0);
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
    poll(); // is the server already scoring some (the extension pushed jobs, or this was started earlier)?
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

// --- connecting the Chrome extension: personal tokens ----------------------------------------------------------------------------------

const TOKENS_API = "/api/tokens";
const closeConnect = toggleForm("show-connect", "connect-card", null);
document.getElementById("close-connect").addEventListener("click", closeConnect);

function when_(iso) {
  return iso ? new Date(iso).toLocaleString() : "";
}

async function loadTokens() {
  const host = document.getElementById("token-list");
  try {
    const rows = await Global.fetchJSON(TOKENS_API);
    host.replaceChildren(
      ...rows.map((t) => {
        const remove = Global.el("button", { type: "button", class: "danger-btn", text: "Remove" });
        remove.addEventListener("click", async () => {
          if (!confirm(`Remove the token "${t.name}"?\n\nThe extension using it stops working until you give it a new one.`)) return;
          try {
            await Global.fetchJSON(`${TOKENS_API}/${t.id}`, { method: "DELETE" });
            loadTokens();
          } catch (err) {
            Global.showMessage(err.message, "error");
          }
        });
        const used = t.last_used_at ? `last used ${when_(t.last_used_at)}` : "never used";
        const version = t.extension_version ? `, extension ${t.extension_version}${t.update_available ? ` (version ${t.extension_latest} is available)` : ""}` : "";
        return Global.el("div", { class: "request-card" }, [
          Global.el("div", { class: "request-info" }, [Global.el("strong", { text: t.name }), Global.el("div", { class: "field-hint", text: `${t.prefix}... made ${when_(t.created_at)}, ${used}${version}` })]),
          Global.el("div", { class: "request-actions" }, [remove]),
        ]);
      })
    );
    Global.iconifyButtons && Global.iconifyButtons(host);
  } catch (err) {
    host.replaceChildren(Global.el("p", { class: "field-hint is-error", text: err.message }));
  }
}

// "Before you start": scoring and tailoring need a saved resume and a usable Anthropic key (their own, or the system key if an administrator allowed it).
// Shown until both are in place, each line saying whether it is done, so a new person is never left to find out from an error message.
async function checkGettingStarted() {
  const box = document.getElementById("getting-started");
  try {
    const [people, key] = await Promise.all([Global.fetchJSON("/api/people"), Global.fetchJSON("/api/api-key")]);
    const haveResume = people.length > 0;
    const haveKey = Boolean(key.usable);
    box.classList.toggle("hidden", haveResume && haveKey);
    if (haveResume && haveKey) return;
    const line = (done, text, href, linkText, tail) =>
      Global.el("li", {}, [
        Global.el("span", { class: done ? "step-done" : "step-optional", text: done ? "done" : "to do" }),
        document.createTextNode(" "),
        done ? document.createTextNode(text) : document.createTextNode(`${text} `),
        done ? document.createTextNode("") : Global.el("a", { href, text: linkText }),
        done ? document.createTextNode("") : document.createTextNode(tail || ""),
      ]);
    document.getElementById("getting-started-list").replaceChildren(
      line(haveResume, "Save your resume.", "/build.html", "Build my resume", " reads your LinkedIn data and old resumes and writes a first draft to check."),
      line(haveKey, "Add your Anthropic key.", "/build.html", "Step 1 of Build my resume", " explains how to get one (about five minutes, once).")
    );
  } catch (err) {
    box.classList.add("hidden"); // the page's own requests will say what is wrong
  }
}

// A banner when the extension on one of the person's computers reported an older version than the one posted on the install page. The extension reports its
// version each time it sends something, so a computer that has not used it since the update shows nothing until it does.
async function checkExtensionUpdate() {
  try {
    const rows = await Global.fetchJSON(TOKENS_API);
    const behind = rows.filter((t) => t.update_available);
    const host = document.getElementById("extension-update");
    host.classList.toggle("hidden", behind.length === 0);
    if (!behind.length) return;
    const names = behind.map((t) => `${t.name} (${t.extension_version})`).join(", ");
    host.replaceChildren(
      document.createTextNode(`A newer version of the Chrome extension is available (${behind[0].extension_latest}). Still on an older one: ${names}. `),
      Global.el("a", { href: "/extension.html", text: "Get the new version and how to update." })
    );
  } catch (err) {
    /* just no banner */
  }
}

async function copyFrom(inputId, button, done) {
  const input = document.getElementById(inputId);
  try {
    await navigator.clipboard.writeText(input.value);
    Global.showMessage(done, "success");
  } catch (err) {
    input.select(); // no clipboard access here: leave it selected for the person to copy
    Global.showMessage("Couldn't copy automatically. It is selected: press Ctrl+C (or Cmd+C).", "error");
  }
}
document.getElementById("copy-token").addEventListener("click", (e) => copyFrom("token-value", e.target, "Token copied."));
document.getElementById("copy-server").addEventListener("click", (e) => copyFrom("token-server", e.target, "Address copied."));
document.getElementById("show-connect").addEventListener("click", loadTokens);

document.getElementById("make-token").addEventListener("click", async (e) => {
  const name = document.getElementById("token-name");
  const status = document.getElementById("token-status");
  status.textContent = "";
  const button = e.currentTarget;
  button.disabled = true;
  try {
    const made = await Global.fetchJSON(TOKENS_API, { method: "POST", headers: JSON_HEADERS, body: JSON.stringify({ name: name.value }) });
    name.value = "";
    document.getElementById("token-value").value = made.token;
    document.getElementById("token-server").value = location.origin;
    document.getElementById("token-new").classList.remove("hidden");
    document.getElementById("token-value").select();
    loadTokens();
  } catch (err) {
    status.textContent = err.message;
  }
  button.disabled = false;
});
document.getElementById("token-name").addEventListener("keydown", (e) => {
  if (e.key === "Enter") document.getElementById("make-token").click();
});
// The new token is gone from the page once the card is closed: it cannot be shown again.
document.getElementById("close-connect").addEventListener("click", () => {
  document.getElementById("token-value").value = "";
  document.getElementById("token-new").classList.add("hidden");
});

// Jobs may have arrived (from the extension) or been scored while this tab was in the background.
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") load();
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
  checkExtensionUpdate();
  checkGettingStarted();
}
start();
