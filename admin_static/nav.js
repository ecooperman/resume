// The sidebar, built once for both admin pages with Global.buildNav (shared-assets/theme.js).
// Only the "which page is active" logic is specific to this app.
const onCodes = window.location.pathname === "/codes.html";
Global.buildNav([
  { href: "/", icon: "person", label: "People", active: !onCodes },
  { href: "/codes.html", icon: "lock", label: "Access codes", active: onCodes },
  { icon: "refresh", label: "Refresh", onclick: () => location.reload() },
]);
