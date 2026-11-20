// The install page (extension.html): shows the newest extension download, if one has been posted. The server picks the file (app/extension.py).
const host = document.getElementById("ext-download");
const versionEl = document.getElementById("ext-version");

function kb(bytes) {
  return bytes < 1024 * 1024 ? `${Math.max(1, Math.round(bytes / 1024))} KB` : `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

(async () => {
  try {
    const info = await Global.withLoading(host, Global.fetchJSON("/api/extension"));
    if (!info.available) {
      host.replaceChildren(Global.el("p", { class: "hint", text: "The download isn't posted yet. Check back soon." }));
      return;
    }
    versionEl.textContent = `version ${info.version}, ${kb(info.size)}`;
    const link = Global.el("a", { class: "save-btn", href: info.download_url, download: info.filename, "data-btn-icon": "download", text: `Download ${info.filename}` });
    host.replaceChildren(link);
    Global.iconifyButtons && Global.iconifyButtons(host);
  } catch (err) {
    host.replaceChildren(Global.el("p", { class: "hint is-error", text: err.message }));
  }
})();
