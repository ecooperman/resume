// "Build my resume": bring your LinkedIn data and old resumes, describe the work behind them (saved to your account), get a draft to
// review and save, and later merge new LinkedIn data into the resume you already have.
// Needs Global (shared-assets). The server does all the checking (limits, who may use what); this only guides and shows.
const el = Global.el;
const JSON_HEADERS = { "Content-Type": "application/json" };

// Mirrors the server's limits (app/generate.py) so a mistake is caught before an upload, not after.
const MAX_FILES = 12;
const MAX_FILE_MB = 8;
const READABLE = [".pdf", ".docx", ".txt", ".md", ".csv", ".json", ".yaml", ".yml", ".zip"];

let me = { is_admin: false };
let existing = null; // the signed-in person's one resume, if they have one (ordinary accounts)
let keyStatus = { usable: false };
let keep = true; // their choice (saved on the server): keep the text of their files and their draft, or hold nothing
let localFiles = []; // when not keeping: the File objects, held in this page only (a refresh loses them)
let localChecks = []; // what the server made of each, same order
let sources = []; // what the server keeps of the person's files (GET /api/builder/sources): their text, never the originals
let engagements = []; // saved on the server (GET /api/engagements); the form below adds, edits and removes them there
let myPeople = []; // this account's resumes (an ordinary account has at most one)
let target = null; // the resume an update merges into: {id, name}; null means a brand new one
let baseYaml = null; // that resume's text, so the review can show what an update changes
let targetEmployers = []; // the companies on it, offered when describing an engagement
let draftTimer = null;

const $ = (id) => document.getElementById(id);

// --- notes, saved with the account ----------------------------------------------------------------------------------------------

let notesTimer = null;
async function loadNotes() {
  try {
    const saved = await Global.fetchJSON("/api/builder/notes");
    $("notes").value = saved.notes;
    keep = saved.keep_files;
    renderKeep();
  } catch (err) {
    Global.showMessage(err.message, "error");
  }
}
function saveNotesSoon() {
  clearTimeout(notesTimer);
  $("notes-status").textContent = "";
  notesTimer = setTimeout(async () => {
    try {
      await Global.fetchJSON("/api/builder/notes", { method: "PUT", headers: JSON_HEADERS, body: JSON.stringify({ notes: $("notes").value }) });
      $("notes-status").textContent = "Saved";
    } catch (err) {
      $("notes-status").textContent = err.message;
    }
  }, 700);
}

// --- step 1: the key ---------------------------------------------------------------------------------------------------------

// A failure is shown twice: at the top of the page, as everywhere, and in the status line beside the button that was pressed, because the
// top of the page is often out of sight (a draft takes a minute, and people scroll away while it runs).
function reportError(err, statusEl) {
  Global.showMessage(err.message, "error");
  if (statusEl) {
    statusEl.textContent = err.message;
    statusEl.classList.add("is-error");
  }
}

function clearStatus(statusEl) {
  statusEl.classList.remove("is-error");
  statusEl.textContent = "";
}

async function loadKey() {
  try {
    keyStatus = await Global.withLoading($("key-body"), Global.fetchJSON("/api/api-key"));
  } catch (err) {
    Global.showMessage(err.message, "error");
  }
  renderKey();
  updateDraftButton();
}

function renderKey() {
  const body = $("key-body");
  $("key-done").classList.toggle("hidden", !keyStatus.usable);
  const parts = [];
  if (keyStatus.has_own_key && keyStatus.own_key_readable) {
    const remove = el("button", { type: "button", class: "cancel-btn", text: "Remove my key" });
    remove.addEventListener("click", async () => {
      if (!confirm("Remove your saved key? You can add it again any time.")) return;
      try {
        keyStatus = await Global.withLoading($("key-body"), Global.fetchJSON("/api/api-key", { method: "DELETE" }));
        renderKey();
        updateDraftButton();
      } catch (err) {
        Global.showMessage(err.message, "error");
      }
    });
    parts.push(el("p", { class: "hint", text: `Your key (ending ${keyStatus.last4}) is saved, encrypted. Only you can use it, and it is never shown again.` }), remove);
  } else {
    if (keyStatus.has_own_key) {
      parts.push(el("p", { class: "hint is-error", text: "A saved key couldn't be read (the server's secret changed). Paste it again." }));
    }
    if (keyStatus.using_house_key && keyStatus.system_key) {
      // Someone the administrator let fall back to the system key: say so plainly, and that their own key still takes over if they add one.
      const limit = keyStatus.system_runs_limit ? ` You have ${keyStatus.system_runs_left} of ${keyStatus.system_runs_limit} AI runs left this month.` : "";
      parts.push(el("p", { class: "hint", text: `The administrator is covering the AI cost for you for now, so you can skip this.${limit} Your text is still sent to Anthropic to write the draft. If you add your own key (a few dollars of credit on your own Anthropic account), it is used instead.` }));
    } else if (keyStatus.using_house_key) {
      parts.push(el("p", { class: "hint", text: "As an administrator you can use the server's key, so you can skip this. Add your own if you'd rather the cost went to your account." }));
    } else {
      parts.push(
        el("p", { class: "hint" }, [
          document.createTextNode("Drafting uses Anthropic's AI, billed to your own account. "),
          el("a", { href: keyStatus.get_url || "https://console.anthropic.com/settings/keys", target: "_blank", rel: "noopener noreferrer", text: "Create a key in the Anthropic console" }),
          document.createTextNode(" (add a few dollars of credit first), then paste it here. It is saved encrypted and only you can use it."),
        ])
      );
    }
    if (keyStatus.can_save === false) {
      parts.push(el("p", { class: "hint is-error", text: "Saving keys isn't set up on the server yet. Ask the administrator." }));
    } else {
      const input = el("input", {
        type: "password",
        placeholder: "Paste your Anthropic key",
        autocomplete: "off",
        "data-1p-ignore": "",
        "data-lpignore": "true",
        "data-bwignore": "true",
        "data-form-type": "other",
        "aria-label": "Your Anthropic key",
      });
      const save = el("button", { type: "button", class: "secondary-btn", "data-btn-icon": "lock", text: "Save key" });
      const problem = el("p", { class: "hint is-error hidden" });
      const submit = async () => {
        if (!input.value.trim()) return;
        save.disabled = true;
        problem.classList.add("hidden");
        try {
          const result = await Global.withLoading($("key-body"), Global.fetchJSON("/api/api-key", { method: "PUT", headers: JSON_HEADERS, body: JSON.stringify({ key: input.value }) }));
          keyStatus = result.status;
          if (result.note) Global.showMessage(result.note, "error");
          else Global.showMessage("Key saved.", "success");
          renderKey();
          updateDraftButton();
        } catch (err) {
          problem.textContent = err.message;
          problem.classList.remove("hidden");
          save.disabled = false;
        }
      };
      save.addEventListener("click", submit);
      input.addEventListener("keydown", (e) => e.key === "Enter" && (e.preventDefault(), submit()));
      parts.push(el("div", { class: "member-add" }, [input, save]), problem);
    }
  }
  body.replaceChildren(...parts);
  Global.iconifyButtons && Global.iconifyButtons(body);
}

// --- steps 2 and 3: the drop zones and the list of what was found --------------------------------------------------------------

function extension(name) {
  const dot = name.lastIndexOf(".");
  return dot < 0 ? "" : name.slice(dot).toLowerCase();
}

function mb(bytes) {
  return bytes >= 1024 * 1024 ? `${(bytes / (1024 * 1024)).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

function dropzone(host, { accept, prompt, multiple }) {
  const input = el("input", { type: "file", accept, ...(multiple ? { multiple: "" } : {}) });
  const zone = el("div", { class: "dropzone", tabindex: "0", role: "button", "aria-label": prompt }, [
    el("strong", { text: prompt }),
    el("span", { text: "or click to choose" }),
    input,
  ]);
  const message = el("div", { class: "page-message hidden", role: "status" });
  const take = (list) => addFiles([...list], message);
  zone.addEventListener("click", (e) => e.target !== input && input.click());
  zone.addEventListener("keydown", (e) => (e.key === "Enter" || e.key === " ") && (e.preventDefault(), input.click()));
  input.addEventListener("change", () => {
    take(input.files);
    input.value = "";
  });
  for (const type of ["dragenter", "dragover"]) {
    zone.addEventListener(type, (e) => {
      e.preventDefault();
      zone.classList.add("is-over");
    });
  }
  for (const type of ["dragleave", "drop"]) zone.addEventListener(type, () => zone.classList.remove("is-over"));
  zone.addEventListener("drop", (e) => {
    e.preventDefault();
    take(e.dataTransfer.files);
  });
  host.append(message, zone);
}

async function addFiles(list, message) {
  const problems = [];
  const accepted = [];
  for (const file of list) {
    if (!READABLE.includes(extension(file.name))) problems.push(`${file.name}: that kind of file can't be read (use PDF, Word, text, or LinkedIn's ZIP).`);
    else if (file.size > MAX_FILE_MB * 1024 * 1024) problems.push(`${file.name}: over ${MAX_FILE_MB} MB.`);
    else if (file.size === 0) problems.push(`${file.name}: it's empty.`);
    else accepted.push(file);
  }
  if (accepted.length && !keep) {
    localFiles.push(...accepted);
    localChecks = localFiles.map(() => null);
    Global.showMessage(`Added ${accepted.map((f) => f.name).join(", ")}. It is held in this page only; a refresh will remove it.`, "success", message);
    renderFound();
    updateDraftButton();
    checkLocal();
  } else if (accepted.length) {
    const data = new FormData();
    accepted.forEach((f) => data.append("sources", f));
    Global.showMessage(accepted.length === 1 ? `Reading ${accepted[0].name}...` : `Reading ${accepted.length} files...`, "success", message);
    try {
      const result = await Global.withLoading(message.parentElement, Global.withLoading($("found"), Global.fetchJSON("/api/builder/sources", { method: "POST", body: data })));
      sources = result.sources;
      for (const r of result.rejected) problems.push(`${r.name}: ${r.problem}`);
      if (result.added.length && !problems.length) {
        Global.showMessage(result.added.length === 1 ? `Added ${result.added[0].name} and saved it to your account.` : `Added ${result.added.length} files and saved them to your account.`, "success", message);
      }
    } catch (err) {
      problems.push(err.message);
    }
  }
  if (problems.length) Global.showMessage(problems.join(" "), "error", message);
  renderFound();
  updateDraftButton();
}

async function removeFile(source) {
  try {
    await Global.withLoading($("found"), Global.fetchJSON(`/api/builder/sources/${source.id}`, { method: "DELETE" }));
    sources = sources.filter((s) => s.id !== source.id);
  } catch (err) {
    Global.showMessage(err.message, "error");
  }
  renderFound();
  updateDraftButton();
}

// Not keeping: ask the server what it makes of the files held in this page (nothing is stored), so they are checked before a draft.
let checkRound = 0;
async function checkLocal() {
  const round = ++checkRound;
  if (!localFiles.length) return;
  const data = new FormData();
  localFiles.forEach((f) => data.append("sources", f));
  try {
    const result = await Global.withLoading($("found"), Global.fetchJSON("/api/builder/inspect", { method: "POST", body: data }));
    if (round !== checkRound) return;
    localChecks = result.files;
  } catch (err) {
    if (round !== checkRound) return;
    localChecks = localFiles.map(() => ({ ok: false, found: {}, summary: err.message }));
  }
  renderFound();
  updateDraftButton();
}

function removeLocal(index) {
  localFiles.splice(index, 1);
  localChecks = localFiles.map(() => null);
  renderFound();
  updateDraftButton();
  checkLocal();
}

async function loadSources() {
  try {
    sources = await Global.withLoading($("found"), Global.fetchJSON("/api/builder/sources"));
  } catch (err) {
    Global.showMessage(err.message, "error");
  }
  renderFound();
  updateDraftButton();
}

function when(iso) {
  return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}

function renderFound() {
  const host = $("found");
  // One row per file, whether it is kept on the account (sources) or held in this page only (localFiles).
  const rows = keep
    ? sources.map((source) => ({ name: source.name, label: `${source.name} (${mb(source.size_bytes)}, added ${when(source.added_at)})`, check: { ok: true, found: source.found, summary: source.summary }, remove: () => removeFile(source) }))
    : localFiles.map((file, i) => ({ name: file.name, label: `${file.name} (${mb(file.size)}, this page only)`, check: localChecks[i], remove: () => removeLocal(i) }));
  if (!rows.length) {
    host.replaceChildren(el("p", { class: "hint", text: keep ? "Nothing added yet. Files you add above are read and kept here." : "Nothing added yet. Files you add above are held in this page only." }));
    return;
  }
  host.replaceChildren(
    ...rows.map((row) => {
      const detail = el("div", { class: "found-detail" });
      const check = row.check;
      const parts = check ? Object.entries(check.found || {}).filter(([, n]) => n) : [];
      if (!check) detail.textContent = "Checking...";
      else if (!check.ok) {
        detail.classList.add("is-problem");
        detail.textContent = check.summary;
      } else if (parts.length) {
        detail.append(...parts.map(([part, n]) => el("span", { class: "chip", text: `${part}: ${n}` })));
        if (!check.found.Positions) detail.appendChild(el("div", { class: "is-problem", text: "No jobs (Positions) in it. LinkedIn's pick-individual-files option leaves jobs out. Request the larger data archive (the first option) and drop that ZIP here." }));
      } else {
        detail.textContent = check.summary;
      }
      const remove = el("button", { type: "button", class: "cancel-btn", text: "Remove", "aria-label": `Remove ${row.name}` });
      remove.addEventListener("click", row.remove);
      return el("div", { class: "found-row" }, [el("div", { class: "found-name", text: row.label }), detail, remove]);
    })
  );
  Global.iconifyButtons && Global.iconifyButtons(host);
}

// --- keeping or not keeping the files ---------------------------------------------------------------------------------------------

function draftOpen() {
  return !$("step-review").classList.contains("hidden");
}

function renderKeep() {
  $("keep-files").checked = keep;
  $("keep-hint").classList.toggle("keep-warning", !keep);
  $("keep-hint").textContent = keep
    ? "On: your files' text and your draft are saved, so you can come back and update your resume without adding anything again."
    : "Warning: keeping is off. Your files and draft are held in this page only. If you refresh or leave it, they are lost and you start over. Your engagements and notes are still saved.";
  renderFound();
}

async function changeKeep() {
  const wanted = $("keep-files").checked;
  if (!wanted) {
    // Always asked, even with nothing saved yet: this is the one switch that makes a refresh lose everything.
    const saved = sources.length ? `This deletes the ${sources.length} file${sources.length === 1 ? "" : "s"} saved on your account${draftOpen() ? " and your saved draft" : ""}. ` : draftOpen() ? "This deletes your saved draft. " : "";
    if (!confirm(`Turn off keeping your files?\n\n${saved}From now on your files and draft are held in this page only. If you refresh or leave it, they are lost and you start over.\n\nYour engagements and notes are still saved.`)) {
      $("keep-files").checked = true;
      return;
    }
  }
  try {
    await Global.withLoading($("step-privacy"), Global.fetchJSON("/api/builder/keep", { method: "PUT", headers: JSON_HEADERS, body: JSON.stringify({ keep: wanted }) }));
  } catch (err) {
    Global.showMessage(err.message, "error");
    $("keep-files").checked = keep;
    return;
  }
  keep = wanted;
  clearTimeout(draftTimer);
  if (!keep) sources = [];
  if (keep && localFiles.length) {
    // They turned keeping back on: what this page was holding is saved now.
    const data = new FormData();
    localFiles.forEach((f) => data.append("sources", f));
    localFiles = [];
    localChecks = [];
    try {
      sources = (await Global.withLoading($("step-privacy"), Global.fetchJSON("/api/builder/sources", { method: "POST", body: data }))).sources;
    } catch (err) {
      Global.showMessage(err.message, "error");
    }
  }
  if (keep && $("draft-yaml").value.trim()) saveDraftSoon();
  renderKeep();
  updateDraftButton();
}

// --- step 4: engagements (saved to the account) --------------------------------------------------------------------------------

const INDUSTRIES = [
  "Banking", "Insurance", "Health insurance", "Healthcare", "Pharma and life sciences", "Government", "Defense", "Education", "Retail",
  "E-commerce", "Telecom", "Energy and utilities", "Manufacturing", "Logistics and transport", "Media and entertainment", "Travel and hospitality",
  "Real estate", "Software and technology", "Non-profit", "Consulting",
];

// Back to the top of the engagements list (or, with none, the proposals under it, or failing that the step itself). Reading a pasted list swaps a tall box for a
// different one, which leaves the page wherever the browser lands, often down at a later step; this puts the person where the result is.
function scrollToEngagements() {
  const target = $("engagements").children.length ? $("engagements") : $("import-box").children.length ? $("import-box") : $("step-work");
  requestAnimationFrame(() => target.scrollIntoView({ behavior: "smooth", block: "start" }));
}

async function loadEngagements() {
  try {
    engagements = await Global.withLoading($("engagements"), Global.fetchJSON("/api/engagements"));
  } catch (err) {
    Global.showMessage(err.message, "error");
  }
  renderEngagements();
}

function employerNames() {
  return [...new Set([...targetEmployers, ...engagements.map((e) => e.employer)].filter(Boolean))];
}

function renderEngagements() {
  const host = $("engagements");
  host.replaceChildren(
    ...engagements.map((e) => {
      const sub = [e.employer && `at ${e.employer}`, e.industry, e.role, [e.start, e.end].filter(Boolean).join(" - ")].filter(Boolean).join(" · ");
      const edit = el("button", { type: "button", class: "secondary-btn", "data-btn-icon": "edit", text: "Edit" });
      const remove = el("button", { type: "button", class: "cancel-btn", text: "Remove", "aria-label": `Remove ${e.client}` });
      edit.addEventListener("click", () => openEngagementForm(e));
      remove.addEventListener("click", async () => {
        if (!confirm(`Remove "${e.client}"? It won't be used in future drafts.`)) return;
        try {
          await Global.withLoading($("engagements"), Global.fetchJSON(`/api/engagements/${e.id}`, { method: "DELETE" }));
          await loadEngagements();
        } catch (err) {
          Global.showMessage(err.message, "error");
        }
      });
      return el("div", { class: "engagement-card" }, [
        el("div", { class: "engagement-main" }, [
          el("strong", { text: e.client + (e.confidential ? " (not named on printed copies)" : "") }),
          ...(sub ? [el("div", { class: "engagement-sub", text: sub })] : []),
          el("div", {}, e.technologies.map((t) => el("span", { class: "chip", text: t }))),
        ]),
        edit,
        remove,
      ]);
    })
  );
  Global.iconifyButtons && Global.iconifyButtons(host);
  $("remove-all-box").classList.toggle("hidden", engagements.length < 2); // one is removed with its own Remove button
  updateDraftButton();
}

async function removeAllEngagements() {
  const n = engagements.length;
  if (!n) return;
  if (!confirm(`Remove ALL ${n} of your engagements?\n\nThis deletes every one of them from your account and cannot be undone. Your LinkedIn data, old resumes and notes are not touched. Future drafts will not use these engagements unless you add them again.`)) return;
  if (!confirm(`Last check: really delete all ${n} engagements?`)) return;
  const button = $("remove-all-engagements");
  button.disabled = true;
  clearStatus($("remove-all-status"));
  try {
    const result = await Global.withLoading($("engagements"), Global.fetchJSON("/api/engagements?confirm=all", { method: "DELETE" }));
    Global.showMessage(`Removed ${result.removed} engagement${result.removed === 1 ? "" : "s"}.`, "success");
  } catch (err) {
    reportError(err, $("remove-all-status"));
  } finally {
    button.disabled = false;
    await loadEngagements();
    scrollToEngagements();
  }
}

// A text box that turns what you type into removable chips (Enter or a comma adds one).
function tagInput(initial, label) {
  let tags = [...initial];
  const box = el("div", { class: "tag-input" });
  const input = el("input", { type: "text", placeholder: "Type one, then Enter", maxlength: "60", "aria-label": label });
  const draw = () => {
    box.replaceChildren(
      ...tags.map((tag, i) => {
        const x = el("button", { type: "button", "aria-label": `Remove ${tag}`, text: "x" });
        x.addEventListener("click", () => {
          tags.splice(i, 1);
          draw();
        });
        return el("span", { class: "chip" }, [document.createTextNode(tag), x]);
      }),
      input
    );
  };
  const take = () => {
    for (const piece of input.value.split(",")) {
      const text = piece.trim();
      if (text && !tags.includes(text) && tags.length < 30) tags.push(text);
    }
    input.value = "";
    draw();
  };
  input.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter" || ev.key === ",") {
      ev.preventDefault();
      take();
    } else if (ev.key === "Backspace" && !input.value && tags.length) {
      tags.pop();
      draw();
    }
  });
  input.addEventListener("blur", () => input.value.trim() && take());
  draw();
  return { box, value: () => (take(), tags) };
}

function openEngagementForm(existingEntry) {
  const entry = existingEntry || { employer: "", client: "", client_does: "", industry: "", regulated: "unknown", regulators: "", technologies: [], role: "", start: "", end: "", outcomes: [], confidential: false };
  const text = (label, key, placeholder, hint) => {
    const input = el("input", { type: "text", maxlength: "300", placeholder, value: entry[key] || "", "aria-label": label });
    return { input, field: el("div", { class: "field" }, [el("label", { text: label }), input, ...(hint ? [el("span", { class: "hint", text: hint })] : [])]) };
  };
  const employer = text("Which job was this part of?", "employer", "The company, as on your resume", "Matches it to the right job on your resume.");
  Global.suggest(employer.input, { items: () => employerNames().map((name) => ({ value: name })) });
  const client = text("Client or project *", "client", "e.g. Blue Harbor Insurance");
  const does = text("What do they do?", "client_does", "e.g. Sells health plans to small employers");
  const industry = text("Industry", "industry", "e.g. Health insurance");
  Global.suggest(industry.input, { items: () => INDUSTRIES.map((name) => ({ value: name })) });
  const role = text("Your role", "role", "e.g. Tech lead");
  const start = text("Started", "start", "e.g. Mar 2023");
  const end = text("Ended", "end", "e.g. Present");

  const regulated = el("select", { "aria-label": "Was it regulated?" }, [
    el("option", { value: "unknown", text: "Not sure" }),
    el("option", { value: "no", text: "No" }),
    el("option", { value: "yes", text: "Yes" }),
  ]);
  regulated.value = entry.regulated;
  const regulators = el("input", { type: "text", maxlength: "300", placeholder: "By whom: HIPAA, SOX, FDA...", value: entry.regulators || "", "aria-label": "Regulated by" });
  const syncRegulators = () => regulators.classList.toggle("hidden", regulated.value !== "yes");
  regulated.addEventListener("change", syncRegulators);
  syncRegulators();

  const tech = tagInput(entry.technologies, "Technologies");
  const outcomes = el("textarea", { rows: "3", placeholder: "One result per line, with numbers if you have them", "aria-label": "What came of it" });
  outcomes.value = entry.outcomes.join("\n");
  const confidential = el("input", { type: "checkbox", id: "eng-confidential" });
  confidential.checked = entry.confidential;

  const save = el("button", { type: "button", class: "save-btn", text: existingEntry ? "Save changes" : "Add engagement" });
  const cancel = el("button", { type: "button", class: "cancel-btn", text: "Cancel" });
  const problem = el("p", { class: "hint is-error hidden" });
  const form = el("div", { class: "engagement" }, [
    el("div", { class: "field-row" }, [client.field, employer.field]),
    el("div", { class: "field-row" }, [does.field, industry.field]),
    el("div", { class: "field-row" }, [
      el("div", { class: "field" }, [el("label", { text: "Was it regulated?" }), regulated, regulators]),
      role.field,
    ]),
    el("div", { class: "field-row" }, [start.field, end.field]),
    el("div", { class: "field" }, [el("label", { text: "Technologies" }), tech.box]),
    el("div", { class: "field" }, [el("label", { text: "What came of it?" }), outcomes]),
    el("div", { class: "checkbox-field field" }, [confidential, el("label", { for: "eng-confidential", text: "Don't name this client on anything printed (NDA)" })]),
    problem,
    el("div", { class: "item-actions" }, [save, cancel]),
  ]);
  const close = () => {
    $("engagement-form").replaceChildren();
    $("add-buttons").classList.remove("hidden");
  };
  cancel.addEventListener("click", close);
  save.addEventListener("click", async () => {
    problem.classList.add("hidden");
    const body = {
      employer: employer.input.value,
      client: client.input.value,
      client_does: does.input.value,
      industry: industry.input.value,
      regulated: regulated.value,
      regulators: regulators.value,
      technologies: tech.value(),
      role: role.input.value,
      start: start.input.value,
      end: end.input.value,
      outcomes: outcomes.value.split("\n"),
      confidential: confidential.checked,
    };
    save.disabled = true;
    try {
      await Global.withLoading(
        $("engagement-form"),
        Global.fetchJSON(existingEntry ? `/api/engagements/${existingEntry.id}` : "/api/engagements", {
          method: existingEntry ? "PUT" : "POST",
          headers: JSON_HEADERS,
          body: JSON.stringify(body),
        })
      );
      close();
      await loadEngagements();
    } catch (err) {
      problem.textContent = err.message;
      problem.classList.remove("hidden");
      save.disabled = false;
    }
  });
  $("add-buttons").classList.add("hidden");
  $("import-box").replaceChildren();
  $("engagement-form").replaceChildren(form);
  Global.iconifyButtons && Global.iconifyButtons(form);
  client.input.focus();
}

// Paste a whole list of clients (an outline from a notes app, say) and have Claude propose one engagement per client. Nothing is saved
// until the person ticks the ones they want and presses Add; each can then be opened and completed with the ordinary form.
function openImport() {
  const sameAs = (a, b) => a.client.trim().toLowerCase() === b.client.trim().toLowerCase() && (a.employer || "").trim().toLowerCase() === (b.employer || "").trim().toLowerCase();
  const box = $("import-box");
  const close = () => {
    box.replaceChildren();
    $("add-buttons").classList.remove("hidden");
  };
  const text = el("textarea", { rows: "12", placeholder: "Paste your list here: one client or project per item, with what you did underneath.", "aria-label": "Your list of clients and projects" });
  const problem = el("p", { class: "hint is-error hidden" });
  const status = el("span", { class: "status", role: "status" });
  const find = el("button", { type: "button", class: "save-btn", "data-btn-icon": "zap", text: "Find the clients" });
  const cancel = el("button", { type: "button", class: "cancel-btn", text: "Cancel" });

  const review = (proposed, room) => {
    const picked = proposed.map((p) => !engagements.some((e) => sameAs(e, p)));
    const count = el("p", { class: "hint" });
    const add = el("button", { type: "button", class: "save-btn", text: "Add the selected" });
    const back = el("button", { type: "button", class: "cancel-btn", text: "Back to the list" });
    const err = el("p", { class: "hint is-error hidden" });
    const refresh = () => {
      const n = picked.filter(Boolean).length;
      count.textContent = `${n} of ${proposed.length} selected.` + (n > room ? ` You can keep ${room} more engagements, so remove some ticks or delete older ones first.` : "");
      add.disabled = n === 0 || n > room;
    };
    const cards = proposed.map((p, i) => {
      const tick = el("input", { type: "checkbox", id: `imp-${i}`, "aria-label": `Add ${p.client}` });
      tick.checked = picked[i];
      tick.addEventListener("change", () => {
        picked[i] = tick.checked;
        refresh();
      });
      const dupe = engagements.some((e) => sameAs(e, p));
      const sub = [p.employer && `at ${p.employer}`, p.industry, p.role, [p.start, p.end].filter(Boolean).join(" - ")].filter(Boolean).join(" · ");
      return el("div", { class: "engagement-card" }, [
        tick,
        el("label", { class: "engagement-main", for: `imp-${i}` }, [
          el("strong", { text: p.client }),
          ...(dupe ? [el("span", { class: "hint", text: " (already added, so not ticked)" })] : []),
          ...(sub ? [el("div", { class: "engagement-sub", text: sub })] : []),
          ...(p.outcomes.length ? [el("div", { class: "engagement-sub", text: `${p.outcomes.length} point${p.outcomes.length === 1 ? "" : "s"}: ${p.outcomes[0]}${p.outcomes.length > 1 ? " ..." : ""}` })] : []),
          el("div", {}, p.technologies.map((t) => el("span", { class: "chip", text: t }))),
        ]),
      ]);
    });
    back.addEventListener("click", () => {
      box.replaceChildren(form);
    });
    add.addEventListener("click", async () => {
      err.classList.add("hidden");
      add.disabled = true;
      let added = 0;
      const failed = [];
      await Global.withLoading(box, async () => {
        for (let i = 0; i < proposed.length; i++) {
          if (!picked[i]) continue;
          try {
            await Global.fetchJSON("/api/engagements", { method: "POST", headers: JSON_HEADERS, body: JSON.stringify(proposed[i]) });
            added++;
          } catch (e) {
            failed.push(`${proposed[i].client}: ${e.message}`);
          }
        }
      });
      await loadEngagements();
      if (failed.length) {
        err.textContent = `Added ${added}, but not: ${failed.join("; ")}`;
        err.classList.remove("hidden");
        add.disabled = false;
        return;
      }
      close();
      scrollToEngagements();
      Global.showMessage(`Added ${added} engagement${added === 1 ? "" : "s"}. Open any of them to fill in dates, your role or the industry.`, "success");
    });
    const view = el("div", { class: "engagement" }, [
      el("p", { class: "hint", text: "Here is what we found. Untick anything that is not a client or project, then add them. Nothing is saved until you press Add." }),
      ...cards, count, err,
      el("div", { class: "item-actions" }, [add, back, cancel]),
    ]);
    refresh();
    Global.iconifyButtons && Global.iconifyButtons(view);
    box.replaceChildren(view);
    scrollToEngagements();
  };

  const form = el("div", { class: "engagement" }, [
    el("p", { class: "hint", text: "Copy it from wherever you keep it (numbered lists, bullets and indents are all fine). We read it with your own Anthropic key and propose one engagement per client for you to check. We only use what the list says; nothing is invented, and nothing is saved until you confirm." }),
    el("div", { class: "field" }, [text]),
    problem,
    el("div", { class: "item-actions" }, [find, cancel, status]),
  ]);
  cancel.addEventListener("click", close);
  find.addEventListener("click", async () => {
    problem.classList.add("hidden");
    if (!text.value.trim()) {
      problem.textContent = "Paste your list first.";
      problem.classList.remove("hidden");
      return;
    }
    find.disabled = true;
    status.textContent = "Reading your list. This usually takes under a minute.";
    try {
      const result = await Global.withLoading(box, Global.fetchJSON("/api/engagements/import", { method: "POST", headers: JSON_HEADERS, body: JSON.stringify({ text: text.value }) }));
      review(result.engagements, result.room);
    } catch (err) {
      problem.textContent = err.message;
      problem.classList.remove("hidden");
    } finally {
      find.disabled = false;
      status.textContent = "";
    }
  });
  $("add-buttons").classList.add("hidden");
  $("engagement-form").replaceChildren();
  box.replaceChildren(form);
  Global.iconifyButtons && Global.iconifyButtons(form);
  text.focus();
}

// --- step 5: what to do (new draft or update), and draft ------------------------------------------------------------------------

async function loadTarget() {
  baseYaml = null;
  targetEmployers = [];
  if (!target) return;
  try {
    await Global.withLoading($("mode"), async () => {
      const full = await Global.fetchJSON(`/api/people/${target.id}`);
      baseYaml = full.resume_yaml;
      const summary = await Global.fetchJSON("/api/builder/preview", { method: "POST", headers: JSON_HEADERS, body: JSON.stringify({ resume_yaml: baseYaml }) });
      if (summary.ok) targetEmployers = summary.jobs.map((j) => j.company).filter(Boolean);
    });
  } catch (err) {
    Global.showMessage(err.message, "error");
  }
}

function renderMode() {
  const host = $("mode");
  if (!myPeople.length) {
    host.replaceChildren();
    return;
  }
  const name = "mode";
  const options = [];
  const radio = (value, label, checked) => {
    const input = el("input", { type: "radio", name, value, id: `mode-${value}` });
    input.checked = checked;
    input.addEventListener("change", async () => {
      target = value === "new" ? null : myPeople.find((p) => String(p.id) === value) || null;
      await loadTarget();
      updateDraftButton();
    });
    return el("div", { class: "radio-line" }, [input, el("label", { for: `mode-${value}`, text: label })]);
  };
  for (const person of myPeople) {
    options.push(radio(String(person.id), `Update "${person.name}" with everything saved here. Your edits are kept; only what's new or changed is merged in.`, target && target.id === person.id));
  }
  options.push(radio("new", me.is_admin ? "Write a brand new person instead" : "Start fresh from everything saved here (replaces your resume only when you Save)", !target));
  host.replaceChildren(el("label", { text: "What should this do?" }), ...options);
}

function engagementsCount() {
  return engagements.length;
}

function hasMaterial() {
  return sources.length > 0 || localChecks.some((c) => c && c.ok) || engagementsCount() > 0 || $("notes").value.trim().length > 0;
}

function updateDraftButton() {
  let why = "";
  if (!keyStatus.usable) why = "Add your Anthropic key in step 1 first.";
  else if (!hasMaterial()) why = "Add a LinkedIn ZIP, an old resume, an engagement or a note first.";
  $("draft-btn").disabled = Boolean(why);
  $("draft-why").textContent = why;
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const POLL_MS = 3000;
const POLL_FAILURES_ALLOWED = 5; // a few failed checks in a row are a bad connection, not a failed draft

async function draft() {
  if ($("draft-yaml").value.trim() && !$("step-review").classList.contains("hidden") && !confirm("This replaces the draft you are working on below. Continue?")) return;
  const data = new FormData(); // the files, engagements and notes are already saved; the server drafts from those
  if (!keep) localFiles.forEach((f, i) => (!localChecks[i] || localChecks[i].ok) && data.append("sources", f)); // not kept: they ride with the draft
  if (target) data.append("update_person_id", String(target.id));
  $("draft-btn").disabled = true;
  clearStatus($("draft-status"));
  clearTimeout(notesTimer);
  try {
    // The notes are saved first so the draft uses what is on screen (engagements are already saved as they are made).
    await Global.fetchJSON("/api/builder/notes", { method: "PUT", headers: JSON_HEADERS, body: JSON.stringify({ notes: $("notes").value }) });
    // Starting returns at once; the writing happens on the server and is asked about every few seconds. One long request would be cut
    // off by the proxy in front of the app after 100 seconds, which is shorter than a long resume takes.
    await waitForDraft(await Global.fetchJSON("/api/generate-resume", { method: "POST", body: data }));
  } catch (err) {
    reportError(err, $("draft-status"));
  } finally {
    updateDraftButton();
  }
}

// Follow a draft that is running on the server until it ends, then show it (or throw why not). The spinner stays up the whole time.
async function waitForDraft(job) {
  const startedAt = Date.now() - (job.elapsed || 0) * 1000;
  let failures = 0;
  await Global.withLoading($("step-draft"), async () => {
    while (job.state === "running") {
      const seconds = Math.round((Date.now() - startedAt) / 1000);
      $("draft-status").textContent = `Drafting (${seconds} seconds so far). This usually takes a minute or two. You can leave this page or reload it; the draft keeps going.`;
      await sleep(POLL_MS);
      try {
        job = await Global.fetchJSON("/api/generate-resume/status");
        failures = 0;
      } catch (err) {
        if (++failures >= POLL_FAILURES_ALLOWED) throw err;
      }
    }
  });
  clearStatus($("draft-status"));
  const collect = () => Global.fetchJSON("/api/generate-resume/status", { method: "DELETE" }).catch(() => {}); // forget it on the server: it has been seen
  if (job.state === "idle") throw new Error("The draft was interrupted (the server restarted while it was writing). Press Draft my resume again.");
  if (job.state === "error") {
    await collect();
    throw new Error(job.error);
  }
  await showReview(job.resume_yaml);
  $("draft-note").classList.add("hidden");
  await collect();
  Global.showMessage(job.update_person_id ? "Update ready. Look at what changed, fix anything, then Save." : "Draft ready. Read it through below, fix anything, then Save.", "success");
}

// On opening the page: a draft started earlier may still be running (or have just finished) on the server. Pick it up. True if it was shown.
async function resumePendingDraft() {
  let job;
  try {
    job = await Global.fetchJSON("/api/generate-resume/status");
  } catch (err) {
    return false;
  }
  if (job.state === "idle") return false;
  if (job.update_person_id) {
    target = myPeople.find((p) => p.id === job.update_person_id) || target;
    await loadTarget();
    renderMode();
  }
  $("draft-btn").disabled = true;
  try {
    await waitForDraft(job);
    return true;
  } catch (err) {
    reportError(err, $("draft-status"));
    return false; // an older kept draft, if there is one, is still shown by restoreDraft
  } finally {
    updateDraftButton();
  }
}

// --- step 6: review and save ---------------------------------------------------------------------------------------------------

let previewRound = 0;
let previewTimer = null;
let lastPreview = { ok: false };

async function showReview(yamlText, { scroll = true, keepReview = false } = {}) {
  if (!keepReview) rememberReview(null); // a fresh draft has not been reviewed
  $("step-review").classList.remove("hidden");
  const box = $("draft-yaml");
  box.value = yamlText;
  box.scrollTop = 0; // a re-draft must not open partway down the text or the summary of the one before
  box.setSelectionRange(0, 0);
  $("preview").scrollTop = 0;
  renderSaveFields();
  await refreshPreview(); // so the page has its final height before it scrolls
  // In a frame, so it happens when the tab is actually showing (a draft takes a minute, and people look at something else meanwhile).
  if (scroll) requestAnimationFrame(() => $("step-review").scrollIntoView({ behavior: "smooth", block: "start" }));
}

// The draft being reviewed is kept on the server as it is edited, so a reload or coming back tomorrow does not lose it.
function saveDraftSoon() {
  clearTimeout(draftTimer);
  if (!keep) return;
  draftTimer = setTimeout(async () => {
    try {
      await Global.fetchJSON("/api/builder/draft", { method: "PUT", headers: JSON_HEADERS, body: JSON.stringify({ resume_yaml: $("draft-yaml").value, person_id: target ? target.id : null }) });
      $("save-status").textContent = "Draft kept";
    } catch (err) {
      $("save-status").textContent = `Couldn't keep this draft: ${err.message}`;
    }
  }, 1000);
}

async function clearDraft() {
  clearTimeout(draftTimer);
  rememberReview(null);
  try {
    await Global.fetchJSON("/api/builder/draft", { method: "DELETE" });
  } catch (err) {
    Global.showMessage(err.message, "error");
  }
}

async function discardDraft() {
  if (!confirm("Throw away this draft? Your files, engagements and notes stay; you can draft again any time.")) return;
  await clearDraft();
  $("draft-yaml").value = "";
  $("draft-note").classList.add("hidden");
  $("step-review").classList.add("hidden");
  $("save-status").textContent = "";
}

const RECENT_DRAFT_MS = 10 * 60 * 1000;

async function restoreDraft() {
  recallReview();
  let saved = null;
  try {
    saved = (await Global.fetchJSON("/api/builder/draft")).draft;
  } catch (err) {
    Global.showMessage(err.message, "error");
  }
  if (!saved) return;
  target = saved.person_id ? myPeople.find((p) => p.id === saved.person_id) || null : null;
  await loadTarget();
  renderMode();
  // A draft touched in the last few minutes is one the person is in the middle of (a page reloaded while it was being written, or just
  // after): take them to it. An older one waiting from another day does not pull the page down past the earlier steps.
  const recent = Date.now() - Date.parse(saved.updated_at) < RECENT_DRAFT_MS;
  await showReview(saved.resume_yaml, { scroll: recent, keepReview: true });
  const note = $("draft-note");
  note.textContent = `This is the draft you were working on (last changed ${new Date(saved.updated_at).toLocaleString()}). It is not saved as your resume yet.`;
  note.classList.remove("hidden");
}

async function refreshPreview() {
  const round = ++previewRound;
  let result;
  try {
    result = await Global.withLoading($("preview"), Global.fetchJSON("/api/builder/preview", { method: "POST", headers: JSON_HEADERS, body: JSON.stringify({ resume_yaml: $("draft-yaml").value, base_yaml: target ? baseYaml : null }) }));
  } catch (err) {
    result = { ok: false, error: err.message };
  }
  if (round !== previewRound) return;
  lastPreview = result;
  const host = $("preview");
  if (!result.ok) {
    host.replaceChildren(el("p", { class: "preview-error", text: result.error }));
    $("save-btn").disabled = true;
    $("feedback-box").classList.add("hidden");
    return;
  }
  $("save-btn").disabled = false;
  renderDiff(result.changes);
  renderSuggestions(result.suggestions || []);
  renderAiReview();
  const nameField = document.getElementById("save-name");
  if (nameField && !nameField.dataset.touched) nameField.value = result.name || "";
  host.replaceChildren(
    el("strong", { text: result.name || "(no name)" }),
    ...(result.title ? [el("div", { class: "hint", text: result.title })] : []),
    el("div", {}, Object.entries(result.counts).filter(([, n]) => n).map(([label, n]) => el("span", { class: "chip", text: `${label}: ${n}` }))),
    ...result.jobs.flatMap((job) => [
      el("h3", { text: [job.title, job.company].filter(Boolean).join(" at ") }),
      el("div", { class: "hint", text: job.dates }),
      el("ul", {}, job.bullets.map((b) => el("li", { text: b }))),
    ])
  );
}

// Quick rule-based suggestions (the server sends them with every preview) and, on request, an AI editor's. Neither changes the draft.
function renderSuggestions(list) {
  $("feedback-box").classList.remove("hidden");
  const host = $("suggestions");
  if (!list.length) {
    host.replaceChildren(el("li", { class: "suggestion ok", text: "Nothing to flag from the quick checks." }));
    return;
  }
  host.replaceChildren(
    ...list.map((s) =>
      clickable(
        el("li", { class: `suggestion ${s.level}` }, [
          el("span", { class: "suggestion-tag", text: s.level === "fix" ? "Fix" : "Tip" }),
          el("strong", { text: s.where }),
          el("span", { text: ` ${s.text}` }),
        ]),
        s.find,
        s.nth
      )
    )
  );
}

// A suggestion that can be clicked (or reached with the keyboard) to show the words it is about in the YAML box.
function clickable(item, finds, nth = 0) {
  item.classList.add("jumps");
  item.tabIndex = 0;
  item.setAttribute("role", "button");
  item.title = "Show this in the box above";
  const go = () => jumpToText(finds, nth);
  item.addEventListener("click", go);
  item.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      go();
    }
  });
  return item;
}

// What an AI suggestion is about, most exact first: its quote, then the job (the company in "Title at Company"), then the section.
function aiFinds(s) {
  const where = (s.where || "").trim();
  const split = where.lastIndexOf(" at ");
  const title = split > 0 ? where.slice(0, split).trim() : "";
  const company = split > 0 ? where.slice(split + 4).trim() : "";
  const sections = ["summary", "skills", "education", "certifications", "projects", "experience"];
  const section = sections.includes(where.toLowerCase()) ? `${where.toLowerCase()}:` : "";
  // The title line comes before the company line: one company can have several jobs, but each has its own title.
  return [s.quote, title && `title: ${title}`, company && `company: ${company}`, company, section, company ? "experience:" : "", "basics:"].filter(Boolean);
}

// The letters and digits of a text, lowercased, with where each one sat in the original. Searching these instead of the raw text means
// line breaks, indentation, quotes, escapes and punctuation in the YAML never stop a match (a long bullet is often wrapped over lines).
function squash(text) {
  const chars = [];
  const at = [];
  for (let i = 0; i < text.length; i++) {
    for (const ch of text[i].toLowerCase()) {
      if (/[\p{L}\p{N}]/u.test(ch)) {
        chars.push(ch);
        at.push(i);
      }
    }
  }
  return { s: chars.join(""), at };
}

// [start, end) in the box text of `needle`, or null. A bare key ("summary:") is that key at the start of a line; anything else is
// words, matched ignoring spacing and punctuation. A phrase (4+ words, such as a bullet or an AI quote that may be slightly reworded)
// that is not found whole is looked for in shrinking runs of its words. `nth` picks the nth match (two jobs at one company).
function findRange(text, hay, needle, nth) {
  if (/^[a-z_]+:$/i.test(needle)) {
    const re = new RegExp(`^[ \\t-]*(${needle.slice(0, -1)}):`, "gmi");
    const found = [...text.matchAll(re)];
    if (!found.length) return null;
    const m = found[Math.min(nth, found.length - 1)];
    const start = m.index + m[0].indexOf(m[1]);
    return [start, start + needle.length];
  }
  const locate = (sq) => {
    if (sq.length < 3) return null;
    const starts = [];
    for (let i = hay.s.indexOf(sq); i >= 0; i = hay.s.indexOf(sq, i + 1)) starts.push(i);
    if (!starts.length) return null;
    const i = starts[Math.min(nth, starts.length - 1)];
    return [hay.at[i], hay.at[i + sq.length - 1] + 1];
  };
  const whole = locate(squash(needle).s);
  if (whole) return whole;
  const words = needle.split(/\s+/).map((w) => squash(w).s).filter(Boolean);
  if (words.length < 4) return null;
  for (let size = words.length - 1; size >= 3; size--) {
    for (let from = 0; from + size <= words.length; from++) {
      const chunk = words.slice(from, from + size).join("");
      const found = chunk.length >= 12 ? locate(chunk) : null;
      if (found) return found;
    }
  }
  return null;
}

// Select the first of `finds` that is in the draft box and scroll the box to it. If none is (the text was edited away), it still lands
// on the top of the box rather than doing nothing.
function jumpToText(finds, nth = 0) {
  const box = $("draft-yaml");
  const hay = squash(box.value);
  let range = null;
  for (const find of finds || []) {
    range = findRange(box.value, hay, (find || "").trim(), nth);
    if (range) break;
  }
  const exact = Boolean(range);
  if (!range) range = [0, (box.value.indexOf("\n") + 1 || box.value.length + 1) - 1];
  box.scrollIntoView({ behavior: "smooth", block: "center" });
  box.focus({ preventScroll: true });
  box.setSelectionRange(range[0], range[1]);
  const line = box.value.slice(0, range[0]).split("\n").length - 1;
  const style = getComputedStyle(box);
  const lineHeight = parseFloat(style.lineHeight) || parseFloat(style.fontSize) * 1.3;
  box.scrollTop = Math.max(0, line * lineHeight - box.clientHeight / 3);
  if (!exact) Global.showMessage("That part of the draft has been edited, so this is the top of the box.", "error");
}

// The AI review is remembered in this browser (a convenience, not part of the draft), with the text it was about, so after a reload it
// is still there and can say whether the draft has been edited since.
const REVIEW_KEY = "resume-ai-review";
let aiReview = null; // {suggestions, at, yaml} or null

function rememberReview(review) {
  aiReview = review;
  try {
    if (review) localStorage.setItem(REVIEW_KEY, JSON.stringify(review));
    else localStorage.removeItem(REVIEW_KEY);
  } catch (err) {
    /* private window or blocked storage: the review simply is not kept across a reload */
  }
}

function recallReview() {
  try {
    const saved = JSON.parse(localStorage.getItem(REVIEW_KEY) || "null");
    if (saved && Array.isArray(saved.suggestions) && typeof saved.yaml === "string") aiReview = saved;
  } catch (err) {
    aiReview = null;
  }
}

function renderAiReview() {
  const host = $("ai-suggestions");
  const status = $("review-status");
  if (!aiReview) {
    host.replaceChildren();
    status.textContent = "";
    return;
  }
  const n = aiReview.suggestions.length;
  const edited = aiReview.yaml !== $("draft-yaml").value;
  const when = new Date(aiReview.at).toLocaleString();
  status.textContent = `${n ? `${n} suggestion${n === 1 ? "" : "s"} from the AI review` : "The AI review found nothing worth raising"} (${when}).${edited ? " You have edited the draft since, so some may be out of date: review again to refresh." : ""}`;
  host.replaceChildren(
    ...aiReview.suggestions.map((s) =>
      clickable(
        el("li", { class: "suggestion ai" }, [
          el("span", { class: "suggestion-tag", text: "AI" }),
          el("strong", { text: s.where }),
          el("span", { text: ` ${s.issue}` }),
          ...(s.suggestion ? [el("div", { class: "hint", text: s.suggestion })] : []),
        ]),
        aiFinds(s)
      )
    )
  );
}

async function reviewWithAI() {
  const button = $("review-btn");
  button.disabled = true;
  clearStatus($("review-status"));
  $("review-status").textContent = "Reading your resume...";
  $("ai-suggestions").replaceChildren();
  const text = $("draft-yaml").value;
  let failure = null;
  try {
    const result = await Global.withLoading($("ai-suggestions"), Global.fetchJSON("/api/builder/review", { method: "POST", headers: JSON_HEADERS, body: JSON.stringify({ resume_yaml: text }) }));
    rememberReview({ suggestions: result.suggestions, at: new Date().toISOString(), yaml: text });
  } catch (err) {
    failure = err;
  } finally {
    button.disabled = false;
    renderAiReview();
    if (failure) reportError(failure, $("review-status")); // after the render, which writes the status line itself
  }
}

function renderDiff(changes) {
  $("diff-box").classList.toggle("hidden", !changes);
  if (!changes) return;
  const box = $("diff");
  if (!changes.length) {
    box.replaceChildren(document.createTextNode("No changes: the draft matches your current resume."));
    return;
  }
  box.replaceChildren(
    ...changes
      .filter((c) => c.op !== "@")
      .map((c) => el("div", { class: c.op === "+" ? "add" : c.op === "-" ? "del" : "ctx", text: `${c.op === " " ? " " : c.op} ${c.text}` }))
  );
}

function renderSaveFields() {
  const host = $("save-fields");
  const fields = [];
  if (me.is_admin && !target) {
    // Administrators may keep several resumes; each needs a name and a short address name. It starts private; publishing is on the People page.
    const name = el("input", { type: "text", id: "save-name", placeholder: "Name", "aria-label": "Name for this resume" });
    name.addEventListener("input", () => (name.dataset.touched = "1"));
    const slug = el("input", { type: "text", id: "save-slug", placeholder: "short-name (lowercase, no spaces)", "aria-label": "Short name for the address" });
    fields.push(el("div", { class: "field" }, [el("label", { text: "Name" }), name]), el("div", { class: "field" }, [el("label", { text: "Short name" }), slug]));
  }
  host.replaceChildren(...fields);
  const button = $("save-btn");
  button.textContent = target ? `Save as the new "${target.name}"` : existing && !me.is_admin ? "Replace my resume with this" : "Save as my resume";
  button.setAttribute("data-btn-icon", "check");
  Global.iconifyButtons && Global.iconifyButtons($("step-review"));
}

async function save() {
  if (!lastPreview.ok) return;
  const yamlText = $("draft-yaml").value;
  const replacing = target || (existing && !me.is_admin ? existing : null);
  if (replacing && !confirm(`This replaces your current resume, "${replacing.name}", with what is shown. Continue?`)) return;
  $("save-btn").disabled = true;
  clearStatus($("save-status"));
  $("save-status").textContent = "Saving...";
  try {
    await Global.withLoading($("save-actions"), async () => {
      let saved;
      if (replacing) {
        saved = await Global.fetchJSON(`/api/people/${replacing.id}`, { method: "PATCH", headers: JSON_HEADERS, body: JSON.stringify({ resume_yaml: yamlText, name: lastPreview.name || replacing.name }) });
      } else {
        const body = { name: ($("save-name") && $("save-name").value.trim()) || lastPreview.name || "My resume", resume_yaml: yamlText };
        if (me.is_admin) {
          body.slug = $("save-slug").value.trim();
          body.published = false; // never public by accident; publishing is a deliberate step on the People page
        }
        saved = await Global.fetchJSON("/api/people", { method: "POST", headers: JSON_HEADERS, body: JSON.stringify(body) });
      }
      myPeople = await Global.fetchJSON("/api/people");
      existing = me.is_admin ? existing : saved;
      target = myPeople.find((p) => p.id === saved.id) || null; // the next update merges into what was just saved
      await loadTarget();
      renderMode();
      await clearDraft(); // it is a resume now, no longer a draft
      $("draft-note").classList.add("hidden");
      $("save-status").replaceChildren(document.createTextNode("Saved. "), el("a", { href: "/", text: "Open my resume" }));
      Global.showMessage("Saved.", "success");
    });
  } catch (err) {
    reportError(err, $("save-status"));
  } finally {
    $("save-btn").disabled = !lastPreview.ok;
  }
}

// --- start -----------------------------------------------------------------------------------------------------------------------

async function start() {
  try {
    me = await Global.fetchJSON("/api/me");
    myPeople = await Global.fetchJSON("/api/people");
    existing = me.is_admin ? null : myPeople[0] || null;
    target = myPeople.length === 1 || !me.is_admin ? myPeople[0] || null : null; // an update by default when there is a resume to update
  } catch (err) {
    Global.showMessage(err.message, "error");
  }
  dropzone($("drop-linkedin"), { accept: ".zip,.json", prompt: "Drop LinkedIn's ZIP here", multiple: false });
  dropzone($("drop-resumes"), { accept: ".pdf,.docx,.txt,.md", prompt: "Drop your old resumes here", multiple: true });
  renderMode();
  $("add-engagement").addEventListener("click", () => openEngagementForm(null));
  $("import-engagements").addEventListener("click", openImport);
  $("remove-all-engagements").addEventListener("click", removeAllEngagements);
  $("notes").addEventListener("input", () => {
    saveNotesSoon();
    updateDraftButton();
  });
  $("draft-btn").addEventListener("click", draft);
  $("save-btn").addEventListener("click", save);
  $("discard-btn").addEventListener("click", discardDraft);
  $("review-btn").addEventListener("click", reviewWithAI);
  $("keep-files").addEventListener("change", changeKeep);
  $("draft-yaml").addEventListener("input", () => {
    clearTimeout(previewTimer);
    previewTimer = setTimeout(refreshPreview, 400);
    $("save-status").textContent = "";
    saveDraftSoon();
  });
  Global.iconifyButtons && Global.iconifyButtons(document.body);
  await Promise.all([loadKey(), loadNotes(), loadTarget(), loadSources()]);
  await loadEngagements();
  if (!(await resumePendingDraft())) await restoreDraft();
}
start();
