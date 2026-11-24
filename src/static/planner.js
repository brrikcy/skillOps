// SkillOps session planner: drag parts of the runbook (above) into the timeline (below), in any order.
// The page works without this file (every + and every arrow is a normal form). This file adds:
// drag and drop, reordering by dragging, saving when asked (never by itself), and turning the runbook's pages.
(function () {
  "use strict";
  var dataEl = document.getElementById("plan-data");
  if (!dataEl) return;
  var S = JSON.parse(dataEl.textContent);
  var list = document.querySelector("[data-plan-list]");
  var empty = document.querySelector("[data-plan-empty]");
  var stateEl = document.querySelector("[data-plan-state]");
  var metaEl = document.querySelector("[data-plan-meta]");
  var pages = document.querySelector("[data-pages]");
  var MIME = "application/x-skillops-plan";
  var saveTimer = null, saving = false, again = false, attempt = 0;

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }
  function setState(text, kind) {
    if (!stateEl) return;
    stateEl.textContent = text;
    stateEl.className = "sp-state " + (kind || "");
  }

  // ------------------------------------------------------------ the plan list
  function render() {
    if (!list) { markSources(); return; }      // the runbook on its own (no planner beside it)
    list.innerHTML = "";
    S.entries.forEach(function (e, i) {
      var li = el("li", "sp-entry" + (e.kind === "text" ? " is-text" : "") + (e.missing ? " is-missing" : ""));
      li.setAttribute("data-i", i);
      if (e.missing) li.title = "This part is no longer in the runbook this batch uses. Saving takes it out of the plan.";
      if (e.kind !== "text") li.style.setProperty("--act", "#" + (e.fill || "3b404a"));
      if (!S.readonly) { li.draggable = true; li.appendChild(el("span", "grip", "⋮⋮")); }
      if (e.kind === "text") {
        li.appendChild(el("span", "sp-text", e.text));
      } else {
        if (e.label) {
          var b = el("span", "act-badge", e.label);
          b.style.background = "#" + (e.fill || "3b404a");
          li.appendChild(b);
        }
        var t = el("span", "sp-title");
        var a = el("a", "", e.title);
        a.href = "/b/" + S.batch + "/p/" + e.proj + "/teach/" + e.node;
        t.appendChild(a);
        t.appendChild(el("span", "muted small", e.missing ? "No longer in the runbook" : (e.project_name ? e.project_name + " · " : "") + (e.path || "")));
        li.appendChild(t);
      }
      if (!S.readonly) {
        var btns = el("span", "sp-btns");
        [["up", "←", "Move earlier"], ["down", "→", "Move later"], ["remove", "×", "Remove"]].forEach(function (x) {
          var btn = el("button", "btn sm ghost", x[1]);
          btn.type = "button";
          btn.title = x[2];
          btn.setAttribute("aria-label", x[2] + ": " + (e.title || e.text));
          btn.setAttribute("data-act", x[0]);
          btns.appendChild(btn);
        });
        li.appendChild(btns);
      }
      list.appendChild(li);
    });
    if (empty) empty.hidden = S.entries.length > 0;
    var teach = document.querySelector("[data-plan-teach]");
    if (teach) teach.hidden = S.entries.length === 0;
    var pdfBtn = document.querySelector("[data-plan-pdf]");
    if (pdfBtn) pdfBtn.hidden = S.entries.length === 0;
    markSources();
  }

  function markSources() {
    var inPlan = {};
    S.entries.forEach(function (e) { if (e.kind !== "text") inPlan[e.proj + "|" + e.node] = true; });
    function parentOf(el) {
      var p = el.getAttribute("data-parent");
      if (p) return p;
      var part = el.closest && el.closest(".pv-part");
      return el.classList.contains("sheet-act") && part ? part.getAttribute("data-part") : "";
    }
    document.querySelectorAll("[data-src]").forEach(function (s) {
      var proj = s.getAttribute("data-proj");
      var on = !!inPlan[proj + "|" + s.getAttribute("data-node")];
      var par = parentOf(s);
      var via = !on && !!par && !!inPlan[proj + "|" + par];          // its whole section is in the plan
      s.classList.toggle("in-plan", on || via);
      s.classList.toggle("via-section", via);
      s.classList.remove("part-plan");
      var m = s.querySelector(".in-plan-mark");
      if (m) m.hidden = !(on || via);
    });
    // a section in the list: full when it, or every one of its parts, is in; partly when some parts are
    document.querySelectorAll(".rv-sec-row[data-src]").forEach(function (row) {
      var proj = row.getAttribute("data-proj"), id = row.getAttribute("data-node");
      if (inPlan[proj + "|" + id]) return;
      var kids = row.closest(".rv-sec").querySelectorAll(".rv-sub-row[data-src]");
      var n = 0;
      kids.forEach(function (k) { if (inPlan[proj + "|" + k.getAttribute("data-node")]) n += 1; });
      if (kids.length && n === kids.length) row.classList.add("in-plan");
      else if (n > 0) row.classList.add("part-plan");
    });
    document.querySelectorAll("[data-nav-add]").forEach(function (b) {      // say it in words too, not only in colour
      var row = b.closest("[data-src]");
      b.setAttribute("aria-pressed", !row ? "false" : row.classList.contains("part-plan") ? "mixed" : row.classList.contains("in-plan") ? "true" : "false");
    });
  }

  function fromSource(s) {
    return { kind: "item", proj: s.getAttribute("data-proj"), node: s.getAttribute("data-node"),
             title: s.getAttribute("data-title"), label: s.getAttribute("data-label"),
             fill: s.getAttribute("data-fill"), path: s.getAttribute("data-path"), parent: parentOfSource(s) };
  }
  function parentOfSource(s) {                      // the section a part belongs to ("" for a section)
    var p = s.getAttribute("data-parent");
    if (p) return p;
    var part = s.closest && s.closest(".pv-part");
    return s.classList && s.classList.contains("sheet-act") && part ? part.getAttribute("data-part") : "";
  }
  // the parts of a section, as the sections list knows them
  function navKids(proj, sectionId) {
    var row = document.querySelector('.rv-sec-row[data-node="' + sectionId + '"]');
    var sec = row && row.closest(".rv-sec");
    return sec ? Array.prototype.slice.call(sec.querySelectorAll(".rv-sub-row[data-src]")).map(function (k) { return k.getAttribute("data-node"); }) : [];
  }
  // Can this go in the plan? "" when yes, else the reason. A part whose whole section is in is already covered.
  function canAdd(d, moving) {
    if (indexOf(d) >= 0) return moving ? "" : "Already in the plan";
    if (d.parent && indexOf({ proj: d.proj, node: d.parent }) >= 0) return "Already in the plan, with its whole section";
    return "";
  }
  // Put it in (at `at`, or at the end). A section takes the place of any of its parts that were in on their own.
  function addEntry(d, at) {
    var i = indexOf(d);
    if (i >= 0) {                                    // already in: move it to where it was dropped
      S.entries.splice(i, 1);
      if (at !== undefined && i < at) at -= 1;
    } else if (!d.parent) {
      var first = -1;
      navKids(d.proj, d.node).forEach(function (id) {
        var j = indexOf({ proj: d.proj, node: id });
        if (j >= 0) {
          if (first < 0 || j < first) first = j;
          S.entries.splice(j, 1);
          if (at !== undefined && j < at) at -= 1;
        }
      });
      if (at === undefined && first >= 0) at = first;
    }
    if (at === undefined || at > S.entries.length) at = S.entries.length;
    S.entries.splice(at, 0, d);
  }

  function indexOf(e) {
    for (var i = 0; i < S.entries.length; i++) {
      if (S.entries[i].kind !== "text" && S.entries[i].proj === e.proj && S.entries[i].node === e.node) return i;
    }
    return -1;
  }

  function change(fn) {
    var before = S.entries.length;
    fn();
    render();
    if (list && S.entries.length > before) list.scrollLeft = list.scrollWidth;
    markDirty();
  }

  // ------------------------------------------------------------ saving (only when the trainer asks)
  var dirty = false, ver = 0, waiters = [];
  var saveBtn = document.querySelector("[data-plan-save]"), discardBtn = document.querySelector("[data-plan-discard]");
  function showState() {
    if (saving) return;
    if (dirty) setState("Unsaved changes", "warn");
    else if (stateEl && /Unsaved/.test(stateEl.textContent)) setState("", "");
  }
  function updateButtons() {
    if (saveBtn) saveBtn.disabled = !dirty || saving;
    if (discardBtn) discardBtn.hidden = !dirty;
  }
  function markDirty() { dirty = true; ver += 1; updateButtons(); showState(); }
  function note(msg) { setState(msg, ""); setTimeout(showState, 2200); }
  var gone = false;
  function save() {
    return new Promise(function (resolve) {
      function go() { waiters.push(resolve); if (!saving) run(); }
      var missing = S.entries.filter(function (e) { return e.missing; }).length;
      if (missing && !gone) {
        ask(missing + (missing === 1 ? " planned part is" : " planned parts are") + " no longer in this batch's runbook. Saving takes " + (missing === 1 ? "it" : "them") + " out of the plan.",
            [["Save without " + (missing === 1 ? "it" : "them"), "save", "primary"], ["Not now", "stay", ""]], "Parts that are gone").then(function (v) {
          if (v === "save") { gone = true; go(); } else resolve(false);
        });
      } else go();
    });
  }
  function finish(ok) {
    saving = false;
    updateButtons();
    var w = waiters; waiters = [];
    w.forEach(function (r) { r(ok); });
  }
  function run() {
    saving = true;
    updateButtons();
    setState("Saving…");
    var mine = ver;
    var noteEl = document.querySelector("[data-plan-note]");
    var body = { csrf: S.csrf, date: S.date, rev: S.rev, note: noteEl ? noteEl.value : S.note,
                 entries: S.entries.map(function (e) {
                   return e.kind === "text" ? { kind: "text", text: e.text } : { kind: "item", proj: e.proj, node: e.node };
                 }) };
    fetch(S.save, { method: "POST", credentials: "same-origin", body: JSON.stringify(body),
                    headers: { "content-type": "application/json", "accept": "application/json", "x-requested-with": "fetch" } })
      .then(function (r) {
        if (r.status === 409) throw { conflict: true };
        if (r.status === 401) { throw { stop: "Not saved: your sign-in has ended. Sign in again in another tab, then press Save plan.", retry: false }; }
        if (r.status >= 400 && r.status < 500) {
          return r.json().then(function (j) { throw { stop: "Not saved: " + ((j.error && j.error.message) || "refused") }; });
        }
        if (!r.ok) throw { retry: true };
        return r.json();
      })
      .then(function (j) {
        attempt = 0;
        S.rev = j.rev;
        gone = false;
        if (ver === mine) {            // nothing changed while saving: take the server's copy (titles straight from the runbook)
          S.entries = j.entries;
          dirty = false;
          render();
          var d = j.dropped || {}, bits = [];
          if (d.missing) bits.push(d.missing + (d.missing === 1 ? " part that is no longer in the runbook was removed" : " parts that are no longer in the runbook were removed"));
          if (d.overlap) bits.push(d.overlap + (d.overlap === 1 ? " part was left out because its whole section is in the plan" : " parts were left out because their whole section is in the plan"));
          setState(bits.length ? "Saved. " + bits.join("; ") + "." : "Saved", "ok");
        } else { setState("Unsaved changes", "warn"); }
        if (metaEl) metaEl.textContent = "Saved just now";
        finish(true);
      })
      .catch(function (err) {
        if (err && err.conflict) { finish(false); settleConflict(); return; }
        if (err && err.stop) { setState(err.stop, "bad"); finish(false); return; }
        attempt += 1;
        if (attempt > 3) { setState("Not saved: the connection failed. Press Save plan to try again.", "bad"); attempt = 0; finish(false); return; }
        setState("Not saved, retrying…", "bad");
        setTimeout(run, 1200 * attempt);
      });
  }
  // someone saved this date first: show what they saved and let the trainer choose
  function settleConflict() {
    setState("Someone else saved this plan first…", "bad");
    fetch(S.save + "/current?date=" + encodeURIComponent(S.date), { credentials: "same-origin", headers: { "accept": "application/json", "x-requested-with": "fetch" } })
      .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
      .then(function (theirs) {
        var who = theirs.by_name || "Another trainer";
        var n = (theirs.entries || []).length;
        return ask(who + " saved this plan (" + n + (n === 1 ? " entry" : " entries") + ") while you were editing it. What should happen to your changes?",
                   [["Merge: add what they have to mine", "merge", "primary"], ["Keep mine (replaces theirs)", "mine", "danger"], ["Use theirs (drop my changes)", "theirs", ""], ["Decide later", "stay", ""]],
                   "This plan was changed by someone else").then(function (v) {
          var noteEl = document.querySelector("[data-plan-note]");
          if (v === "mine") { S.rev = theirs.rev; save(); }
          else if (v === "theirs") {
            S.rev = theirs.rev; S.entries = theirs.entries || []; if (noteEl) noteEl.value = theirs.note || "";
            dirty = false; ver += 1; render(); updateButtons(); setState("Loaded their plan", "ok");
          } else if (v === "merge") {
            var have = {};
            S.entries.forEach(function (e) { have[e.kind === "text" ? "t|" + e.text : e.proj + "|" + e.node] = true; });
            var extra = (theirs.entries || []).filter(function (e) { return !have[e.kind === "text" ? "t|" + e.text : e.proj + "|" + e.node]; });
            S.rev = theirs.rev; S.entries = S.entries.concat(extra);
            if (noteEl && !noteEl.value && theirs.note) noteEl.value = theirs.note;
            render(); markDirty();
            note("Merged " + extra.length + (extra.length === 1 ? " entry" : " entries") + " from " + who + ". Check the order, then press Save plan.");
          } else { setState("Not saved: " + who + " changed this plan. Press Save plan to choose.", "bad"); }
        });
      })
      .catch(function () { setState("Not saved: someone else changed this plan. Reload the page to see theirs.", "bad"); });
  }
  if (saveBtn) saveBtn.addEventListener("click", function () { save(); });
  if (discardBtn) discardBtn.addEventListener("click", function () {
    ask("Discard the changes made since the last save?", [["Discard changes", "discard", "danger"], ["Keep editing", "stay", ""]]).then(function (v) {
      if (v === "discard") { dirty = false; window.location.reload(); }
    });
  });

  // a three-way question (our own dialog, in app.js)
  function ask(message, buttons, title) {
    if (window.soAsk) return window.soAsk({ title: title || "Unsaved changes", message: message, buttons: buttons.map(function (b) { return { label: b[0], value: b[1], kind: b[2] }; }), cancelValue: "stay" });
    return Promise.resolve(window.confirm(message) ? buttons[0][1] : "stay");
  }
  // leaving with unsaved changes: save, discard, or stay (links and the date pickers go through here)
  function guard(go) {
    if (!dirty) { go(); return; }
    ask("This plan has changes that are not saved yet.", [["Save and continue", "save", "primary"], ["Discard changes", "discard", "danger"], ["Keep editing", "stay", ""]]).then(function (v) {
      if (v === "save") save().then(function (ok) { if (ok) go(); });
      else if (v === "discard") { dirty = false; go(); }
    });
  }
  window.__soPlanner = {
    dirty: function () { return dirty; },
    save: save,
    discard: function () { dirty = false; }
  };
  window.addEventListener("beforeunload", function (ev) {       // closing the tab or reloading (the browser words this one)
    if (!dirty) return;
    ev.preventDefault();
    ev.returnValue = "";
  });
  document.addEventListener("click", function (ev) {
    var a = ev.target.closest && ev.target.closest("a[href]");
    if (!a || ev.defaultPrevented || a.target === "_blank" || ev.ctrlKey || ev.metaKey || ev.shiftKey || ev.button !== 0) return;
    if (a.hasAttribute("data-plan-pdf")) {           // a download: the page stays, but the PDF holds only what is saved
      if (!dirty) return;
      ev.preventDefault();
      ev.stopPropagation();
      var fetchPdf = function () { var t = document.createElement("a"); t.href = a.href; t.setAttribute("download", ""); document.body.appendChild(t); t.click(); t.remove(); };
      ask("The PDF shows the saved plan, and this plan has changes that are not saved yet.",
          [["Save, then download", "save", "primary"], ["Download the saved plan", "old", ""], ["Cancel", "stay", ""]], "Download PDF").then(function (v) {
        if (v === "save") save().then(function (ok) { if (ok) fetchPdf(); });
        else if (v === "old") fetchPdf();
      });
      return;
    }
    if (a.hasAttribute("data-goto") || a.hasAttribute("data-popup") || a.getAttribute("href").charAt(0) === "#") return;
    if (!dirty) return;
    ev.preventDefault();
    ev.stopPropagation();
    guard(function () { window.location.href = a.href; });
  }, true);
  document.addEventListener("change", function (ev) {            // another date or plan
    var t = ev.target;
    if (!dirty || !t.matches || !t.matches("[data-autosubmit]") || !t.form) return;
    ev.stopPropagation();
    var form = t.form, prev = t.defaultValue !== undefined && t.tagName === "INPUT" ? t.defaultValue : null;
    if (t.tagName === "SELECT") { for (var i = 0; i < t.options.length; i++) if (t.options[i].defaultSelected) prev = t.options[i].value; }
    guard(function () { form.submit(); });
    setTimeout(function () { if (dirty && prev !== null) t.value = prev; }, 0);
  }, true);

  // ------------------------------------------------------------ drag and drop
  var line = el("li", "sp-dropline");
  function dropIndex(x) {
    var items = list.querySelectorAll("li.sp-entry");
    for (var i = 0; i < items.length; i++) {
      var r = items[i].getBoundingClientRect();
      if (x < r.left + r.width / 2) return i;
    }
    return items.length;
  }
  function showLine(idx) {
    var items = list.querySelectorAll("li.sp-entry");
    if (idx >= items.length) list.appendChild(line); else list.insertBefore(line, items[idx]);
  }
  function hideLine() { if (line.parentNode) line.parentNode.removeChild(line); }

  if (!S.readonly && list) {
    document.addEventListener("dragstart", function (ev) {
      var src = ev.target.closest && ev.target.closest("[data-src]");
      var entry = ev.target.closest && ev.target.closest("li.sp-entry");
      if (src) {
        ev.dataTransfer.setData(MIME, JSON.stringify({ add: fromSource(src) }));
        ev.dataTransfer.setData("text/plain", src.getAttribute("data-title"));
        ev.dataTransfer.effectAllowed = "copy";
        src.classList.add("dragging");
      } else if (entry) {
        ev.dataTransfer.setData(MIME, JSON.stringify({ move: parseInt(entry.getAttribute("data-i"), 10) }));
        ev.dataTransfer.setData("text/plain", "move");
        ev.dataTransfer.effectAllowed = "move";
        entry.classList.add("dragging");
      }
    });
    document.addEventListener("dragend", function (ev) {
      if (ev.target.classList) ev.target.classList.remove("dragging");
      hideLine();
      document.querySelector(".sp").classList.remove("drop-on");
    });
    var zone = document.querySelector(".sp");
    zone.addEventListener("dragover", function (ev) {
      if (Array.prototype.slice.call(ev.dataTransfer.types || []).indexOf(MIME) < 0) return;
      ev.preventDefault();
      zone.classList.add("drop-on");
      showLine(dropIndex(ev.clientX));
      var tr = list.getBoundingClientRect();                      // near an end of the timeline: keep it moving
      if (ev.clientX > tr.right - 60) list.scrollLeft += 18;
      else if (ev.clientX < tr.left + 60) list.scrollLeft -= 18;
    });
    zone.addEventListener("dragleave", function (ev) {
      if (!zone.contains(ev.relatedTarget)) { zone.classList.remove("drop-on"); hideLine(); }
    });
    zone.addEventListener("drop", function (ev) {
      var raw = ev.dataTransfer.getData(MIME);
      if (!raw) return;
      ev.preventDefault();
      var idx = dropIndex(ev.clientX);
      hideLine();
      zone.classList.remove("drop-on");
      var d = JSON.parse(raw);
      if (d.add) {
        var why = canAdd(d.add, true);
        if (why) { note(why); return; }
      }
      change(function () {
        if (d.add) {
          addEntry(d.add, idx);
        } else if (typeof d.move === "number") {
          var moved = S.entries.splice(d.move, 1)[0];
          if (d.move < idx) idx -= 1;
          S.entries.splice(idx, 0, moved);
        }
      });
    });

    // buttons in the list
    list.addEventListener("click", function (ev) {
      var b = ev.target.closest("button[data-act]");
      if (!b) return;
      var i = parseInt(b.closest("li").getAttribute("data-i"), 10);
      var act = b.getAttribute("data-act");
      change(function () {
        if (act === "remove") S.entries.splice(i, 1);
        else {
          var to = act === "up" ? i - 1 : i + 1;
          if (to >= 0 && to < S.entries.length) { var x = S.entries[i]; S.entries[i] = S.entries[to]; S.entries[to] = x; }
        }
      });
    });

    // "+" on a section or a part in the sections list: press once to add, again to take it out
    function kidsOf(sectionEl) {
      return Array.prototype.slice.call(sectionEl.closest(".rv-sec").querySelectorAll(".rv-sub-row[data-src]"));
    }
    function splice(at, remove, items) { Array.prototype.splice.apply(S.entries, [at, remove].concat(items)); }
    document.addEventListener("click", function (ev) {
      var b = ev.target.closest && ev.target.closest("[data-nav-add]");
      if (!b) return;
      ev.preventDefault();
      var src = b.closest("[data-src]");
      if (!src) return;
      var d = fromSource(src), parent = src.getAttribute("data-parent");
      change(function () {
        var i = indexOf(d);
        if (parent) {                                                  // a part
          if (i >= 0) { S.entries.splice(i, 1); return; }               // it is in on its own: take it out
          var pi = indexOf({ proj: d.proj, node: parent });
          if (pi >= 0) {                                                // its whole section is in: keep the other parts, drop this one
            var rest = kidsOf(src).filter(function (k) { return k !== src; }).map(fromSource);
            splice(pi, 1, rest);
          } else { S.entries.push(d); }
          return;
        }
        if (i >= 0) { S.entries.splice(i, 1); return; }                 // a section that is in: take it out
        var kids = kidsOf(src), at = -1, found = 0;
        kids.forEach(function (k) {
          var j = indexOf(fromSource(k));
          if (j >= 0) { found += 1; if (at < 0 || j < at) at = j; }
        });
        if (found && found === kids.length) {                           // every part is in already: press = take them all out
          kids.forEach(function (k) { var j = indexOf(fromSource(k)); if (j >= 0) S.entries.splice(j, 1); });
          return;
        }
        kids.forEach(function (k) { var j = indexOf(fromSource(k)); if (j >= 0) S.entries.splice(j, 1); });   // some parts: the whole section replaces them
        if (at < 0 || at > S.entries.length) at = S.entries.length;
        splice(at, 0, [d]);
      });
    });

    // "+" on a part of the runbook, and "your own line"
    document.addEventListener("submit", function (ev) {
      var f = ev.target;
      if (f.hasAttribute("data-plan-add")) {
        ev.preventDefault();
        var src = f.closest("[data-src]") || f.parentNode.closest("[data-src]");
        if (!src) { src = f.closest(".sheet-head"); }
        var add = fromSource(src);
        var refused = canAdd(add, false);
        if (refused) { note(refused); return; }
        change(function () { addEntry(add); });
      } else if (f.hasAttribute("data-plan-text")) {
        ev.preventDefault();
        var input = f.querySelector("input[name=text]");
        var text = (input.value || "").trim();
        if (!text) return;
        change(function () { S.entries.push({ kind: "text", text: text.slice(0, 200) }); });
        input.value = "";
      } else if (f.classList.contains("sp-note")) {
        ev.preventDefault();
        save();
      }
    }, true);
    var noteField = document.querySelector("[data-plan-note]");
    if (noteField) noteField.addEventListener("input", function () { markDirty(); });
  }

  // ------------------------------------------------------------ turning the runbook's pages
  // The sections list follows where you are: steps before this one are tucked into one "Earlier steps" row, this step
  // shows its sections (only the open one expanded), and the steps after it stay folded.
  var navList = document.querySelector(".rv-nav-list");
  function focusGroup(group) {
    if (!navList || !group) return;
    var earlier = navList.querySelector("[data-earlier]");
    if (!earlier) return;
    var body = earlier.querySelector(".rv-earlier-body");
    var groups = Array.prototype.slice.call(document.querySelectorAll("[data-nav] details.rv-group"))
      .sort(function (x, y) { return parseInt(x.getAttribute("data-i"), 10) - parseInt(y.getAttribute("data-i"), 10); });
    var cur = parseInt(group.getAttribute("data-i"), 10), done = 0, total = 0, n = 0, anchor = earlier;
    groups.forEach(function (g) {
      var i = parseInt(g.getAttribute("data-i"), 10);
      if (i < cur) {
        body.appendChild(g);
        g.open = false;
        done += parseInt(g.getAttribute("data-done") || "0", 10);
        total += parseInt(g.getAttribute("data-total") || "0", 10);
        n += 1;
      } else {
        anchor.parentNode.insertBefore(g, anchor.nextSibling);
        anchor = g;
        g.open = i === cur;
      }
    });
    earlier.hidden = n === 0;
    var c = earlier.querySelector("[data-earlier-count]");
    if (c) c.textContent = n + (n === 1 ? " step" : " steps") + " \u00b7 " + done + "/" + total;
  }
  function pin() {
    if (!navList || document.querySelector(".rv-nav.rv-filtering")) { if (navList) navList.querySelectorAll(".rv-stuck").forEach(function (e) { e.classList.remove("rv-stuck", "rv-stuck-last"); e.style.top = ""; }); return; }
    navList.querySelectorAll(".rv-stuck").forEach(function (e) { e.classList.remove("rv-stuck", "rv-stuck-last"); e.style.top = ""; });
    var grp = navList.querySelector("details.rv-group[open]");
    if (!grp) return;
    var sum = grp.querySelector(":scope > summary");
    if (!sum) return;
    sum.classList.add("rv-stuck");
    sum.style.top = "0px";
    var off = sum.offsetHeight, last = sum;
    var secs = Array.prototype.slice.call(grp.querySelectorAll(":scope > .rv-sec"));
    var cur = -1;
    secs.forEach(function (sec, i) { if (sec.querySelector("a.on[data-goto]:not([data-at])")) cur = i; });
    var cap = navList.clientHeight * 0.34, keep = [];
    off += 6;                                                    // the list's own padding and the shadow
    for (var i = cur - 1; i >= 0; i--) {                       // the sections just above the one you are in, as many as fit
      var sub = secs[i].querySelector(".rv-subs");
      if (sub && !sub.hidden) continue;                          // an opened section is long: leave it to scroll
      var h = secs[i].getBoundingClientRect().height + 4;
      if (off + h > cap) break;
      off += h;
      keep.unshift(secs[i]);
    }
    var y = sum.offsetHeight;
    keep.forEach(function (sec) {
      sec.classList.add("rv-stuck");
      sec.style.top = y + "px";
      y += sec.offsetHeight + 4;
      last = sec;
    });
    last.classList.add("rv-stuck-last");
  }
  window.addEventListener("resize", pin);
  window.addEventListener("load", pin);

  function markNav(id) {
    document.querySelectorAll("[data-nav] a[data-goto]:not([data-at])").forEach(function (a) {
      var on = a.getAttribute("data-goto") === id;
      a.classList.toggle("on", on);
      if (on) a.setAttribute("aria-current", "true"); else a.removeAttribute("aria-current");
      if (!on) return;
      var thisSec = a.closest(".rv-sec");
      document.querySelectorAll("[data-nav] .rv-sec").forEach(function (sec) {       // only the open section stays expanded
        if (sec === thisSec) return;
        var sub = sec.querySelector(".rv-subs"), car = sec.querySelector(".rv-caret");
        if (sub) sub.hidden = true;
        if (car) car.setAttribute("aria-expanded", "false");
      });
      focusGroup(a.closest("details.rv-group"));
      var subs = thisSec && thisSec.querySelector(".rv-subs"), caret = thisSec && thisSec.querySelector(".rv-caret");
      if (subs && caret && !caret.hidden) { subs.hidden = false; caret.setAttribute("aria-expanded", "true"); }
      a.scrollIntoView({ block: "nearest" });
    });
    pin();
  }
  (function () {
    var here = document.querySelector("[data-nav] a.on[data-goto]:not([data-at])");
    if (here) markNav(here.getAttribute("data-goto"));            // the first look already follows where you are
  })();

  // the part last chosen (by clicking it, or in the sections list): marked in the list and on the page
  var selected = "";
  function showAt(id) {                       // the chosen part is in the address, so a reload or a shared link finds it
    try {
      var u = new URL(location.href);
      if (id) u.searchParams.set("at", id); else u.searchParams.delete("at");
      history.replaceState(null, "", u.toString());
    } catch (e) { /* ignore */ }
  }
  function selectAct(id, scroll) {
    selected = id || "";
    showAt(selected);
    pages.querySelectorAll(".sheet-act.sel, .sheet-head.sel").forEach(function (e) { e.classList.remove("sel"); });
    document.querySelectorAll("[data-nav] .rv-sub.on").forEach(function (a) { a.classList.remove("on"); a.removeAttribute("aria-current"); });
    if (!id) return;
    var el = pages.querySelector('[data-node="' + id + '"]');
    if (el) el.classList.add("sel");
    var row = document.querySelector('[data-nav] .rv-sub[data-at="' + id + '"]');
    if (row) {
      row.classList.add("on");
      row.setAttribute("aria-current", "true");
      var sec = row.closest(".rv-sec"), subs = sec && sec.querySelector(".rv-subs"), caret = sec && sec.querySelector(".rv-caret");
      if (subs && subs.hidden) { subs.hidden = false; if (caret) caret.setAttribute("aria-expanded", "true"); }
      var det = row.closest("details"); if (det) det.open = true;
      if (scroll !== false) row.scrollIntoView({ block: "nearest" });
    }
  }
  pages.addEventListener("click", function (ev) {
    if (ev.target.closest("a, button, input, textarea, select, summary")) return;
    var act = ev.target.closest(".sheet-act[data-node]");
    if (act) selectAct(act.getAttribute("data-node"));
  });
  document.addEventListener("dragstart", function (ev) {
    var act = ev.target.closest && ev.target.closest(".sheet-act[data-node]");
    if (act) selectAct(act.getAttribute("data-node"));
  });
  function scrollToAct(at) {
    var el = pages.querySelector('[data-node="' + at + '"]');
    if (!el) return;
    var r = (el.getClientRects()[0]) || el.getBoundingClientRect(), pr = pages.getBoundingClientRect();
    pages.scrollTo({ left: pages.scrollLeft + r.left - pr.left - 18, top: 0, behavior: "instant" });
    el.classList.add("flash-target");
    setTimeout(function () { el.classList.remove("flash-target"); }, 1600);
    selectAct(at);
  }
  function openPart(id, at) {
    var shown = pages.querySelector(".pv-part");
    if (shown && shown.getAttribute("data-part") === id) {          // already on this section: just move to the part
      if (at) scrollToAct(at); else pages.scrollLeft = 0;
      return;
    }
    var url = pages.getAttribute("data-base") + id + "?date=" + encodeURIComponent(S.date) + "&mode=" + (S.mode || "plan");
    pages.classList.add("loading");
    fetch(url, { credentials: "same-origin" })
      .then(function (r) { if (!r.ok) throw new Error(r.status); return r.text(); })
      .then(function (html) {
        pages.innerHTML = html;
        pages.scrollTop = 0;
        pages.scrollLeft = 0;
        pages.classList.remove("loading");
        markNav(id);
        var u = new URL(location.href);
        u.searchParams.set("part", id);
        u.searchParams.delete("at");
        history.replaceState(null, "", u.toString());
        markSources();
        applyFilter();
        if (at) scrollToAct(at);
        else if (selected && pages.querySelector('[data-node="' + selected + '"]')) selectAct(selected, false);
        else selectAct("");                                         // the part you chose is in another section: let it go
      })
      .catch(function () {
        location.href = "?" + (S.mode === "read" ? "" : "date=" + encodeURIComponent(S.date) + "&") + "part=" + encodeURIComponent(id) + (at ? "&at=" + encodeURIComponent(at) : "");
      });
  }
  // ------------------------------------------------------------ find, fold and skip in the sections list
  var filterEl = document.querySelector("[data-nav-filter]"), nofind = document.querySelector("[data-nofind]");
  function applyFilter() {
    var nav = document.querySelector(".rv-nav");
    if (!filterEl || !nav) return;
    var q = (filterEl.value || "").trim().toLowerCase();
    var secs = document.querySelectorAll(".rv-nav .rv-sec");
    nav.classList.toggle("rv-filtering", !!q);
    if (!q) {
      document.querySelectorAll(".rv-nav .f-hide").forEach(function (e) { e.classList.remove("f-hide"); });
      if (nofind) nofind.hidden = true;
      var here = document.querySelector("[data-nav] a.on[data-goto]:not([data-at])");
      if (here) markNav(here.getAttribute("data-goto"));          // back to following where you are
      return;
    }
    var any = false;
    document.querySelectorAll(".rv-nav details.rv-group").forEach(function (g) {
      var gHit = (g.querySelector(".rv-group-title").textContent || "").toLowerCase().indexOf(q) >= 0, gAny = false;
      g.querySelectorAll(".rv-sec").forEach(function (sec) {
        var t = (sec.querySelector(".rv-sec-title").textContent || "").toLowerCase();
        var rows = sec.querySelectorAll(".rv-sub-row"), hits = 0;
        rows.forEach(function (r) {
          var hit = (r.querySelector(".rv-sub-title").textContent || "").toLowerCase().indexOf(q) >= 0;
          r.classList.toggle("f-hide", !hit && !(t.indexOf(q) >= 0 || gHit));
          if (hit) hits += 1;
        });
        var show = t.indexOf(q) >= 0 || gHit || hits > 0;
        sec.classList.toggle("f-hide", !show);
        var sub = sec.querySelector(".rv-subs"), car = sec.querySelector(".rv-caret");
        var open = hits > 0;
        if (sub) sub.hidden = !open;
        if (car) car.setAttribute("aria-expanded", open ? "true" : "false");
        if (show) gAny = true;
      });
      g.classList.toggle("f-hide", !gAny);
      if (gAny) { g.open = true; any = true; }
    });
    var earlier = document.querySelector("[data-earlier]");
    if (earlier) { var inside = earlier.querySelector(".rv-group:not(.f-hide)"); earlier.classList.toggle("f-hide", !inside && !earlier.hidden); if (inside) earlier.open = true; }
    if (nofind) nofind.hidden = any;
    pin();
  }
  if (filterEl) {
    filterEl.addEventListener("input", applyFilter);
    filterEl.addEventListener("keydown", function (ev) { if (ev.key === "Escape" && filterEl.value) { ev.stopPropagation(); filterEl.value = ""; applyFilter(); } });
  }
  document.addEventListener("click", function (ev) {
    var fold = ev.target.closest && ev.target.closest("[data-nav-collapse]");
    var open = ev.target.closest && ev.target.closest("[data-nav-expand]");
    if (!fold && !open) return;
    var scope = fold ? document.querySelectorAll(".rv-nav .rv-sec") : document.querySelectorAll(".rv-nav details.rv-group[open] .rv-sec");
    scope.forEach(function (sec) {
      var sub = sec.querySelector(".rv-subs"), car = sec.querySelector(".rv-caret");
      if (!sub || (car && car.hidden)) return;
      sub.hidden = !!fold;
      if (car) car.setAttribute("aria-expanded", fold ? "false" : "true");
    });
    pin();
  });
  var skip = document.querySelector("[data-skip]");
  if (skip) skip.addEventListener("click", function (ev) { ev.preventDefault(); pages.focus(); });

  var jump = document.querySelector("[data-jump]");
  if (jump) jump.addEventListener("change", function () { openPart(jump.value); });
  document.addEventListener("click", function (ev) {
    var caret = ev.target.closest && ev.target.closest("[data-toggle-subs]");
    if (caret) {
      var subs = caret.closest(".rv-sec").querySelector(".rv-subs");
      var open = caret.getAttribute("aria-expanded") !== "true";
      caret.setAttribute("aria-expanded", open ? "true" : "false");
      if (subs) subs.hidden = !open;
      pin();
      return;
    }
    var a = ev.target.closest && ev.target.closest("a[data-goto]");
    if (!a || ev.ctrlKey || ev.metaKey) return;
    ev.preventDefault();
    openPart(a.getAttribute("data-goto"), a.getAttribute("data-at"));
  });
  var firstAt = new URL(location.href).searchParams.get("at");     // a link straight to a part of a section
  if (firstAt) window.addEventListener("load", function () { scrollToAct(firstAt); });
  var dateInput = document.querySelector("[data-autosubmit]");
  if (dateInput) dateInput.addEventListener("change", function () { if (dateInput.value) dateInput.form.submit(); });

  // ------------------------------------------------------------ the sections pane: hide or show (remembered)
  var planner = document.querySelector("[data-planner]");
  function setNav(closed) {
    if (!planner) return;
    planner.classList.toggle("nav-closed", closed);
    try { localStorage.setItem("so-nav-closed", closed ? "1" : "0"); } catch (e) { /* private window */ }
  }
  if (window.innerWidth < 1100) {
    if (planner) planner.classList.add("nav-closed");          // small screens: the pane slides over when opened
  } else {
    try { if (localStorage.getItem("so-nav-closed") === "1") setNav(true); } catch (e) { /* ignore */ }
  }
  document.querySelectorAll("[data-nav-toggle]").forEach(function (b) {
    b.addEventListener("click", function () { setNav(!planner.classList.contains("nav-closed")); });
  });

  // the sections pane: its width can be dragged (or set with the arrow keys); the choice is remembered
  (function () {
    var handle = document.querySelector("[data-resize]");
    if (!handle || !planner) return;
    var MIN = 200, MAX = 620, DEF = 300;
    function apply(w) {
      w = Math.max(MIN, Math.min(MAX, Math.round(w)));
      planner.style.setProperty("--nav-w", w + "px");
      handle.setAttribute("aria-valuenow", String(w));
      return w;
    }
    function keep(w) { try { localStorage.setItem("so-nav-w", String(w)); } catch (e) { /* private window */ } }
    var saved = DEF;
    try { saved = parseInt(localStorage.getItem("so-nav-w") || String(DEF), 10) || DEF; } catch (e) { /* ignore */ }
    apply(saved);
    handle.setAttribute("aria-valuemin", String(MIN));
    handle.setAttribute("aria-valuemax", String(MAX));
    handle.addEventListener("pointerdown", function (ev) {
      ev.preventDefault();
      handle.setPointerCapture(ev.pointerId);
      document.documentElement.classList.add("so-resizing");
      var left = planner.getBoundingClientRect().left;
      function move(e) { apply(e.clientX - left); }
      function up(e) {
        handle.removeEventListener("pointermove", move);
        handle.removeEventListener("pointerup", up);
        document.documentElement.classList.remove("so-resizing");
        keep(apply(e.clientX - left));
      }
      handle.addEventListener("pointermove", move);
      handle.addEventListener("pointerup", up);
    });
    handle.addEventListener("keydown", function (ev) {
      var cur = parseInt(handle.getAttribute("aria-valuenow") || String(DEF), 10);
      if (ev.key === "ArrowLeft") { ev.preventDefault(); keep(apply(cur - 20)); }
      else if (ev.key === "ArrowRight") { ev.preventDefault(); keep(apply(cur + 20)); }
      else if (ev.key === "Home") { ev.preventDefault(); keep(apply(MIN)); }
      else if (ev.key === "End") { ev.preventDefault(); keep(apply(MAX)); }
    });
    handle.addEventListener("dblclick", function () { keep(apply(DEF)); });
  })();

  // in its own window, "back" closes the window and returns to the batch page behind it
  var back = document.querySelector("[data-close-popup]");
  if (back && window.opener && !window.opener.closed) {
    back.addEventListener("click", function (ev) { ev.preventDefault(); try { window.opener.focus(); } catch (e) { /* ignore */ } window.close(); });
  }

  render();
})();
