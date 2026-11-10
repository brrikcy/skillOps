// Page script (moved out of the HTML so the page needs no inline script).
Robomy.frame("/register");
(function () {
  var params = new URLSearchParams(location.search);
  if (params.get("code")) document.getElementById("code").value = params.get("code");
  var form = document.getElementById("form");
  form.addEventListener("submit", async function (ev) {
    ev.preventDefault();
    var err = document.getElementById("err");
    var btn = document.getElementById("go");
    err.classList.add("hidden");
    btn.disabled = true; btn.textContent = "Registering...";
    var r = await Robomy.call("POST", "/v1/register", { body: {
      join_code: document.getElementById("code").value.trim(),
      full_name: document.getElementById("name").value.trim(),
      roll_number: document.getElementById("roll").value.trim()
    }});
    btn.disabled = false; btn.textContent = "Register";
    if (r.status !== 201) {
      err.textContent = Robomy.errText(r.data);
      err.classList.remove("hidden");
      return;
    }
    var d = r.data;
    var apiUrl = d.api_url || Robomy.api;
    var envText = "SIMLAB_API_KEY=" + d.api_key + "\nSIMLAB_API_URL=" + apiUrl;
    form.classList.add("hidden");
    document.getElementById("welcome").textContent = d.message + " Name: " + d.student.name +
      ", roll number: " + d.student.roll_number + ", batch: " + d.student.batch + ".";
    document.getElementById("key").textContent = d.api_key;
    document.getElementById("env").textContent = envText;
    if (d.student.status === "pending") document.getElementById("pending").classList.remove("hidden");
    document.getElementById("copyKey").onclick = function () { Robomy.copy(d.api_key, this); };
    document.getElementById("copyEnv").onclick = function () { Robomy.copy(envText + "\n", this); };
    document.getElementById("done").classList.remove("hidden");
    // The key lives only on this page. Leaving the page forgets it.
    window.addEventListener("beforeunload", function (e) { e.preventDefault(); e.returnValue = ""; });
  });
})();
