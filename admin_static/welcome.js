// Start here (welcome.html): the written explanation is static; this adds a live "where you are" box and a done / to do mark on each step, from what the
// person actually has (a usable key, a saved resume, a token, jobs, scores, tailored documents). Nothing here changes anything. Needs Global (shared-assets).

const get = (url) => Global.fetchJSON(url).catch(() => null);

const mark = (name, done, text) => {
  const el = document.querySelector(`[data-status="${name}"]`);
  if (!el) return;
  el.className = "step-status " + (done ? "step-done" : "step-optional");
  el.textContent = text || (done ? "done" : "to do");
};

async function start() {
  const [key, people, tokens, jobs] = await Promise.all([get("/api/api-key"), get("/api/people"), get("/api/tokens"), get("/api/jobs")]);
  const haveKey = Boolean(key && key.usable);
  const haveResume = Boolean(people && people.length);
  const haveToken = Boolean(tokens && tokens.length);
  const list = jobs || [];
  const scored = list.filter((j) => j.tier).length;
  const tailored = list.filter((j) => j.has_documents).length;

  mark("key", haveKey, haveKey ? (key.has_own_key ? "done" : "covered for you") : "to do");
  mark("resume", haveResume);
  mark("extension", haveToken, haveToken ? "connected" : "not set up");
  mark("jobs", list.length > 0, list.length ? `${list.length} job${list.length === 1 ? "" : "s"}` : "none yet");
  mark("scored", scored > 0, scored ? `${scored} scored` : "none yet");
  mark("tailor", tailored > 0, tailored ? `${tailored} made` : "none yet");

  // The one thing to do next, in the order the process needs.
  let next;
  if (!haveKey) next = ["Add your Anthropic key", "/build.html", "It is the first thing the AI needs, and takes about five minutes."];
  else if (!haveResume) next = ["Build your resume", "/build.html", "Everything else is measured against it."];
  else if (!list.length) next = ["Add your first job", "/jobs.html", "Paste one in, or set up the Chrome extension to send them."];
  else if (!scored) next = ["See how your jobs scored", "/jobs.html", "They are scored in the background; open the Jobs page."];
  else if (!tailored) next = ["Make a resume and cover letter for a job you like", "/jobs.html", "Open a job and press the button that makes them."];
  else next = null;

  const summary = document.getElementById("where-summary");
  const actions = document.getElementById("where-next");
  if (next) {
    summary.textContent = `Your next step: ${next[0]}. ${next[2]}`;
    actions.replaceChildren(Global.el("a", { class: "save-btn", "data-btn-icon": "arrow-right", href: next[1], text: next[0] }));
  } else {
    summary.textContent = "You have set up everything and made documents for at least one job. From here it is just the Jobs page.";
    actions.replaceChildren(Global.el("a", { class: "save-btn", "data-btn-icon": "briefcase", href: "/jobs.html", text: "Open the Jobs page" }));
  }
  Global.iconifyButtons && Global.iconifyButtons(document.body);
}

start();
