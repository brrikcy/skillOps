// Page script (moved out of the HTML so the page needs no inline script).
Robomy.frame("/status");
async function load() {
  var r = await Robomy.call("GET", "/v1/status");
  var out = document.getElementById("out");
  if (r.status !== 200) { out.innerHTML = '<div class="msg bad">' + Robomy.esc(Robomy.errText(r.data)) + "</div>"; return; }
  var d = r.data, e = Robomy.esc;
  var html = '<div class="grid"><div class="card"><div class="stat-label">API</div><div class="stat">Working</div>' +
    '<div class="hint">Server time ' + e(Robomy.localTime(d.server_time)) + "</div></div>" +
    '<div class="card"><div class="stat-label">Active learners (last hour)</div><div class="stat">' + e(d.active_learners_last_hour) + "</div></div></div>";
  if (!d.feeds.length) html += '<div class="card">No feed is open at the moment.</div>';
  d.feeds.forEach(function (f) {
    html += '<div class="card"><h3>Feed ' + e(f.feed) + ' <span class="pill ' + e(f.status) + '">' + e(f.status) + "</span></h3>" +
      "<p>Current data time: <strong>" + e(f.current_data_time || "not started yet") + "</strong></p>" +
      '<div class="table-wrap"><table><tr><th>Dataset</th><th>Rows released so far</th></tr>' +
      f.datasets.map(function (x) { return "<tr><td><code>" + e(x.dataset) + '</code></td><td class="num">' + Number(x.rows_released).toLocaleString("en-IN") + "</td></tr>"; }).join("") +
      "</table></div></div>";
  });
  out.innerHTML = html;
}
load();
setInterval(load, 30000);
