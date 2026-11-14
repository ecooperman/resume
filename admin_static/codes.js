// The Access codes page: each code as an accordion card, plus the form that makes one.
// Needs Global (shared-assets).

const SITE_URL = "https://resume.evancooperman.com";
const JSON_HEADERS = { "Content-Type": "application/json" };

function fmtDate(iso) {
  if (!iso) return null;
  return new Date(iso.endsWith("Z") ? iso : iso + "Z").toLocaleString();
}

function codeCard(c) {
  // No ?person=<slug> needed - the code is tied to a specific person and app/main.py looks that
  // up first, so it is authoritative on its own whoever is currently the default.
  const url = `${SITE_URL}/?code=${c.code}`;
  const card = Global.el("div", { class: `item-card code-${c.status}`, "data-id": c.id });

  const summary = Global.el("button", { type: "button", class: "item-summary", "aria-expanded": "false" });
  summary.appendChild(Global.el("span", { class: "item-summary-title", text: c.label || "(no label)" }));
  if (c.person_name) summary.appendChild(Global.el("span", { class: "item-badge item-badge-muted", text: c.person_name }));
  summary.appendChild(Global.el("span", { class: `item-badge badge-status badge-${c.status}`, text: c.status }));
  summary.appendChild(Global.el("span", { class: "item-badge item-badge-muted", text: `${c.use_count} ${c.use_count === 1 ? "use" : "uses"}` }));
  summary.appendChild(Global.el("span", { class: "item-chevron", "aria-hidden": "true", text: "▸" }));

  const details = Global.el("div", { class: "item-details hidden" });
  Global.wireAccordionToggle(card, summary, details);

  const readonly = (label, value) =>
    Global.el("div", { class: "field" }, [Global.el("label", { text: label }), Global.el("div", { class: "readonly-value", text: value })]);
  const fields = Global.el("div", { class: "item-fields" }, [
    readonly("Person", c.person_name || "-"),
    readonly("Last used", fmtDate(c.last_used_at) || "never"),
    readonly("Expires", fmtDate(c.expires_at) || "never"),
    readonly("Created", fmtDate(c.created_at) || "-"),
  ]);
  const link = Global.el("div", { class: "field" }, [Global.el("label", { text: "Link" }), Global.el("div", { class: "readonly-value code-link", text: url })]);

  const actions = Global.el("div", { class: "item-actions" });
  if (c.status !== "revoked") {
    actions.appendChild(
      Global.el("button", {
        type: "button",
        class: "danger-btn",
        "data-btn-icon": "lock",
        text: "Revoke",
        onclick: async () => {
          if (!confirm(`Revoke the code for "${c.label || "(no label)"}"? This can't be undone.`)) return;
          try {
            await Global.fetchJSON(`/api/codes/${c.id}/revoke`, { method: "POST" });
            Global.showMessage("Revoked.", "success");
            loadCodes();
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
      class: "secondary-btn",
      text: "Copy link",
      onclick: async () => {
        try {
          await navigator.clipboard.writeText(url);
          Global.showMessage("Link copied.", "success");
        } catch (err) {
          Global.showMessage("Your browser won't let this page copy - select the link above instead.", "error");
        }
      },
    })
  );

  details.appendChild(Global.el("div", { class: "item-details-inner" }, [fields, link, actions]));
  card.append(summary, details);
  return card;
}

async function loadCodes() {
  const list = document.getElementById("codes-list");
  const codes = await Global.withLoading(list, Global.fetchJSON("/api/codes"));
  list.replaceChildren(...codes.map(codeCard));
  document.getElementById("codes-count").textContent = codes.length ? String(codes.length) : "";
  document.getElementById("codes-empty").hidden = codes.length !== 0;
}

async function loadPeopleForSelect() {
  const people = await Global.fetchJSON("/api/people");
  const select = document.getElementById("code-person");
  select.replaceChildren(
    ...people.map((p) => Global.el("option", { value: String(p.id), text: p.is_default ? `${p.name} (default)` : p.name }))
  );
  const preferred = people.find((p) => p.is_default) || people[0];
  if (preferred) select.value = String(preferred.id);
}

function initCreateForm() {
  const form = document.getElementById("create-form");
  const showBtn = document.getElementById("show-create-code");
  function close() {
    form.reset();
    form.classList.add("hidden");
    showBtn.classList.remove("hidden");
    loadPeopleForSelect().catch(() => {});
  }
  showBtn.addEventListener("click", () => {
    form.classList.remove("hidden");
    showBtn.classList.add("hidden");
    document.getElementById("label").focus();
  });
  document.getElementById("cancel-create-code").addEventListener("click", close);

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const label = document.getElementById("label").value.trim() || null;
    const daysRaw = document.getElementById("days").value.trim();
    const personValue = document.getElementById("code-person").value;
    try {
      const c = await Global.fetchJSON("/api/codes", {
        method: "POST",
        headers: JSON_HEADERS,
        body: JSON.stringify({
          label,
          days: daysRaw ? parseInt(daysRaw, 10) : null,
          person_id: personValue ? parseInt(personValue, 10) : null,
        }),
      });
      close();
      Global.showMessage(`Created code for "${c.label || "(no label)"}" (${c.person_name || "no person"}).`, "success");
      loadCodes();
    } catch (err) {
      Global.showMessage(err.message, "error");
    }
  });
}

initCreateForm();
loadPeopleForSelect().catch((err) => Global.showMessage(err.message, "error"));
loadCodes().catch((err) => Global.showMessage(err.message, "error"));
