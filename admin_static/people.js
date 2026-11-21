// The Resumes page (My resume for everyone but an administrator; the file is still people.js): each resume as an accordion card (their resume YAML inside), plus the forms
// for adding someone (drafting one from documents is the Build my resume page, build.js). Needs Global (shared-assets).

const PEOPLE_API = "/api/people";
const JSON_HEADERS = { "Content-Type": "application/json" };

// Administrators manage the public site (slugs, the default, publishing) and may use the server's AI key; everyone else has one
// private resume. The server enforces all of it; this only decides what to show.
let me = { is_admin: false };

function personCard(p) {
  const card = Global.el("div", { class: "item-card", "data-id": p.id });
  const summary = Global.el("button", { type: "button", class: "item-summary", "aria-expanded": "false" });
  summary.appendChild(Global.el("span", { class: "item-summary-title", text: p.name }));
  if (me.is_admin) summary.appendChild(Global.el("span", { class: "item-badge item-badge-muted", text: p.slug }));
  summary.appendChild(Global.el("span", { class: p.published ? "item-badge item-badge-accent" : "item-badge item-badge-muted", text: p.published ? "public" : "private" }));
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
  const patch = async (body, done) => {
    try {
      await Global.fetchJSON(`${PEOPLE_API}/${p.id}`, { method: "PATCH", headers: JSON_HEADERS, body: JSON.stringify(body) });
      Global.showMessage(done, "success");
      loadPeople();
    } catch (err) {
      Global.showMessage(err.message, "error");
    }
  };
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
  }
  if (me.is_admin && !p.is_default) {
    actions.appendChild(
      Global.el("button", {
        type: "button",
        class: "secondary-btn",
        "data-btn-icon": "check",
        text: "Make default",
        onclick: () => patch({ is_default: true }, `"${p.name}" is now the default.`),
      })
    );
  }
  if (me.is_admin && !p.is_default) {
    actions.appendChild(
      Global.el("button", {
        type: "button",
        class: "secondary-btn",
        "data-btn-icon": p.published ? "eye-off" : "eye",
        text: p.published ? "Make private" : "Publish",
        onclick: () => patch({ published: !p.published }, p.published ? `"${p.name}" is private again.` : `"${p.name}" is now on the public site.`),
      })
    );
  }
  for (const [format, label] of [["pdf", "PDF"], ["docx", "Word"]]) {
    actions.appendChild(
      Global.el("a", { class: "secondary-btn", "data-btn-icon": "download", href: `${PEOPLE_API}/${p.id}/download?format=${format}`, text: `Download ${label}` })
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
  // Everyone but an administrator has exactly one resume.
  const full = !me.is_admin && people.length >= 1;
  document.getElementById("show-add-person").classList.toggle("hidden", full);
  if (full) document.getElementById("add-person-form").classList.add("hidden");
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
      const body = { name: document.getElementById("person-name").value, resume_yaml: document.getElementById("person-yaml").value };
      if (me.is_admin) {
        body.slug = document.getElementById("person-slug").value;
        body.is_default = document.getElementById("person-default").checked;
        body.published = document.getElementById("person-published").checked;
      }
      const p = await Global.fetchJSON(PEOPLE_API, { method: "POST", headers: JSON_HEADERS, body: JSON.stringify(body) });
      close();
      Global.showMessage(`Added "${p.name}".`, "success");
      loadPeople();
    } catch (err) {
      Global.showMessage(err.message, "error");
    }
  });
  return open;
}

async function start() {
  try {
    me = await Global.fetchJSON("/api/me");
  } catch (err) {
    Global.showMessage(err.message, "error");
  }
  // What only an administrator may use stays out of everyone else's page.
  for (const id of ["slug-field", "default-field", "published-field"]) {
    document.getElementById(id).classList.toggle("admin-only-hidden", !me.is_admin);
  }
  if (!me.is_admin) {
    // Everyone else has just their own, private resume: say so in their words.
    document.querySelector(".app-header h1").textContent = "My resume";
    document.querySelector(".app-header .subtitle").textContent = "Your resume lives here, private to you. Nobody else can see it, including the administrators.";
    document.getElementById("people-title").textContent = "Your resume";
    document.getElementById("show-add-person").textContent = "+ Add my resume";
    document.getElementById("people-empty").replaceChildren(
      document.createTextNode("You haven't added your resume yet. The easiest way to start is "),
      Global.el("a", { href: "/build.html", text: "Build my resume" }),
      document.createTextNode(" (in the menu on the left): it reads your LinkedIn data and any old resumes and writes a first draft for you to check. If you already have a resume.yaml, you can instead paste it with the button above.")
    );
    document.getElementById("person-slug").required = false;
  }
  initAddPerson();
  loadPeople().catch((err) => Global.showMessage(err.message, "error"));
}
start();
