// Send feedback (contact.html): anyone sends the administrator a message; an administrator also reads everyone's here and ticks them off.
// The server keeps each person to their own messages and refuses the inbox to everyone else (403), so the inbox is only asked for when the
// signed-in person is an administrator. Needs Global (shared-assets).

const JSON_HEADERS = { "Content-Type": "application/json" };
const KINDS = { problem: "Problem", idea: "Idea", question: "Question" };
const form = document.getElementById("contact-form");
const bodyEl = document.getElementById("contact-body");
const kindEl = document.getElementById("contact-kind");
const mineSection = document.getElementById("contact-mine");
const mineEl = document.getElementById("contact-mine-list");
const inboxSection = document.getElementById("inbox-section");
const inboxEl = document.getElementById("inbox");

// The page the person came from (the menu links here), so a report of "it did not work" says where. Only a path on this app is sent.
function cameFrom() {
  try {
    const ref = new URL(document.referrer);
    if (ref.origin === location.origin && ref.pathname !== location.pathname) return ref.pathname;
  } catch (err) {
    /* no referrer */
  }
  return "";
}

const when = (iso) => new Date(`${iso}Z`).toLocaleString();

function mineCard(message) {
  const status = message.done_at ? "Dealt with" : "Waiting";
  return Global.el("div", { class: "request-card" }, [
    Global.el("div", { class: "request-info" }, [
      Global.el("strong", { text: `${KINDS[message.kind] || message.kind} - ${status}` }),
      Global.el("div", { class: "field-hint", text: when(message.created_at) }),
      Global.el("div", { text: message.body, style: "white-space: pre-wrap" }),
    ]),
  ]);
}

async function loadMine() {
  let messages = [];
  try {
    messages = await Global.fetchJSON("/api/contact");
  } catch (err) {
    return; // a convenience: sending still works
  }
  mineSection.classList.toggle("hidden", messages.length === 0);
  mineEl.replaceChildren(...messages.map(mineCard));
}

function updateNavCount(count) {
  const badge = document.getElementById("nav-message-count");
  if (!badge) return;
  badge.textContent = String(count);
  badge.classList.toggle("hidden", count === 0);
}

async function act(message, action, buttons) {
  buttons.forEach((b) => (b.disabled = true));
  try {
    if (action === "delete") await Global.fetchJSON(`/api/admin/messages/${message.id}`, { method: "DELETE" });
    else await Global.fetchJSON(`/api/admin/messages/${message.id}/${action}`, { method: "POST" });
  } catch (err) {
    Global.showMessage(err.message, "error");
  }
  loadInbox();
}

function inboxCard(message) {
  const toggle = Global.el("button", { type: "button", class: "secondary-btn", text: message.done_at ? "Reopen" : "Mark done" });
  const remove = Global.el("button", { type: "button", class: "danger-btn", text: "Delete" });
  const buttons = [toggle, remove];
  toggle.addEventListener("click", () => act(message, message.done_at ? "reopen" : "done", buttons));
  remove.addEventListener("click", () => confirm("Delete this message for good?") && act(message, "delete", buttons));
  const subject = encodeURIComponent(`Re: your ${(KINDS[message.kind] || "message").toLowerCase()} about Jobs`);
  const where = message.page ? ` - from ${message.page}` : "";
  return Global.el("div", { class: "request-card" }, [
    Global.el("div", { class: "request-info" }, [
      Global.el("strong", { text: `${KINDS[message.kind] || message.kind}${message.done_at ? " - done" : ""}` }),
      Global.el("div", { class: "field-hint" }, [
        Global.el("a", { href: `mailto:${message.from_email}?subject=${subject}`, text: message.from_email }),
        document.createTextNode(` - ${when(message.created_at)}${where}`),
      ]),
      Global.el("div", { text: message.body, style: "white-space: pre-wrap" }),
    ]),
    Global.el("div", { class: "request-actions" }, buttons),
  ]);
}

async function loadInbox() {
  let messages;
  try {
    messages = await Global.withLoading(inboxEl, Global.fetchJSON("/api/admin/messages"));
  } catch (err) {
    inboxEl.replaceChildren(Global.el("p", { class: "field-hint", text: err.message }));
    return;
  }
  updateNavCount(messages.filter((m) => !m.done_at).length);
  inboxEl.replaceChildren(...(messages.length ? messages.map(inboxCard) : [Global.el("p", { class: "field-hint", text: "No messages yet." })]));
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = form.querySelector("button[type=submit]");
  button.disabled = true;
  try {
    await Global.fetchJSON("/api/contact", {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify({ kind: kindEl.value, body: bodyEl.value, page: cameFrom() }),
    });
    bodyEl.value = "";
    Global.showMessage("Sent. Thank you - if it needs an answer, you will hear back by email.", "success");
    loadMine();
    if (!inboxSection.classList.contains("hidden")) loadInbox();
  } catch (err) {
    Global.showMessage(err.message, "error");
  } finally {
    button.disabled = false;
  }
});

document.addEventListener("resume-me-ready", () => {
  const me = window.RESUME_ME || {};
  document.getElementById("contact-from").textContent = me.email ? `Sent as ${me.email}; any reply comes to that address by email.` : "";
  if (me.is_admin) {
    inboxSection.classList.remove("hidden");
    loadInbox();
  }
});
loadMine();
Global.iconifyButtons && Global.iconifyButtons(document.body);
