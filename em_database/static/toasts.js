// toasts.js - a global, fixed bottom-right download-toast stack.
//
// Backs the toasts that a bare `ds.download()` pops up in Jupyter. The widget
// itself is an invisible anchor; the toasts live on <body> (position: fixed) so
// they float over the notebook regardless of which cell started the download.

const MB = 1e6;

function fmtMB(bytes) {
  const mb = bytes / MB;
  if (mb >= 100) return mb.toFixed(0);
  if (mb >= 10) return mb.toFixed(1);
  return mb.toFixed(2);
}

function esc(value) {
  return String(value).replace(/[&<>"']/g, function (c) {
    return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
  });
}

function el(tag, cls, html) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (html != null) n.innerHTML = html;
  return n;
}

function render({ model, el: root }) {
  root.style.display = "none";  // the widget is just an anchor
  const toastRoot = el("div", "emdb-toast-root");
  document.body.appendChild(toastRoot);

  const cancelling = new Set();
  let nonce = 0;
  function cmd(action, extra) {
    model.set("_command", Object.assign({ action, nonce: nonce++ }, extra || {}));
    model.save_changes();
  }

  let lastSig = "";
  function draw() {
    const downloads = model.get("downloads") || {};
    for (const t of [...cancelling]) if (!(t in downloads)) cancelling.delete(t);
    const tokens = Object.keys(downloads);
    const sig = tokens
      .map(function (t) { return t + (downloads[t].error ? ":e" : cancelling.has(t) ? ":c" : ""); })
      .sort().join("|");
    if (sig === lastSig) {  // only byte-progress changed -> update in place
      for (const t of tokens) {
        if (!downloads[t].error && !cancelling.has(t)) update(t, downloads[t]);
      }
      return;
    }
    lastSig = sig;
    toastRoot.innerHTML = "";
    for (const [token, dl] of Object.entries(downloads)) {
      toastRoot.appendChild(dl.error ? errorToast(token, dl) : progressToast(token, dl));
    }
  }

  function update(token, dl) {
    let card = null;
    for (const c of toastRoot.children) { if (c.dataset.token === token) { card = c; break; } }
    if (!card) return;
    const pct = dl.total > 0 ? Math.min(100, (100 * dl.done) / dl.total) : null;
    const fill = card.querySelector(".emdb-fill");
    if (fill) {
      if (pct == null) { fill.classList.add("indet"); fill.style.width = "32%"; }
      else { fill.classList.remove("indet"); fill.style.width = pct + "%"; }
    }
    const bytes = card.querySelector(".emdb-bytes");
    if (bytes) {
      bytes.textContent = pct == null ? fmtMB(dl.done) + " MB"
        : fmtMB(dl.done) + " / " + fmtMB(dl.total) + " MB · " + pct.toFixed(0) + "%";
    }
  }

  function progressToast(token, dl) {
    const isc = cancelling.has(token);
    const pct = dl.total > 0 ? Math.min(100, (100 * dl.done) / dl.total) : null;
    const card = el("div", "emdb-toast" + (isc ? " cancelling" : ""));
    card.dataset.token = token;
    const bar = (pct == null || isc)
      ? '<div class="emdb-fill indet" style="width:32%"></div>'
      : '<div class="emdb-fill" style="width:' + pct + '%"></div>';
    const bytes = isc ? "Cancelling…"
      : (pct == null ? fmtMB(dl.done) + " MB"
        : fmtMB(dl.done) + " / " + fmtMB(dl.total) + " MB · " + pct.toFixed(0) + "%");
    card.innerHTML =
      '<div class="emdb-toast-row"><span class="emdb-toast-title">' + esc(dl.label) + "</span>" +
      '<button class="emdb-x" title="Cancel download">✕</button></div>' +
      '<div class="emdb-track">' + bar + "</div>" +
      '<div class="emdb-bytes">' + bytes + "</div>";
    card.querySelector(".emdb-x").addEventListener("click", function () {
      cmd("cancel", { token: token });
      cancelling.add(token);
      draw();
    });
    return card;
  }

  function errorToast(token, dl) {
    const card = el("div", "emdb-toast error");
    card.dataset.token = token;
    card.innerHTML =
      '<div class="emdb-toast-row"><span class="emdb-toast-title">Failed: ' + esc(dl.label) + "</span>" +
      '<button class="emdb-x" title="Dismiss">✕</button></div>' +
      '<div class="emdb-toast-err">' + esc(dl.error) + "</div>";
    card.querySelector(".emdb-x").addEventListener("click", function () { cmd("dismiss", { token: token }); });
    return card;
  }

  const onDownloads = function () { draw(); };
  model.on("change:downloads", onDownloads);
  draw();

  return function () { toastRoot.remove(); model.off("change:downloads", onDownloads); };
}

export default { render };
