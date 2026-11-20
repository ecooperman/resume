// The sidebar, built once for every admin page with Global.buildNav (shared-assets/theme.js). Only an administrator is
// offered Access codes and Users; everyone else has just their own resume. The server enforces this too (these pages and
// routes refuse anyone else), so hiding the links is only tidiness.
const path = window.location.pathname;
const header = Global.mountUserBadge && Global.mountUserBadge();
(async () => {
  let me = { is_admin: false };
  try {
    me = await Global.fetchJSON("/api/me");
  } catch (err) {
    /* the page's own requests will say what is wrong */
  }
  window.RESUME_ME = me;
  const items = [{ href: "/", icon: "person", label: me.is_admin ? "People" : "My resume", active: path === "/" }];
  items.push({ href: "/build.html", icon: "zap", label: "Build my resume", active: path === "/build.html" });
  items.push({ href: "/jobs.html", icon: "briefcase", label: "Jobs", active: path === "/jobs.html" || path === "/extension.html" });
  items.push({
    href: "/invites.html", icon: "mail", label: "Invites", active: path === "/invites.html",
    badge: me.is_admin ? { id: "nav-invite-count", count: me.pending_invites || 0 } : null, // how many requests wait for an administrator
  });
  if (me.is_admin) {
    items.push({ href: "/codes.html", icon: "lock", label: "Access codes", active: path === "/codes.html" });
    items.push({ href: "/users.html", icon: "users", label: "Users", active: path === "/users.html" });
  }
  items.push({ icon: "refresh", label: "Refresh", onclick: () => location.reload() });
  Global.buildNav(items);
  document.dispatchEvent(new Event("resume-me-ready"));
})();
