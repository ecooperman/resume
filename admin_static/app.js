const SITE_URL = "https://resume.evancooperman.com";

async function fetchJSON(url, options) {
  const res = await fetch(url, options);
  if (!res.ok) {
    let detail = `${url} -> ${res.status}`;
    try {
      const body = await res.json();
      if (body.detail) detail = body.detail;
    } catch (e) {
      // ignore, use default detail
    }
    throw new Error(detail);
  }
  if (res.status === 204) return null;
  return res.json();
}

function showMessage(text, kind) {
  const el = document.getElementById("message");
  el.textContent = text;
  el.className = "message " + kind;
  el.classList.remove("hidden");
  clearTimeout(showMessage._t);
  showMessage._t = setTimeout(() => el.classList.add("hidden"), 4000);
}

function fmtDate(iso) {
  if (!iso) return null;
  const d = new Date(iso.endsWith("Z") ? iso : iso + "Z");
  return d.toLocaleString();
}

function escapeHtml(s) {
  const div = document.createElement("div");
  div.textContent = s;
  return div.innerHTML;
}

function codeRow(c) {
  const tr = document.createElement("tr");
  tr.className = "status-" + c.status;

  const url = `${SITE_URL}/?code=${c.code}`;
  const lastUsed = fmtDate(c.last_used_at) || "—";
  const expires = fmtDate(c.expires_at) || "never";

  tr.innerHTML = `
    <td>${c.label ? escapeHtml(c.label) : "(no label)"}</td>
    <td><span class="badge badge-${c.status}">${c.status}</span></td>
    <td>${c.use_count}</td>
    <td>${lastUsed}</td>
    <td>${expires}</td>
    <td><button type="button" class="copy-btn">Copy link</button></td>
    <td class="revoke-cell"></td>
  `;

  tr.querySelector(".copy-btn").addEventListener("click", async () => {
    await navigator.clipboard.writeText(url);
    showMessage("Link copied.", "success");
  });

  const revokeCell = tr.querySelector(".revoke-cell");
  if (c.status !== "revoked") {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.textContent = "Revoke";
    btn.className = "revoke-btn";
    btn.addEventListener("click", async () => {
      if (!confirm(`Revoke the code for "${c.label || "(no label)"}"? This can't be undone.`)) return;
      try {
        await fetchJSON(`/api/codes/${c.id}/revoke`, { method: "POST" });
        showMessage("Revoked.", "success");
        loadCodes();
      } catch (e) {
        showMessage(e.message, "error");
      }
    });
    revokeCell.appendChild(btn);
  }

  return tr;
}

function personRow(p) {
  const div = document.createElement("div");
  div.className = "person-row" + (p.is_default ? " is-default" : "");

  const summary = document.createElement("div");
  summary.className = "person-summary";
  summary.innerHTML = `
    <span class="person-name">${escapeHtml(p.name)}</span>
    <span class="person-slug">(${escapeHtml(p.slug)})</span>
    ${p.is_default ? '<span class="badge badge-active">default</span>' : ""}
  `;

  const actions = document.createElement("div");
  actions.className = "person-actions";

  const editBtn = document.createElement("button");
  editBtn.type = "button";
  editBtn.textContent = "Edit";
  actions.appendChild(editBtn);

  if (!p.is_default) {
    const defaultBtn = document.createElement("button");
    defaultBtn.type = "button";
    defaultBtn.textContent = "Make default";
    defaultBtn.addEventListener("click", async () => {
      try {
        await fetchJSON(`/api/people/${p.id}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ is_default: true }),
        });
        showMessage(`"${p.name}" is now the default.`, "success");
        loadPeople();
      } catch (e) {
        showMessage(e.message, "error");
      }
    });
    actions.appendChild(defaultBtn);

    const deleteBtn = document.createElement("button");
    deleteBtn.type = "button";
    deleteBtn.className = "revoke-btn";
    deleteBtn.textContent = "Delete";
    deleteBtn.addEventListener("click", async () => {
      if (!confirm(`Delete "${p.name}"? This can't be undone.`)) return;
      try {
        await fetchJSON(`/api/people/${p.id}`, { method: "DELETE" });
        showMessage(`Deleted "${p.name}".`, "success");
        loadPeople();
      } catch (e) {
        showMessage(e.message, "error");
      }
    });
    actions.appendChild(deleteBtn);
  }

  summary.appendChild(actions);
  div.appendChild(summary);

  // Edit form starts hidden - fetching full content (which can be a few KB)
  // only happens the first time Edit is actually clicked, not on every
  // page load for every person.
  const editForm = document.createElement("div");
  editForm.className = "person-edit hidden";
  div.appendChild(editForm);

  let loaded = false;
  editBtn.addEventListener("click", async () => {
    const isHidden = editForm.classList.contains("hidden");
    if (!isHidden) {
      editForm.classList.add("hidden");
      editBtn.textContent = "Edit";
      return;
    }
    if (!loaded) {
      try {
        const full = await fetchJSON(`/api/people/${p.id}`);
        editForm.innerHTML = `
          <textarea class="person-yaml-edit" rows="14">${escapeHtml(full.resume_yaml)}</textarea>
          <button type="button" class="save-btn">Save</button>
        `;
        editForm.querySelector(".save-btn").addEventListener("click", async () => {
          try {
            await fetchJSON(`/api/people/${p.id}`, {
              method: "PATCH",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ resume_yaml: editForm.querySelector(".person-yaml-edit").value }),
            });
            showMessage(`Saved "${p.name}".`, "success");
          } catch (e) {
            showMessage(e.message, "error");
          }
        });
        loaded = true;
      } catch (e) {
        showMessage(e.message, "error");
        return;
      }
    }
    editForm.classList.remove("hidden");
    editBtn.textContent = "Close";
  });

  return div;
}

async function loadPeople() {
  const people = await fetchJSON("/api/people");
  const container = document.getElementById("people-list");
  container.innerHTML = "";
  if (people.length === 0) {
    container.innerHTML = `<p class="empty">No one set up yet - add someone below.</p>`;
    return;
  }
  for (const p of people) {
    container.appendChild(personRow(p));
  }
}

document.getElementById("add-person-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const nameInput = document.getElementById("person-name");
  const slugInput = document.getElementById("person-slug");
  const yamlInput = document.getElementById("person-yaml");
  const defaultInput = document.getElementById("person-default");
  try {
    const p = await fetchJSON("/api/people", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: nameInput.value,
        slug: slugInput.value,
        resume_yaml: yamlInput.value,
        is_default: defaultInput.checked,
      }),
    });
    nameInput.value = "";
    slugInput.value = "";
    yamlInput.value = "";
    defaultInput.checked = false;
    showMessage(`Added "${p.name}".`, "success");
    loadPeople();
  } catch (err) {
    showMessage(err.message, "error");
  }
});

loadPeople();

async function loadCodes() {
  const codes = await fetchJSON("/api/codes");
  const tbody = document.getElementById("codes-body");
  tbody.innerHTML = "";
  if (codes.length === 0) {
    tbody.innerHTML = `<tr><td colspan="7" class="empty">No codes yet.</td></tr>`;
    return;
  }
  for (const c of codes) {
    tbody.appendChild(codeRow(c));
  }
}

document.getElementById("create-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const labelInput = document.getElementById("label");
  const daysInput = document.getElementById("days");
  const label = labelInput.value.trim() || null;
  const daysRaw = daysInput.value.trim();
  const days = daysRaw ? parseInt(daysRaw, 10) : null;
  try {
    const c = await fetchJSON("/api/codes", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ label, days }),
    });
    labelInput.value = "";
    daysInput.value = "";
    showMessage(`Created code for "${c.label || "(no label)"}".`, "success");
    loadCodes();
  } catch (err) {
    showMessage(err.message, "error");
  }
});

loadCodes();
