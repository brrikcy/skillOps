// SkillOps: small helpers. Every page works without this file (forms post normally);
// it adds: confirmations, no double sending, ticks that save in place with retries,
// copy buttons, and the auto-refreshing MCQ monitor.
(function () {
  "use strict";

  // ---------------------------------------------------------------- a question with several answers (our own dialog)
  // soAsk({title, message, buttons: [{label, value, kind: "primary"|"danger"|""}], cancelValue}) -> Promise of the value chosen.
  window.soAsk = function (o) {
    return new Promise(function (resolve) {
      var prev = document.activeElement;
      var wrap = document.createElement("div");
      wrap.className = "so-modal";
      wrap.innerHTML = '<div class="so-modal-box" role="alertdialog" aria-modal="true" aria-label=""><h3 class="so-modal-title"></h3><p class="so-modal-msg"></p><div class="so-modal-actions"></div></div>';
      wrap.querySelector(".so-modal-box").setAttribute("aria-label", o.title || "Please choose");
      wrap.querySelector(".so-modal-title").textContent = o.title || "";
      wrap.querySelector(".so-modal-msg").textContent = o.message || "";
      var row = wrap.querySelector(".so-modal-actions");
      var buttons = [];
      (o.buttons || []).forEach(function (b) {
        var el = document.createElement("button");
        el.type = "button";
        el.className = "btn" + (b.kind ? " " + b.kind : "");
        el.textContent = b.label;
        el.addEventListener("click", function () { done(b.value); });
        row.appendChild(el);
        buttons.push(el);
      });
      function done(v) { document.removeEventListener("keydown", onKey, true); wrap.remove(); if (prev && prev.focus) { try { prev.focus(); } catch (e) { /* ignore */ } } resolve(v); }
      function onKey(e) {
        if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); done(o.cancelValue === undefined ? null : o.cancelValue); }
        else if (e.key === "Tab") {
          e.preventDefault();
          var i = buttons.indexOf(document.activeElement);
          buttons[(i + (e.shiftKey ? buttons.length - 1 : 1)) % buttons.length].focus();
        }
      }
      document.addEventListener("keydown", onKey, true);
      document.body.appendChild(wrap);
      var first = buttons.filter(function (b) { return b.classList.contains("primary"); })[0] || buttons[0];
      if (first) first.focus();
    });
  };

  // ---------------------------------------------------------------- confirmations
  // Our own dialog (never the browser's). Click is held, the dialog asks, and OK repeats the click.
  var confirmed = null;
  function askConfirm(message, destructive, onOk) {
    var prev = document.activeElement;
    var wrap = document.createElement("div");
    wrap.className = "so-modal";
    wrap.innerHTML = '<div class="so-modal-box" role="alertdialog" aria-modal="true" aria-label="Please confirm">' +
      '<p class="so-modal-msg"></p><div class="so-modal-actions">' +
      '<button type="button" class="btn" data-no>Cancel</button>' +
      '<button type="button" class="btn ' + (destructive ? "danger" : "primary") + '" data-yes>' +
      (destructive ? "Delete" : "OK") + '</button></div></div>';
    wrap.querySelector(".so-modal-msg").textContent = message;
    function close() { document.removeEventListener("keydown", onKey, true); wrap.remove(); if (prev && prev.focus) prev.focus(); }
    function onKey(e) {
      if (e.key === "Escape") { e.preventDefault(); close(); }
      else if (e.key === "Tab") {
        e.preventDefault();
        var a = wrap.querySelector("[data-no]"), b = wrap.querySelector("[data-yes]");
        (document.activeElement === a ? b : a).focus();
      }
    }
    wrap.addEventListener("click", function (e) {
      if (e.target.hasAttribute("data-yes")) { close(); onOk(); }
      else if (e.target.hasAttribute("data-no") || e.target === wrap) close();
    });
    document.addEventListener("keydown", onKey, true);
    document.body.appendChild(wrap);
    wrap.querySelector("[data-no]").focus();
  }
  document.addEventListener("click", function (ev) {
    var el = ev.target.closest ? ev.target.closest("[data-confirm]") : null;
    if (!el) return;
    if (confirmed === el) { confirmed = null; return; }
    ev.preventDefault();
    var msg = el.getAttribute("data-confirm");
    var destructive = /delete|remove|for good|cannot be undone/i.test(msg + " " + el.textContent);
    askConfirm(msg, destructive, function () { confirmed = el; el.click(); });
  });

  // ---------------------------------------------------------------- Settings: a pane over the page (not a browser popup)
  // The admin's Settings pages load inside the pane (they know they are framed and drop the page chrome).
  // Without this script the links still open the Settings pages as ordinary pages.
  (function () {
    var box = document.getElementById("so-settings");
    if (!box) return;
    var frame = box.querySelector("iframe");
    var links = box.querySelectorAll("[data-settings-link]");
    var loads = 0, returnFocus = null;
    function mark(path) {
      var best = null;
      for (var i = 0; i < links.length; i++) {
        var h = links[i].getAttribute("href");
        links[i].classList.remove("on"); links[i].removeAttribute("aria-current");
        if ((path === h || path.indexOf(h + "/") === 0) && (!best || h.length > best.getAttribute("href").length)) best = links[i];
      }
      if (best) { best.classList.add("on"); best.setAttribute("aria-current", "page"); }
    }
    function open(href) {
      returnFocus = document.activeElement;
      loads = 0;
      var target = href && href !== "/settings" ? href : frame.getAttribute("data-src");
      mark(target);
      frame.src = target;
      box.hidden = false;
      document.documentElement.classList.add("so-lock");
      var close = box.querySelector("[data-settings-close]");
      if (close) close.focus();
    }
    function close() {
      box.hidden = true;
      document.documentElement.classList.remove("so-lock");
      frame.removeAttribute("src");
      if (returnFocus && returnFocus.focus) returnFocus.focus();
      if (loads > 1) location.reload();      // something may have changed: show it
    }
    frame.addEventListener("load", function () {
      var loc;
      try { loc = frame.contentWindow.location; } catch (e) { return; }
      if (!loc || loc.href === "about:blank") return;
      loads += 1;
      if (!/^\/(admin|settings)(\/|$)/.test(loc.pathname)) {   // left Settings (a monitoring page, sign-in): use the whole window
        window.location.href = loc.pathname + loc.search;
        return;
      }
      mark(loc.pathname);
    });
    document.addEventListener("click", function (ev) {
      var t = ev.target.closest ? ev.target : ev.target.parentNode;
      var opener = t.closest("[data-settings-open]");
      if (opener) { ev.preventDefault(); open(opener.getAttribute("href")); return; }
      var link = t.closest("[data-settings-link]");
      if (link && box.contains(link)) { ev.preventDefault(); mark(link.getAttribute("href")); loads = Math.max(loads, 1); frame.src = link.getAttribute("href"); return; }
      if (t.closest("[data-settings-close]") || ev.target === box) close();
    });
    document.addEventListener("keydown", function (ev) {
      if (ev.key === "Escape" && !box.hidden && !document.querySelector(".so-modal")) close();
    });
  })();

  // ---------------------------------------------------------------- runbook and planner: a large pane over the page
  // Links marked data-popup open in a big overlay (like Settings, only larger), not in a browser window. The page
  // loads inside the pane (it knows it is framed and drops the page chrome). Shift- or Ctrl-click opens a normal page.
  var READER = "so-reader";
  var inReader = window.name === READER && window.parent !== window;
  if (inReader) {
    // inside the pane: say when something was saved, and keep "back" and other-page links from loading a page in the pane
    var tell = function (msg) { try { window.parent.postMessage(msg, window.location.origin); } catch (e) { /* ignore */ } };
    if (window.fetch) {
      var realFetch = window.fetch;
      window.fetch = function (u, o) {
        var pr = realFetch.apply(this, arguments);
        if (o && /^post$/i.test(o.method || "")) pr.then(function (r) { if (r && r.ok) tell({ soChanged: true }); }, function () {});
        return pr;
      };
    }
    document.addEventListener("click", function (ev) {
      var a = ev.target.closest ? ev.target.closest("a[href]") : null;
      if (!a || ev.defaultPrevented || a.target === "_blank" || ev.ctrlKey || ev.metaKey || ev.shiftKey) return;
      if (a.hasAttribute("data-close-popup")) { ev.preventDefault(); tell({ soClose: true }); return; }
      var u;
      try { u = new URL(a.href, window.location.href); } catch (e) { return; }
      if (u.origin !== window.location.origin) return;
      if (!/^\/(b\/[^/]+\/p\/|monitor\/runbooks\/|admin\/runbooks\/)/.test(u.pathname)) {
        ev.preventDefault();
        tell({ soNavigate: u.href });                               // the pane closes (asking first if something is unsaved), the page goes there
      }
    }, true);
    document.addEventListener("keydown", function (ev) {            // Esc works wherever the focus is inside the pane
      if (ev.key === "Escape" && !ev.defaultPrevented && !document.querySelector(".so-modal")) tell({ soClose: true });
    });
    document.addEventListener("submit", function (ev) { if (!ev.defaultPrevented) tell({ soChanged: true }); });
  }
  (function () {
    if (inReader) return;
    var box = null, frame = null, titleEl = null, fullLink = null, returnFocus = null, dirty = false, loads = 0, goTo = "";
    function build() {
      box = document.createElement("div");
      box.className = "so-reader";
      box.hidden = true;
      box.setAttribute("role", "dialog");
      box.setAttribute("aria-modal", "true");
      box.setAttribute("aria-label", "Runbook");
      box.innerHTML = '<div class="so-reader-box">' +
        '<header class="so-reader-bar"><strong class="so-reader-title"></strong>' +
        '<a class="so-reader-full btn sm ghost" target="_blank" rel="noopener" title="Open in its own page">Open in a page</a>' +
        '<button class="so-reader-close" type="button" aria-label="Close" title="Close (Esc)">&times;</button></header>' +
        '<iframe class="so-reader-frame" name="' + READER + '" title="Runbook"></iframe></div>';
      document.body.appendChild(box);
      frame = box.querySelector("iframe");
      titleEl = box.querySelector(".so-reader-title");
      fullLink = box.querySelector(".so-reader-full");
      box.addEventListener("click", function (ev) {
        if (ev.target === box || (ev.target.closest && ev.target.closest(".so-reader-close"))) close();
      });
      frame.addEventListener("load", function () {
        var doc;
        try { doc = frame.contentDocument; } catch (e) { return; }
        if (!doc || !frame.getAttribute("src")) return;
        loads += 1;
        var t = (doc.title || "").replace(/\s*·\s*[^·]*$/, "");
        if (t) titleEl.textContent = t;
        fullLink.href = frame.contentWindow.location.href;
      });
      window.addEventListener("message", function (ev) {
        if (ev.origin !== window.location.origin || !frame || ev.source !== frame.contentWindow || !ev.data) return;
        if (ev.data.soChanged) dirty = true;
        if (ev.data.soClose) close();
        if (ev.data.soNavigate) {
          var to;
          try { to = new URL(ev.data.soNavigate, window.location.href); } catch (e) { to = null; }
          if (to && to.origin === window.location.origin) { goTo = to.href; close(); }
        }
      });
      document.addEventListener("keydown", function (ev) {
        if (ev.key === "Escape" && box && !box.hidden && !document.querySelector(".so-modal")) close();
      });
    }
    function open(href, label) {
      if (!box) build();
      returnFocus = document.activeElement;
      dirty = false; loads = 0;
      titleEl.textContent = label || "Runbook";
      fullLink.href = href;
      frame.src = href;
      box.hidden = false;
      document.documentElement.classList.add("so-lock");
      box.querySelector(".so-reader-close").focus();
    }
    function closeNow() {
      if (!box || box.hidden) return;
      box.hidden = true;
      document.documentElement.classList.remove("so-lock");
      frame.removeAttribute("src");
      if (goTo) { var to = goTo; goTo = ""; window.location.href = to; return; }      // a link in the pane to another page
      if (returnFocus && returnFocus.focus) { try { returnFocus.focus(); } catch (e) { /* ignore */ } }
      if (dirty) window.location.reload();      // something was saved in the pane: show it on the page behind
    }
    function close() {
      if (!box || box.hidden) return;
      var pl = null;
      try { pl = frame.contentWindow && frame.contentWindow.__soPlanner; } catch (e) { pl = null; }
      if (!pl || !pl.dirty()) { closeNow(); return; }
      window.soAsk({
        title: "Unsaved changes",
        message: "The session plan has changes that are not saved. Nothing is saved automatically.",
        buttons: [{ label: "Save and close", value: "save", kind: "primary" }, { label: "Discard changes", value: "discard", kind: "danger" }, { label: "Keep editing", value: "stay" }],
        cancelValue: "stay"
      }).then(function (v) {
        if (v === "save") pl.save().then(function (ok) { if (ok) { dirty = true; closeNow(); } });
        else if (v === "discard") { pl.discard(); closeNow(); }
        else goTo = "";                                              // keep editing: stay here
      });
    }
    document.addEventListener("click", function (ev) {
      var a = ev.target.closest ? ev.target.closest("a[data-popup]") : null;
      if (!a || ev.ctrlKey || ev.metaKey || ev.shiftKey || ev.button !== 0) return;
      ev.preventDefault();
      open(a.href, (a.textContent || "").trim().replace(/\s+/g, " "));
    });
  })();

  // ---------------------------------------------------------------- the side menu
  // Wide screens: the button hides or shows the menu, and the choice is remembered (cookie, so the
  // server draws it straight away). Narrow screens: the menu slides in over the page and closes again.
  var narrow = function () { return window.matchMedia && window.matchMedia("(max-width: 900px)").matches; };
  document.addEventListener("click", function (ev) {
    var t = ev.target.closest ? ev.target.closest("[data-nav-toggle], [data-nav-close], .side a") : null;
    if (!t) return;
    var body = document.body;
    if (t.hasAttribute("data-nav-toggle")) {
      if (narrow()) { body.classList.toggle("nav-open"); return; }
      var closed = body.classList.toggle("nav-closed");
      document.cookie = "so_nav=" + (closed ? "closed" : "open") + "; path=/; max-age=31536000; SameSite=Lax";
    } else {
      body.classList.remove("nav-open");
    }
  });
  document.addEventListener("keydown", function (ev) {
    if (ev.key === "Escape") document.body.classList.remove("nav-open");
  });

  // ---------------------------------------------------------------- light or dark
  // The choice is kept in a cookie, so the server draws every page in it straight away (no flash).
  // Until someone chooses, the page follows the device's own light or dark setting.
  document.addEventListener("click", function (ev) {
    var b = ev.target.closest ? ev.target.closest("[data-theme-toggle]") : null;
    if (!b) return;
    var root = document.documentElement;
    var now = root.getAttribute("data-theme") ||
      (window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
    var next = now === "dark" ? "light" : "dark";
    root.setAttribute("data-theme", next);
    document.cookie = "so_theme=" + next + "; path=/; max-age=31536000; SameSite=Lax";
  });

  // ---------------------------------------------------------------- small menus close when you click elsewhere
  document.addEventListener("click", function (ev) {
    document.querySelectorAll("details.row-menu[open], details.t-menu[open], details.profile-menu[open]").forEach(function (d) {
      if (!d.contains(ev.target)) d.open = false;
    });
  });

  // ---------------------------------------------------------------- filters that apply as you choose
  document.addEventListener("change", function (ev) {
    if (ev.target.matches && ev.target.matches("select[data-autosubmit]") && ev.target.form) ev.target.form.submit();
  });

  // ---------------------------------------------------------------- pages that wait for background work
  var auto = document.querySelector("[data-autorefresh]");
  if (auto) {
    setTimeout(function () { location.reload(); }, 1000 * (parseInt(auto.getAttribute("data-autorefresh"), 10) || 5));
  }

  // ---------------------------------------------------------------- forms wait after sending
  document.addEventListener("submit", function (ev) {
    var form = ev.target;
    // a form the page sends itself (the planner's + and Add buttons) calls preventDefault: it must stay usable
    if (ev.defaultPrevented || (form.getAttribute("method") || "").toLowerCase() !== "post" || form.hasAttribute("data-js")) return;
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
          if (document.querySelector(".so-modal")) return;      // a confirmation is open: keep the button it will click
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

    // The Done button saves in place. "Saved" only after the server says so; retries on failure.
    var updateCount = function () {
      var all = cols.querySelectorAll(".t-act:not(.t-own)").length;
      var done = cols.querySelectorAll(".t-act.done:not(.t-own)").length;
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
      if (kind !== "tick-inline" || !window.fetch) return;
      ev.preventDefault();
      var act = form.closest(".t-act");
      var btn = form.querySelector("button");
      {
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
      }
    });
  }

  // ---------------------------------------------------------------- runbook structure: live counts
  var mapForm = document.querySelector("form[data-map]");
  if (mapForm) {
    var recount = function () {
      var sums = { 1: 0, 2: 0, 3: 0, 4: 0 };
      mapForm.querySelectorAll("[data-pat]").forEach(function (row) {
        var picked = row.querySelector("input[type=radio]:checked");
        var lvl = picked ? picked.value : "0";
        row.classList.toggle("is-heading", lvl !== "0");
        if (sums[lvl] !== undefined) sums[lvl] += parseInt(row.getAttribute("data-count"), 10) || 0;
      });
      Object.keys(sums).forEach(function (n) {
        var b = mapForm.querySelector("[data-lvl-count='" + n + "']");
        if (b) { b.textContent = sums[n]; b.parentNode.classList.toggle("zero", !sums[n]); }
      });
    };
    mapForm.addEventListener("change", recount);
    mapForm.addEventListener("input", function (ev) {
      var n = ev.target.getAttribute && ev.target.getAttribute("data-name-input");
      if (!n) return;
      document.querySelectorAll("[data-lvl-name='" + n + "']").forEach(function (el) { el.textContent = ev.target.value || ("Level " + n); });
    });
    recount();
  }

  // ---------------------------------------------------------------- Progress: the columns fit the screen
  var fitGrid = document.querySelector("[data-fit]");
  if (fitGrid) {
    var fit = function () {
      var top = fitGrid.getBoundingClientRect().top + window.scrollY;
      var pad = parseFloat(getComputedStyle(document.querySelector(".content")).paddingBottom) || 0;
      fitGrid.style.setProperty("--fit", Math.max(260, window.innerHeight - top - pad) + "px");
      fitGrid.classList.add("fit");
    };
    fit();
    window.addEventListener("resize", fit);
    window.addEventListener("load", fit);
  }

  // ---------------------------------------------------------------- chart readout on hover and keyboard focus
  if (document.querySelector("[data-tip]")) {
    var tip = document.createElement("div");
    tip.className = "chart-tip";
    tip.hidden = true;
    tip.setAttribute("role", "status");
    document.body.appendChild(tip);
    var showTip = function (el) {
      var r = el.getBoundingClientRect();
      tip.textContent = el.getAttribute("data-tip");          // text only: names come from user data
      tip.hidden = false;
      var w = tip.offsetWidth / 2 + 8;
      tip.style.left = Math.min(Math.max(r.left + r.width / 2, w), window.innerWidth - w) + "px";
      tip.style.top = Math.max(r.top, tip.offsetHeight + 14) + "px";
    };
    var hideTip = function () { tip.hidden = true; };
    document.addEventListener("pointerover", function (ev) {
      var el = ev.target.closest && ev.target.closest("[data-tip]");
      if (el) showTip(el); else hideTip();
    });
    document.addEventListener("focusin", function (ev) {
      var el = ev.target.closest && ev.target.closest("[data-tip]");
      if (el) showTip(el); else hideTip();
    });
    document.addEventListener("focusout", hideTip);
    window.addEventListener("scroll", hideTip, { passive: true });
  }
  // ---------------------------------------------------------------- skip link: move focus to the page, not just the scroll position
  (function () {
    var skip = document.querySelector(".skip-link");
    if (!skip) return;
    skip.addEventListener("click", function () {
      var main = document.getElementById("main");
      if (main) setTimeout(function () { main.focus({ preventScroll: false }); }, 0);
    });
  })();
})();
