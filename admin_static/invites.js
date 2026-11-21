// Invites (invites.html): anyone with an account can ask for a new person to be let in (the shared AppInvites panel); an administrator
// also sees everyone's waiting requests and decides on them. The server refuses the queue to everyone else (403), so it is only asked
// for when the signed-in person is an administrator.

const requestsEl = document.getElementById("invite-requests");
const adminSection = document.getElementById("admin-section");
const declinedSection = document.getElementById("declined-section");
const declinedEl = document.getElementById("declined-requests");

function updateNavCount(count) {
  const badge = document.getElementById("nav-invite-count");
  if (!badge) return;
  badge.textContent = String(count);
  badge.classList.toggle("hidden", count === 0);
}

async function decide(invite, action, buttons) {
  buttons.forEach((b) => (b.disabled = true));
  try {
    await Global.fetchJSON(`/api/admin/invites/${invite.id}/${action}`, { method: "POST" });
    Global.showMessage(action === "approve" ? `${invite.email} can now sign in to Jobs.` : `Declined ${invite.email}.`, "success");
  } catch (err) {
    Global.showMessage(err.message, "error");
  }
  loadQueue();
}

function requestCard(invite) {
  const approve = Global.el("button", { type: "button", class: "save-btn", text: "Approve" });
  const decline = Global.el("button", { type: "button", class: "cancel-btn", text: "Decline" });
  const buttons = [approve, decline];
  approve.addEventListener("click", () => decide(invite, "approve", buttons));
  decline.addEventListener("click", () => decide(invite, "decline", buttons));

  const asked = new Date(`${invite.created_at}Z`).toLocaleString();
  return Global.el("div", { class: "request-card" }, [
    Global.el("div", { class: "request-info" }, [
      Global.el("strong", { text: invite.email }),
      Global.el("div", { class: "field-hint", text: `Asked for by ${invite.invited_by_email} - ${asked}` }),
    ]),
    Global.el("div", { class: "request-actions" }, buttons),
  ]);
}

async function loadQueue() {
  let invites;
  try {
    invites = await Global.withLoading(requestsEl, Global.fetchJSON("/api/admin/invites"));
  } catch (err) {
    requestsEl.replaceChildren(Global.el("p", { class: "field-hint", text: err.message }));
    return;
  }
  updateNavCount(invites.length);
  requestsEl.replaceChildren(
    ...(invites.length ? invites.map(requestCard) : [Global.el("p", { class: "field-hint", text: "Nothing is waiting." })])
  );
  loadDeclined();
}

async function allowAgain(invite, button) {
  if (!confirm(`Allow ${invite.email} to be asked for again?`)) return;
  button.disabled = true;
  try {
    await Global.fetchJSON(`/api/admin/invites/${invite.id}/reinstate`, { method: "POST" });
    Global.showMessage(`${invite.email} can be invited again.`, "success");
  } catch (err) {
    Global.showMessage(err.message, "error");
  }
  loadDeclined();
}

function declinedCard(invite) {
  const button = Global.el("button", { type: "button", class: "secondary-btn", text: "Allow again" });
  button.addEventListener("click", () => allowAgain(invite, button));
  const decided = invite.decided_at ? new Date(`${invite.decided_at}Z`).toLocaleDateString() : "";
  return Global.el("div", { class: "request-card" }, [
    Global.el("div", { class: "request-info" }, [
      Global.el("strong", { text: invite.email }),
      Global.el("div", { class: "field-hint", text: `Declined ${decided} - asked for by ${invite.invited_by_email}` }),
    ]),
    Global.el("div", { class: "request-actions" }, [button]),
  ]);
}

async function loadDeclined() {
  let declined;
  try {
    declined = await Global.fetchJSON("/api/admin/invites/declined");
  } catch (err) {
    return; // the waiting queue already reports a failure; this list is a convenience
  }
  declinedSection.classList.toggle("hidden", declined.length === 0);
  declinedEl.replaceChildren(...declined.map(declinedCard));
}

AppInvites.mount(document.getElementById("app-invites"), {
  appName: "Resume",
  afterApproval: "Everyone's resume stays completely private to them.",
  // An administrator's own request is approved straight away; either way the queue may have changed.
  onChange: () => adminSection.classList.contains("hidden") || loadQueue(),
});

Global.fetchJSON("/api/me")
  .then((me) => {
    if (!me || !me.is_admin) return;
    adminSection.classList.remove("hidden");
    loadQueue();
  })
  .catch(() => {});
