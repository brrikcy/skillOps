// SkillOps: small helpers. Every page works without this file (forms post normally);
// it adds: confirmations, no double sending, ticks that save in place with retries,
// copy buttons, and the auto-refreshing MCQ monitor.
(function () {
  "use strict";

  // ---------------------------------------------------------------- confirmations
  document.addEventListener("click", function (ev) {
    var el = ev.target.closest ? ev.target.closest("[data-confirm]") : null;
    if (el && !window.confirm(el.getAttribute("data-confirm"))) ev.preventDefault();
  });

  // ---------------------------------------------------------------- forms wait after sending
  document.addEventListener("submit", function (ev) {
    var form = ev.target;
    if ((form.getAttribute("method") || "").toLowerCase() !== "post" || form.hasAttribute("data-js")) return;
    setTimeout(function () {
      var buttons = form.querySelectorAll("button[type=submit], button:not([type])");
      for (var i = 0; i < buttons.length; i++) {
        if (!buttons[i].disabled) { buttons[i].disabled = true; buttons[i].setAttribute("data-busy", "1"); }
      }
    }, 0);
  });
  window.addEventListener("pageshow", function (ev) {
    if (!ev.persisted) return;
    var buttons = document.querySelectorAll("button[data-busy]");
    for (var i = 0; i < buttons.length; i++) { buttons[i].disabled = false; buttons[i].removeAttribute("data-busy"); }
  });

  // ---------------------------------------------------------------- ticks
  // The tick is sent at once. "Saved" shows only after the server confirms. On failure it says
  // "Not saved, retrying" and tries again (the server keeps one tick however often it is sent).
  function setState(text, cls) {
    var el = document.querySelector("[data-save-state]");
    if (!el) return;
    el.textContent = text;
    el.className = "save-state " + (cls || "");
  }

  function sendTick(form, attempt) {
    var data = new URLSearchParams(new FormData(form));
    setState(attempt ? "Not saved, retrying…" : "Saving…", attempt ? "bad" : "");
    fetch(form.action, {
      method: "POST", body: data, credentials: "same-origin",
      headers: { "accept": "application/json", "x-requested-with": "fetch", "x-page": location.pathname }
    }).then(function (r) {
      if (r.status === 401) {
        setState("Not saved: your sign-in has ended. Sign in again in another tab; this keeps trying.", "bad");
        throw new Error("signed out");
      }
      if (r.status >= 400 && r.status < 500) {
        return r.json().then(function (j) {
          setState("Not saved: " + ((j.error && j.error.message) || "refused") + " Refresh the page.", "bad");
          return null;
        });
      }
      if (!r.ok) throw new Error("server " + r.status);
      return r.json();
    }).then(function (j) {
      if (!j) return;
      setState("Saved", "ok");
      // Show the new state: reload keeps everything simple and correct (tree, counts, buttons).
      setTimeout(function () { location.reload(); }, 350);
    }).catch(function () {
      var wait = Math.min(30000, 1000 * Math.pow(2, attempt || 0));
      setTimeout(function () { sendTick(form, (attempt || 0) + 1); }, wait);
    });
  }

  document.addEventListener("submit", function (ev) {
    var form = ev.target;
    if (form.getAttribute("data-js") !== "tick" || !window.fetch) return;
    ev.preventDefault();
    var btn = form.querySelector("button");
    if (btn) btn.disabled = true;
    sendTick(form, 0);
  });

  // ---------------------------------------------------------------- direct upload to S3 keeps the file's name
  document.addEventListener("change", function (ev) {
    var input = ev.target;
    if (input.type !== "file" || !input.form) return;
    var meta = input.form.querySelector("input[name='x-amz-meta-filename']");
    if (meta && input.files && input.files[0]) meta.value = input.files[0].name.slice(0, 200);
  });

  // ---------------------------------------------------------------- copy buttons
  document.addEventListener("click", function (ev) {
    var btn = ev.target.closest ? ev.target.closest("[data-copy]") : null;
    if (!btn) return;
    var text = btn.getAttribute("data-copy-text");
    if (!text) {
      var box = btn.parentElement.querySelector("code, input, textarea");
      text = box ? (box.value || box.textContent) : "";
    }
    var done = function () { var old = btn.textContent; btn.textContent = "Copied"; setTimeout(function () { btn.textContent = old; }, 1400); };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(done, function () { fallbackCopy(text); done(); });
    } else { fallbackCopy(text); done(); }
  });
  function fallbackCopy(text) {
    var t = document.createElement("textarea");
    t.value = text; document.body.appendChild(t); t.select();
    try { document.execCommand("copy"); } catch (e) { /* ignore */ }
    t.remove();
  }

  // ---------------------------------------------------------------- keyboard: arrows move between items
  document.addEventListener("keydown", function (ev) {
    if (ev.target.closest && ev.target.closest("input, textarea, select")) return;
    if (ev.altKey || ev.ctrlKey || ev.metaKey) return;
    var link = null;
    if (ev.key === "ArrowRight") link = document.querySelector("[data-next]");
    if (ev.key === "ArrowLeft") link = document.querySelector("[data-prev]");
    if (link && link.href) location.href = link.href;
  });

  // ---------------------------------------------------------------- refreshing panels (MCQ monitor, job status)
  var panel = document.querySelector("[data-refresh]");
  if (panel) {
    var every = parseInt(panel.getAttribute("data-refresh"), 10) || 10;
    var stamp = document.querySelector("[data-refresh-stamp]");
    var refresh = function () {
      fetch(panel.getAttribute("data-refresh-url"), { credentials: "same-origin", headers: { "x-requested-with": "fetch-html" } })
        .then(function (r) { if (!r.ok) throw new Error(r.status); return r.text(); })
        .then(function (html) {
          panel.innerHTML = html;
          if (stamp) stamp.textContent = "Updated " + new Date().toLocaleTimeString("en-IN");
        })
        .catch(function () { if (stamp) stamp.textContent = "Could not refresh; trying again…"; })
        .then(function () { setTimeout(refresh, every * 1000); });
    };
    setTimeout(refresh, every * 1000);
  }

  // ---------------------------------------------------------------- class view (newspaper columns)
  var cols = document.querySelector("[data-columns]");
  if (cols) {
    var status = document.querySelector("[data-save-state]");
    var say = function (text, cls) {
      if (!status) return;
      status.textContent = text;
      status.className = "t-status " + (cls || "");
      if (cls === "ok") setTimeout(function () { if (status.textContent === text) status.className = "t-status"; }, 1800);
    };
    // Column geometry: moves always land on a column edge, so no half column shows on the left.
    var metrics = function () {
      var cs = getComputedStyle(cols);
      var gap = parseFloat(cs.columnGap) || 34;
      var padL = parseFloat(cs.paddingLeft) || 0, padR = parseFloat(cs.paddingRight) || 0;
      var w = cols.clientWidth - padL - padR;
      var want = parseFloat(cs.columnWidth) || 400;
      var n = Math.max(1, Math.floor((w + gap) / (want + gap)));
      return { pitch: (w - (n - 1) * gap) / n + gap, n: n, padL: padL };
    };
    var moveBy = function (columns) {
      var m = metrics();
      var target = Math.round(cols.scrollLeft / m.pitch + columns) * m.pitch;
      cols.scrollLeft = Math.max(0, Math.min(target, cols.scrollWidth - cols.clientWidth));
    };
    var atEnd = function () { return cols.scrollLeft + cols.clientWidth >= cols.scrollWidth - 4; };
    var atStart = function () { return cols.scrollLeft <= 2; };

    // Open at the item named in the address (#a-n123): bring its column into view.
    var goTo = function (el) {
      if (!el) return;
      var m = metrics();
      var x = el.getBoundingClientRect().left - cols.getBoundingClientRect().left + cols.scrollLeft - m.padL;
      cols.style.scrollBehavior = "auto";
      cols.scrollLeft = Math.max(0, Math.floor((x + 2) / m.pitch) * m.pitch);
      cols.style.scrollBehavior = "";
      el.classList.add("focus-flash");
    };
    if (location.hash && location.hash.indexOf("#a-") === 0) {
      setTimeout(function () { goTo(document.getElementById(location.hash.slice(1))); }, 30);
    }

    // The mouse wheel moves sideways through the columns.
    cols.addEventListener("wheel", function (ev) {
      if (ev.shiftKey || Math.abs(ev.deltaX) > Math.abs(ev.deltaY)) return;
      if (cols.scrollWidth <= cols.clientWidth) return;
      ev.preventDefault();
      cols.scrollLeft += ev.deltaY;
    }, { passive: false });

    // Keys: one screen at a time; at the end, on to the next section.
    document.addEventListener("keydown", function (ev) {
      if (ev.target.closest && ev.target.closest("input, textarea, select, summary")) return;
      if (ev.altKey || ev.ctrlKey || ev.metaKey) return;
      var fwd = ev.key === "ArrowRight" || ev.key === "PageDown" || (ev.key === " " && !ev.shiftKey);
      var back = ev.key === "ArrowLeft" || ev.key === "PageUp" || (ev.key === " " && ev.shiftKey);
      if (!fwd && !back) return;
      ev.preventDefault();
      if (fwd) {
        if (atEnd()) { var n = document.querySelector("[data-next-section]"); if (n) location.href = n.href; }
        else moveBy(metrics().n);
      } else {
        if (atStart()) { var p = document.querySelector("[data-prev-section]"); if (p) location.href = p.href; }
        else moveBy(-metrics().n);
      }
    });

    // Larger or smaller text (remembered on this computer only).
    var size = 16;
    try { size = parseInt(localStorage.getItem("skillops.teachSize") || "16", 10) || 16; } catch (e) { size = 16; }
    var applySize = function () { document.body.style.setProperty("--t-fs", size + "px"); };
    applySize();
    document.addEventListener("click", function (ev) {
      var b = ev.target.closest ? ev.target.closest("[data-size]") : null;
      if (!b) return;
      size = Math.max(13, Math.min(26, size + parseInt(b.getAttribute("data-size"), 10) * 2));
      applySize();
      try { localStorage.setItem("skillops.teachSize", String(size)); } catch (e) { /* not kept; fine */ }
    });

    // Done and Repeat buttons save in place. "Saved" only after the server says so; retries on failure.
    var updateCount = function () {
      var all = cols.querySelectorAll(".t-act").length;
      var done = cols.querySelectorAll(".t-act.done").length;
      var c = document.querySelector("[data-done-count]");
      var bar = document.querySelector("[data-done-bar]");
      if (c) c.textContent = done;
      if (bar) bar.style.width = (all ? Math.round(100 * done / all) : 0) + "%";
    };
    var send = function (form, url, attempt, onDone) {
      var data = new URLSearchParams(new FormData(form));
      say(attempt ? "Not saved, retrying…" : "Saving…", attempt ? "bad" : "busy");
      fetch(url, { method: "POST", body: data, credentials: "same-origin",
                   headers: { "accept": "application/json", "x-requested-with": "fetch", "x-page": location.pathname } })
        .then(function (r) {
          if (r.status === 401) { say("Not saved: your sign-in has ended. Sign in again in another tab; this keeps trying.", "bad"); throw new Error("signed out"); }
          if (r.status >= 400 && r.status < 500) {
            return r.json().then(function (j) { say("Not saved: " + ((j.error && j.error.message) || "refused"), "bad"); return null; });
          }
          if (!r.ok) throw new Error("server " + r.status);
          return r.json();
        })
        .then(function (j) { if (j) { onDone(j); say("Saved", "ok"); } })
        .catch(function () {
          setTimeout(function () { send(form, url, (attempt || 0) + 1, onDone); }, Math.min(30000, 1000 * Math.pow(2, attempt || 0)));
        });
    };
    document.addEventListener("submit", function (ev) {
      var form = ev.target;
      var kind = form.getAttribute("data-js");
      if ((kind !== "tick-inline" && kind !== "repeat-inline") || !window.fetch) return;
      ev.preventDefault();
      var act = form.closest(".t-act");
      var btn = form.querySelector("button");
      if (kind === "tick-inline") {
        var isDone = act.classList.contains("done");
        send(form, form.getAttribute(isDone ? "data-untick" : "data-tick"), 0, function (j) {
          var nowDone = j.state === "done";
          act.classList.toggle("done", nowDone);
          btn.classList.toggle("on", nowDone);
          btn.setAttribute("aria-pressed", nowDone ? "true" : "false");
          btn.querySelector("span").textContent = nowDone ? "Done" : "Mark done";
          btn.title = nowDone ? "Done (click to un-mark)" : "Mark done";
          updateCount();
        });
      } else {
        var on = form.querySelector("input[name=on]");
        send(form, form.action, 0, function (j) {
          act.classList.toggle("repeat", !!j.repeat);
          btn.classList.toggle("on", !!j.repeat);
          btn.setAttribute("aria-pressed", j.repeat ? "true" : "false");
          btn.title = j.repeat ? "Marked: needs a repeat (click to clear)" : "Needs a repeat";
          on.value = j.repeat ? "0" : "1";
        });
      }
    });
  }
})();
