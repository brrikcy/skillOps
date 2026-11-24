// The runbook's PDF in a pane: the sections list on the left jumps the PDF to a page; trainers can mark actions done.
(function () {
  "use strict";
  var root = document.querySelector("[data-rbv]");
  if (!root) return;
  var stage = root.querySelector("[data-stage]");
  var frame = root.querySelector("[data-frame]");
  var loading = root.querySelector("[data-loading]");
  var serial = 0;
  var base = root.getAttribute("data-pdf");
  var here = root.querySelector("[data-here]");
  var stateEl = root.querySelector("[data-state]");
  var nav = root.querySelector("[data-nav]");
  var current = null;

  function say(text, cls) {
    if (!stateEl) return;
    stateEl.textContent = text;
    stateEl.className = "pv-hint " + (cls || "");
  }

  // ---------------------------------------------------------------- jump to a page
  // The PDF viewer ignores a changed #page on a page it already shows, so each jump opens the PDF afresh in a
  // new frame (cached after the first time) and swaps it in once it is ready: no blank flash, the old page stays until then.
  function show(page, top) {
    var mine = ++serial;
    var next = document.createElement("iframe");
    next.className = "rbv-frame is-next";
    next.setAttribute("data-frame", "");
    next.title = frame.title;
    next.addEventListener("load", function () {
      if (mine !== serial) { next.remove(); return; }
      setTimeout(function () {
        if (mine !== serial) { next.remove(); return; }
        next.classList.remove("is-next");
        if (frame && frame.parentNode) frame.remove();
        frame = next;
        if (loading) loading.hidden = true;
      }, 350);
    });
    next.src = base + "#page=" + page + "&view=FitH," + (top || 0) + "&navpanes=0";
    if (loading) loading.hidden = false;
    stage.appendChild(next);
  }
  function go(page, link) {
    if (!page) { say("This one was not found in the PDF.", "warn"); return; }
    show(page, link ? parseInt(link.getAttribute("data-top"), 10) || 0 : 0);
    if (current) { current.classList.remove("on"); current.removeAttribute("aria-current"); }
    if (link && link.tagName === "A") { current = link; link.classList.add("on"); link.setAttribute("aria-current", "true"); }
    if (here && link) here.textContent = (link.getAttribute("data-label") || link.textContent || "").replace(/\s+/g, " ").trim() || "";
    say("Page " + page + ". Scroll down to read on.", "");
  }
  root.addEventListener("click", function (ev) {
    var a = ev.target.closest && ev.target.closest("a[data-page]");
    if (a && root.contains(a)) {
      if (ev.ctrlKey || ev.metaKey || ev.shiftKey) return;
      ev.preventDefault();
      go(parseInt(a.getAttribute("data-page"), 10) || 0, a);
      return;
    }
    var sum = ev.target.closest && ev.target.closest("summary[data-page]");
    if (sum && sum.getAttribute("data-page")) {                                                              // the step opens and the PDF goes to it
      show(parseInt(sum.getAttribute("data-page"), 10), parseInt(sum.getAttribute("data-top"), 10) || 0);
      say("Page " + sum.getAttribute("data-page") + ". Scroll down to read on.", "");
    }
    var car = ev.target.closest && ev.target.closest("[data-toggle-subs]");
    if (car) {
      var subs = car.closest(".rv-sec").querySelector(".rv-subs");
      var open = subs.hidden;
      subs.hidden = !open;
      car.setAttribute("aria-expanded", open ? "true" : "false");
    }
  });

  // ---------------------------------------------------------------- the sections list: hide, find, fold
  root.addEventListener("click", function (ev) {
    var t = ev.target.closest && ev.target.closest("[data-nav-toggle]");
    if (t) root.classList.toggle("nav-closed");
  });
  var filter = root.querySelector("[data-nav-filter]");
  var nofind = root.querySelector("[data-nofind]");
  function applyFilter() {
    var q = (filter.value || "").trim().toLowerCase();
    var any = false;
    root.querySelectorAll(".rv-group").forEach(function (g) {
      var hits = 0;
      g.querySelectorAll(".rv-sec").forEach(function (s) {
        var text = (s.textContent || "").toLowerCase();
        var ok = !q || text.indexOf(q) >= 0;
        s.classList.toggle("f-hide", !ok);
        if (ok) hits += 1;
        if (q && ok) {                                      // show the parts that match
          var subs = s.querySelector(".rv-subs");
          if (subs && s.querySelectorAll(".rv-sub-row").length > 1) {
            subs.hidden = false;
            var c = s.querySelector("[data-toggle-subs]");
            if (c) c.setAttribute("aria-expanded", "true");
            subs.querySelectorAll(".rv-sub-row").forEach(function (r) { r.classList.toggle("f-hide", (r.textContent || "").toLowerCase().indexOf(q) < 0 && (s.querySelector(".rv-sec-title").textContent || "").toLowerCase().indexOf(q) < 0); });
          }
        } else {
          s.querySelectorAll(".rv-sub-row").forEach(function (r) { r.classList.remove("f-hide"); });
        }
      });
      g.classList.toggle("f-hide", !!q && !hits);
      if (q && hits) g.open = true;
      if (hits) any = true;
    });
    if (nofind) nofind.hidden = !q || any;
  }
  if (filter) filter.addEventListener("input", applyFilter);
  root.addEventListener("click", function (ev) {
    var fold = ev.target.closest && ev.target.closest("[data-nav-collapse]");
    var open = ev.target.closest && ev.target.closest("[data-nav-expand]");
    if (!fold && !open) return;
    var scope = fold ? root.querySelectorAll(".rv-sec") : root.querySelectorAll(".rv-group[open] .rv-sec");
    scope.forEach(function (s) {
      var subs = s.querySelector(".rv-subs"), car = s.querySelector("[data-toggle-subs]");
      if (!subs || !car || car.hidden) return;
      subs.hidden = !!fold;
      car.setAttribute("aria-expanded", fold ? "false" : "true");
    });
    if (fold) root.querySelectorAll(".rv-group").forEach(function (g) { g.open = false; });
  });
  var skip = root.querySelector("[data-skip]");
  if (skip) skip.addEventListener("click", function (ev) { ev.preventDefault(); frame.focus(); });

  // open the step and part the PDF starts at
  var start = null;
  var first = parseInt((frame.getAttribute("src") || "").replace(/^.*#page=(\d+).*$/, "$1"), 10) || 1;
  var best = -1;
  root.querySelectorAll("a[data-page]").forEach(function (a) {
    var p = parseInt(a.getAttribute("data-page"), 10) || 0;
    if (p && p <= first && p >= best) { best = p; start = a; }
  });
  if (start) {
    var g = start.closest(".rv-group"), s = start.closest(".rv-sec");
    if (g) g.open = true;
    if (s && start.classList.contains("rv-sub")) {
      var sb = s.querySelector(".rv-subs"), c = s.querySelector("[data-toggle-subs]");
      if (sb && c && !c.hidden) { sb.hidden = false; c.setAttribute("aria-expanded", "true"); }
    }
    start.classList.add("on");
    start.setAttribute("aria-current", "true");
    current = start;
    if (here) here.textContent = (start.getAttribute("data-label") || "").trim() || here.textContent;
    setTimeout(function () { try { start.scrollIntoView({ block: "center" }); } catch (e) { /* ignore */ } }, 0);
  } else {
    var g0 = root.querySelector(".rv-group");
    if (g0) g0.open = true;
  }

  // ---------------------------------------------------------------- trainers mark actions done
  var tickBase = root.getAttribute("data-tick");
  function recount() {
    var total = 0, done = 0;
    root.querySelectorAll(".rv-group").forEach(function (g) {
      var gt = 0, gd = 0;
      g.querySelectorAll(".rv-sec").forEach(function (s) {
        var t = s.querySelectorAll(".rv-sub-row").length;
        var d = s.querySelectorAll(".rv-sub-row .rv-check.on").length;
        gt += t; gd += d;
        var cnt = s.querySelector(".rv-sec-row [data-count]");
        if (cnt) cnt.textContent = d + "/" + t;
        s.classList.toggle("done", t > 0 && d >= t);
        var tick = s.querySelector(".rv-sec-row .rv-tick");
        if (tick) tick.hidden = !(t > 0 && d >= t);
      });
      var gc = g.querySelector("summary [data-count]");
      if (gc) gc.textContent = gd + "/" + gt;
      g.classList.toggle("done", gt > 0 && gd >= gt);
      total += gt; done += gd;
    });
    var tt = root.querySelector("[data-total-text]"), tb = root.querySelector("[data-total-bar]");
    if (tt) tt.textContent = done + "/" + total;
    if (tb) tb.style.width = (total ? Math.round(100 * done / total) : 0) + "%";
  }
  root.addEventListener("click", function (ev) {
    var b = ev.target.closest && ev.target.closest("[data-check]");
    if (!b || !tickBase || !window.fetch) return;
    var id = b.getAttribute("data-check");
    var on = b.getAttribute("aria-pressed") === "true";
    b.disabled = true;
    say(on ? "Un-marking…" : "Saving…", "");
    fetch(tickBase + "/i/" + id + "/" + (on ? "untick" : "tick"), {
      method: "POST", credentials: "same-origin",
      headers: { "accept": "application/json", "x-requested-with": "fetch", "content-type": "application/x-www-form-urlencoded" },
      body: "csrf=" + encodeURIComponent(root.getAttribute("data-csrf"))
    }).then(function (r) {
      return r.json().then(function (j) { return { ok: r.ok, j: j }; }, function () { return { ok: r.ok, j: {} }; });
    }).then(function (res) {
      b.disabled = false;
      if (!res.ok) { say("Not saved: " + ((res.j.error && res.j.error.message) || "refused"), "bad"); return; }
      var now = res.j.state === "done";
      b.classList.toggle("on", now);
      b.setAttribute("aria-pressed", now ? "true" : "false");
      b.title = now ? "Done (press to un-mark)" : "Mark done";
      var link = b.parentNode.querySelector("a.rv-sub");
      if (link) link.classList.toggle("done", now);
      recount();
      say(now ? "Marked done." : "Un-marked.", "ok");
    }).catch(function () { b.disabled = false; say("Not saved. Check the connection and try again.", "bad"); });
  });
})();
