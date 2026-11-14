// The People page: each person as an accordion card (their resume YAML inside), plus the forms
// for adding someone and for drafting a resume.yaml from documents. Needs Global (shared-assets).

const PEOPLE_API = "/api/people";
const JSON_HEADERS = { "Content-Type": "application/json" };

function personCard(p) {
  const card = Global.el("div", { class: "item-card", "data-id": p.id });
  const summary = Global.el("button", { type: "button", class: "item-summary", "aria-expanded": "false" });
  summary.appendChild(Global.el("span", { class: "item-summary-title", text: p.name }));
  summary.appendChild(Global.el("span", { class: "item-badge item-badge-muted", text: p.slug }));
  if (p.is_default) summary.appendChild(Global.el("span", { class: "item-badge item-badge-accent", text: "default" }));
  summary.appendChild(Global.el("span", { class: "item-chevron", "aria-hidden": "true", text: "▸" }));

  const details = Global.el("div", { class: "item-details hidden" });
  Global.wireAccordionToggle(card, summary, details);
  card.append(summary, details);

  // The YAML can be a few KB, so it is fetched the first time the card opens, not for every
  // person on every page load.
  let loaded = false;
  summary.addEventListener("click", async () => {
    if (loaded || !card.classList.contains("expanded")) return;
    loaded = true;
    try {
      const full = await Global.withLoading(details, Global.fetchJSON(`${PEOPLE_API}/${p.id}`));
      details.appendChild(personEditor(p, full));
    } catch (err) {
      loaded = false;
      Global.showMessage(err.message, "error");
    }
  });
  return card;
}

function personEditor(p, full) {
  const wrap = Global.el("div", { class: "item-details-inner" });
  const yaml = Global.el("textarea", { class: "yaml", rows: "16", "aria-label": `Resume YAML for ${p.name}` });
  yaml.value = full.resume_yaml;
  wrap.appendChild(Global.el("div", { class: "field" }, [Global.el("label", { text: "Resume YAML" }), yaml]));

  const actions = Global.el("div", { class: "item-actions" });
  if (!p.is_default) {
    actions.appendChild(
      Global.el("button", {
        type: "button",
        class: "danger-btn",
        text: "Delete",
        onclick: async () => {
          if (!confirm(`Delete "${p.name}"? This can't be undone.`)) return;
          try {
            await Global.fetchJSON(`${PEOPLE_API}/${p.id}`, { method: "DELETE" });
            Global.showMessage(`Deleted "${p.name}".`, "success");
            loadPeople();
          } catch (err) {
            Global.showMessage(err.message, "error");
          }
        },
      })
    );
    actions.appendChild(
      Global.el("button", {
        type: "button",
        class: "secondary-btn",
        "data-btn-icon": "check",
        text: "Make default",
        onclick: async () => {
          try {
            await Global.fetchJSON(`${PEOPLE_API}/${p.id}`, { method: "PATCH", headers: JSON_HEADERS, body: JSON.stringify({ is_default: true }) });
            Global.showMessage(`"${p.name}" is now the default.`, "success");
            loadPeople();
          } catch (err) {
            Global.showMessage(err.message, "error");
          }
        },
      })
    );
  }
  actions.appendChild(
    Global.el("button", {
      type: "button",
      class: "save-btn",
      text: "Save",
      onclick: async () => {
        try {
          await Global.fetchJSON(`${PEOPLE_API}/${p.id}`, { method: "PATCH", headers: JSON_HEADERS, body: JSON.stringify({ resume_yaml: yaml.value }) });
          Global.showMessage(`Saved "${p.name}".`, "success");
        } catch (err) {
          Global.showMessage(err.message, "error");
        }
      },
    })
  );
  wrap.appendChild(actions);
  return wrap;
}

async function loadPeople() {
  const list = document.getElementById("people-list");
  const people = await Global.withLoading(list, Global.fetchJSON(PEOPLE_API));
  list.replaceChildren(...people.map(personCard));
  document.getElementById("people-count").textContent = people.length ? String(people.length) : "";
  document.getElementById("people-empty").hidden = people.length !== 0;
}

// --- add a person ---

function initAddPerson() {
  const form = document.getElementById("add-person-form");
  const showBtn = document.getElementById("show-add-person");

  function open() {
    form.classList.remove("hidden");
    showBtn.classList.add("hidden");
  }
  function close() {
    form.reset();
    form.classList.add("hidden");
    showBtn.classList.remove("hidden");
  }
  showBtn.addEventListener("click", () => {
    open();
    document.getElementById("person-name").focus();
  });
  document.getElementById("cancel-add-person").addEventListener("click", close);

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      const p = await Global.fetchJSON(PEOPLE_API, {
        method: "POST",
        headers: JSON_HEADERS,
        body: JSON.stringify({
          name: document.getElementById("person-name").value,
          slug: document.getElementById("person-slug").value,
          resume_yaml: document.getElementById("person-yaml").value,
          is_default: document.getElementById("person-default").checked,
        }),
      });
      close();
      Global.showMessage(`Added "${p.name}".`, "success");
      loadPeople();
    } catch (err) {
      Global.showMessage(err.message, "error");
    }
  });
  return open;
}

// --- draft a resume.yaml from documents ---

function initGenerate(openAddPerson) {
  const form = document.getElementById("generate-form");
  const showBtn = document.getElementById("show-generate");
  const status = document.getElementById("gen-status");
  const genBtn = document.getElementById("gen-btn");

  function close() {
    form.reset();
    form.classList.add("hidden");
    showBtn.classList.remove("hidden");
  }
  showBtn.addEventListener("click", () => {
    form.classList.remove("hidden");
    showBtn.classList.add("hidden");
  });
  document.getElementById("cancel-generate").addEventListener("click", close);

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const linkedin = document.getElementById("gen-linkedin-file").files[0];
    const docs = document.getElementById("gen-docs-files").files;
    if (!linkedin && docs.length === 0) {
      Global.showMessage("Add a LinkedIn export and/or at least one document first.", "error");
      return;
    }
    const data = new FormData();
    if (linkedin) data.append("linkedin_profile", linkedin);
    for (const f of docs) data.append("documents", f);
    data.append("notes", document.getElementById("gen-notes").value);

    genBtn.disabled = true;
    status.textContent = "Generating - this can take 20-60 seconds…";
    try {
      // No Content-Type header on purpose: the browser sets the multipart boundary itself.
      const result = await Global.fetchJSON("/api/generate-resume", { method: "POST", body: data });
      close();
      openAddPerson();
      const yaml = document.getElementById("person-yaml");
      yaml.value = result.resume_yaml;
      yaml.scrollIntoView({ behavior: "smooth", block: "center" });
      Global.showMessage("Generated - review the YAML below, fill in a name and slug, then Add person.", "success");
    } catch (err) {
      Global.showMessage(err.message, "error");
    } finally {
      genBtn.disabled = false;
      status.textContent = "";
    }
  });
}

initGenerate(initAddPerson());
loadPeople().catch((err) => Global.showMessage(err.message, "error"));
