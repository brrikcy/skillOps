// Page script (moved out of the HTML so the page needs no inline script).
Robomy.frame("/docs");
document.querySelectorAll(".sample").forEach(function (box) {
  box.querySelectorAll(".tabs button").forEach(function (b) {
    b.addEventListener("click", function () {
      box.querySelectorAll(".tabs button").forEach(function (x) { x.classList.toggle("on", x === b); });
      box.querySelectorAll("pre[data-t]").forEach(function (p) { p.classList.toggle("hidden", p.dataset.t !== b.dataset.t); });
    });
  });
});
(function () {
  var ep = document.getElementById("t-ep"), out = document.getElementById("t-out");
  var ds = document.getElementById("t-ds");
  ds.value = Robomy.cfg.practiceDataset || "practice_orders";
  var last = null;
  function show() {
    var f = ep.value === "/v1/fetch";
    document.getElementById("t-fetch").classList.toggle("hidden", !f);
    document.getElementById("t-tok").classList.toggle("hidden", !f);
  }
  ep.addEventListener("change", show); show();
  async function send() {
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
    document.getElementById("t-url").textContent = "GET " + Robomy.api + url;
    out.textContent = "Sending...";
    var r = await Robomy.call("GET", url, { headers: key ? { "x-api-key": key } : {} });
    var pretty;
    try { pretty = JSON.stringify(JSON.parse(r.text), null, 2); } catch (e) { pretty = r.text || JSON.stringify(r.data, null, 2); }
    out.textContent = "HTTP " + r.status + "\n\n" + pretty;
    last = r.data;
    document.getElementById("t-next").classList.toggle("hidden", !(last && last.next_page_token));
    document.getElementById("t-cont").classList.toggle("hidden", !(last && last.until && !last.next_page_token));
  }
  document.getElementById("t-send").addEventListener("click", send);
  document.getElementById("t-next").addEventListener("click", function () {
    document.getElementById("t-page").value = last.next_page_token; send();
  });
  document.getElementById("t-cont").addEventListener("click", function () {
    document.getElementById("t-page").value = ""; document.getElementById("t-since").value = last.until; send();
  });
})();
