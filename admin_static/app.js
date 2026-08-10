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
