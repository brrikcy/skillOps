// Developer docs: code-sample tabs and the "Try it" box. The rest of the page is plain HTML.
(function () {
  "use strict";
  document.querySelectorAll(".sample").forEach(function (box) {
    box.querySelectorAll(".code-tabs button").forEach(function (b) {
      b.addEventListener("click", function () {
        box.querySelectorAll(".code-tabs button").forEach(function (x) { x.classList.toggle("on", x === b); });
        box.querySelectorAll("pre[data-t]").forEach(function (p) { p.classList.toggle("hidden", p.getAttribute("data-t") !== b.getAttribute("data-t")); });
      });
    });
  });
  var ep = document.getElementById("t-ep");
  if (!ep) return;
  var out = document.getElementById("t-out"), ds = document.getElementById("t-ds");
  var last = null;
  function show() {
    var f = ep.value === "/v1/fetch";
    document.getElementById("t-fetch").classList.toggle("hidden", !f);
    document.getElementById("t-tok").classList.toggle("hidden", !f);
  }
  ep.addEventListener("change", show); show();
  function send() {
    var key = document.getElementById("t-key").value.trim();
    var path = ep.value, qs = new URLSearchParams();
    if (path === "/v1/fetch") {
      qs.set("dataset", ds.value.trim());
      var tok = document.getElementById("t-page").value.trim();
      if (tok) qs.set("page_token", tok); else qs.set("since", document.getElementById("t-since").value.trim());
      var size = document.getElementById("t-size").value.trim();
      if (size) qs.set("page_size", size);
    }
    var url = path + (qs.toString() ? "?" + qs.toString() : "");
    document.getElementById("t-url").textContent = "GET " + location.origin + url;
    out.textContent = "Sending...";
    fetch(url, { headers: key ? { "x-api-key": key } : {}, credentials: "omit" })
      .then(function (r) { return r.text().then(function (t) { return { status: r.status, text: t }; }); })
      .then(function (r) {
        var data = null, pretty = r.text;
        try { data = JSON.parse(r.text); pretty = JSON.stringify(data, null, 2); } catch (e) { /* not JSON */ }
        out.textContent = "HTTP " + r.status + "\n\n" + pretty;
        last = data;
        document.getElementById("t-next").classList.toggle("hidden", !(last && last.next_page_token));
        document.getElementById("t-cont").classList.toggle("hidden", !(last && last.until && !last.next_page_token));
      })
      .catch(function () { out.textContent = "Could not reach the server. Check your internet."; });
  }
  document.getElementById("t-send").addEventListener("click", send);
  document.getElementById("t-next").addEventListener("click", function () {
    document.getElementById("t-page").value = last.next_page_token; send();
  });
  document.getElementById("t-cont").addEventListener("click", function () {
    document.getElementById("t-page").value = ""; document.getElementById("t-since").value = last.until; send();
  });
})();
