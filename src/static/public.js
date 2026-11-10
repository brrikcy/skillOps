// Shared by every page: settings from config.js, the Robomy header and footer, small helpers.
(function () {
  var cfg = window.SIMLAB_CONFIG || {};
  var name = cfg.platformName || "SkillOps Live Data";
  var api = (cfg.apiUrl || "").replace(/\/$/, "");

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function header(active) {
    var links = [["/live", "Home"], ["/register", "Register"], ["/docs", "Developer docs"], ["/status", "Status"], ["/signin", "Staff sign-in"]];
    var nav = links.map(function (l) {
      return '<a href="' + l[0] + '"' + (l[0] === active ? ' class="on"' : "") + ">" + l[1] + "</a>";
    }).join("");
    var v = cfg["static"] || "";
    return '<header class="top"><div class="top-inner">' +
      '<a class="brand" href="/live"><img class="brand-robomy" src="/static/robomy-logo.png' + v + '" alt="Robomy">' +
      '<img class="brand-skillops" src="/static/skillops-logo.svg' + v + '" alt="SkillOps">' +
      '<span class="brand-module">Live Data</span></a>' +
      '<nav class="nav">' + nav + "</nav></div></header>";
  }

  function footer() {
    return '<footer class="foot"><div>' + esc(name) + " gives you a database that grows while the business runs." +
      (cfg.localMode ? " <strong>Local test mode.</strong>" : "") + "</div></footer>";
  }

  // Put the header/footer in place and fill every [data-platform] and [data-api] element.
  function frame(active) {
    document.title = document.title.replace("{platform}", name);
    var h = document.getElementById("site-header");
    if (h) h.outerHTML = header(active);
    var f = document.getElementById("site-footer");
    if (f) f.outerHTML = footer();
    document.querySelectorAll("[data-platform]").forEach(function (el) { el.textContent = name; });
    document.querySelectorAll("[data-api]").forEach(function (el) { el.textContent = api || "(API address)"; });
    document.querySelectorAll("[data-practice]").forEach(function (el) { el.textContent = cfg.practiceDataset || "practice_orders"; });
  }

  async function call(method, path, opts) {
    opts = opts || {};
    var headers = Object.assign({}, opts.headers || {});
    if (opts.body !== undefined) headers["content-type"] = "application/json";
    var resp, text, data;
    try {
      resp = await fetch(api + path, { method: method, headers: headers, body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined });
      text = await resp.text();
    } catch (e) {
      return { status: 0, data: { error: { code: "network", message: "Could not reach the server. Check your internet connection.", hint: String(e) } }, text: "" };
    }
    try { data = JSON.parse(text); } catch (e) { data = { raw: text }; }
    return { status: resp.status, data: data, text: text };
  }

  function errText(data) {
    var e = (data && data.error) || {};
    if (!e.message && data && data.message) return data.message;
    return (e.message || "Something went wrong.") + (e.hint ? " " + e.hint : "");
  }

  async function copy(text, btn) {
    try {
      await navigator.clipboard.writeText(text);
    } catch (e) {
      var t = document.createElement("textarea");
      t.value = text; document.body.appendChild(t); t.select();
      try { document.execCommand("copy"); } catch (e2) { /* ignore */ }
      t.remove();
    }
    if (btn) { var old = btn.textContent; btn.textContent = "Copied"; setTimeout(function () { btn.textContent = old; }, 1500); }
  }

  function csv(rows, columns) {
    var head = columns.map(function (c) { return c[1]; });
    var lines = [head].concat(rows.map(function (r) {
      return columns.map(function (c) { var v = r[c[0]]; return v == null ? "" : v; });
    }));
    return lines.map(function (l) {
      return l.map(function (v) { v = String(v); return /[",\n]/.test(v) ? '"' + v.replace(/"/g, '""') + '"' : v; }).join(",");
    }).join("\r\n");
  }

  function download(filename, text) {
    var a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([text], { type: "text/csv" }));
    a.download = filename; document.body.appendChild(a); a.click(); a.remove();
  }

  function localTime(iso) {
    if (!iso) return "";
    var d = new Date(iso);
    if (isNaN(d)) return iso;
    return d.toLocaleString("en-IN", { dateStyle: "medium", timeStyle: "short" });
  }

  window.Robomy = { cfg: cfg, name: name, api: api, esc: esc, frame: frame, call: call, errText: errText,
    copy: copy, csv: csv, download: download, localTime: localTime };
})();
