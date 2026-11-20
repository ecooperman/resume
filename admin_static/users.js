// Users (users.html, administrator only): everyone with an account in Resume; add someone, remove (and restore) their
// access, or delete a removed person's data. The server refuses all of it to anyone else (403). An administrator
// manages WHO has an account here and never sees what anyone owns: everyone's data is private to them.

const activeEl = document.getElementById("active-users");
const removedEl = document.getElementById("removed-section");
const removedList = document.getElementById("removed-users");
const addEl = document.getElementById("add-user");

const JSON_HEADERS = { "Content-Type": "application/json" };
const send = (path, method, body) =>
  Global.fetchJSON(`/api/users${path}`, { method, headers: JSON_HEADERS, body: body ? JSON.stringify(body) : undefined });

async function act(button, request, success) {
  button.disabled = true;
  try {
    await request();
    Global.showMessage(success, "success");
  } catch (err) {
    Global.showMessage(err.message, "error");
  }
  load();
}

function card(u) {
  const badges = [];
  if (u.is_admin) badges.push(Global.el("span", { class: "item-badge item-badge-accent", text: "Administrator" }));
  if (u.status === "removed") badges.push(Global.el("span", { class: "item-badge item-badge-muted", text: "Removed" }));
  const title = Global.el("div", {}, [Global.el("strong", { text: u.email + " " }), ...badges]);

  // Fall back to the system (the server's) Anthropic key, for someone who has no key of their own; usage so you can see what it costs.
  const info = [title];
  if (u.status === "active" && !u.is_admin) {
    const box = Global.el("input", { type: "checkbox", id: `system-key-${u.id}` });
    box.checked = u.system_key;
    box.addEventListener("change", () => {
      box.disabled = true;
      act(box, () => send(`/${u.id}/system-key`, "PUT", { allowed: box.checked }), box.checked ? `${u.email} can now fall back to the system key.` : `${u.email} can no longer use the system key.`);
    });
    info.push(Global.el("label", { class: "field-hint", for: `system-key-${u.id}` }, [box, " Can fall back to the system key"]));
  }
  if (u.system_key || u.runs_this_month) {
    info.push(Global.el("div", { class: "field-hint", text: `${u.runs_this_month} AI run${u.runs_this_month === 1 ? "" : "s"} on the system key this month, about $${u.cost_this_month.toFixed(2)}` }));
  }

  const actions = [];
  if (u.status === "active" && !u.is_admin) {
    const b = Global.el("button", { type: "button", class: "cancel-btn", text: "Remove access" });
    b.addEventListener("click", () => {
      if (!confirm(`Remove ${u.email}'s access to Resume?\n\nThey won't be able to use it, but nothing of theirs is deleted and you can restore them any time.`)) return;
      act(b, () => send(`/${u.id}`, "PATCH", { status: "removed" }), `${u.email} can no longer use Resume.`);
    });
    actions.push(b);
  }
  if (u.status === "removed") {
    const restoreBtn = Global.el("button", { type: "button", class: "secondary-btn", text: "Restore" });
    restoreBtn.addEventListener("click", () => act(restoreBtn, () => send(`/${u.id}`, "PATCH", { status: "active" }), `${u.email} has access again.`));
    const del = Global.el("button", { type: "button", class: "danger-btn", text: "Delete their data" });
    del.addEventListener("click", () => {
      if (!confirm(`Permanently delete everything ${u.email} owns?\n\nEvery resume and access code of theirs is erased. This can't be undone. Their email stays here as removed.`)) return;
      act(del, () => send(`/${u.id}/data`, "DELETE"), `Deleted what ${u.email} owned.`);
    });
    actions.push(restoreBtn, del);
  }
  return Global.el("div", { class: "request-card" }, [
    Global.el("div", { class: "request-info" }, info),
    Global.el("div", { class: "request-actions" }, actions),
  ]);
}

async function load() {
  let users;
  try {
    users = await Global.withLoading(activeEl, send("", "GET"));
  } catch (err) {
    activeEl.replaceChildren(Global.el("p", { class: "field-hint", text: err.message }));
    return;
  }
  const active = users.filter((u) => u.status === "active");
  const removed = users.filter((u) => u.status === "removed");
  activeEl.replaceChildren(
    ...(active.length ? active.map(card) : [Global.el("p", { class: "field-hint", text: "Nobody but you has access yet." })])
  );
  removedEl.classList.toggle("hidden", removed.length === 0);
  removedList.replaceChildren(...removed.map(card));
}

// Add someone (not a sign-in field, so password managers stay out).
(function mountAdd() {
  const input = Global.el("input", {
    type: "email",
    placeholder: "Email address",
    autocomplete: "off",
    "data-1p-ignore": "",
    "data-lpignore": "true",
    "data-bwignore": "true",
    "data-form-type": "other",
    "aria-label": "Email address of someone to let into Resume",
  });
  const refreshSuggestions = Global.suggestEmails(input, "/api/users/suggestions");
  const button = Global.el("button", { type: "button", class: "secondary-btn", text: "Add" });
  const problem = Global.el("p", { class: "field-hint is-error hidden" });
  addEl.replaceChildren(Global.el("div", { class: "member-add" }, [input, button]), problem);

  const show = (text) => {
    problem.textContent = text || "";
    problem.classList.toggle("hidden", !text);
  };
  async function add() {
    const email = input.value.trim();
    if (!email) return;
    show("");
    button.disabled = true;
    try {
      const user = await send("", "POST", { email });
      input.value = "";
      refreshSuggestions();
      Global.showMessage(`${user.email} can now sign in to Resume.`, "success");
      load();
    } catch (err) {
      show(err.message);
    }
    button.disabled = false;
  }
  button.addEventListener("click", add);
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      add();
    }
  });
  input.addEventListener("input", () => show(""));
})();

load();
