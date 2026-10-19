/* minibox UI. Plain JavaScript, no libraries, no network requests except to our own /api.
 * Everything that came from the server or from a container is put on the page with
 * textContent, never innerHTML, so container output cannot inject markup or script. */
(function () {
  "use strict";

  var POLL_MS = 3000;
  var LOG_LINES = 200;
  var TOKEN_KEY = "miniboxToken";

  var token = loadToken();
  var containers = [];
  var selectedId = null;
  var pending = {};          // "id:action" -> true while a request for it is running
  var polling = false;

  function $(id) { return document.getElementById(id); }

  // ---- token: read from ?token=, keep in sessionStorage, remove from the address bar ----
  function loadToken() {
    var fromUrl = new URLSearchParams(window.location.search).get("token");
    if (fromUrl) {
      try { sessionStorage.setItem(TOKEN_KEY, fromUrl); } catch (e) { /* storage blocked */ }
      history.replaceState(null, "", window.location.pathname);
      return fromUrl;
    }
    try { return sessionStorage.getItem(TOKEN_KEY) || ""; } catch (e) { return ""; }
  }

  // ---- API helper: adds the Bearer header and turns error JSON into an Error ----
  function api(method, path, body) {
    var options = { method: method, headers: { "Authorization": "Bearer " + token }, cache: "no-store" };
    if (body !== undefined) {
      options.headers["Content-Type"] = "application/json";
      options.body = JSON.stringify(body);
    }
    return fetch(path, options).then(function (resp) {
      return resp.json().catch(function () { return {}; }).then(function (data) {
        if (resp.ok) { return data; }
        var info = data.error || {};
        var message = info.message || ("request failed (HTTP " + resp.status + ")");
        if (resp.status === 401) {
          message = "Not authorised. Open the URL printed by the server (it contains ?token=...).";
        } else if (info.field) {
          message = info.field + ": " + message;
        }
        var err = new Error(message);
        err.status = resp.status;
        throw err;
      });
    });
  }

  function showError(message) {
    var banner = $("banner");
    banner.textContent = message;
    banner.hidden = false;
  }
  function clearError() { $("banner").hidden = true; }

  // Disable a button while an async action runs.
  function busy(button, work) {
    button.disabled = true;
    return work().then(function (v) { button.disabled = false; return v; },
                       function (e) { button.disabled = false; throw e; });
  }

  // ---- formatting helpers ----
  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) { node.className = className; }
    if (text !== undefined) { node.textContent = text; }
    return node;
  }

  function formatMemory(bytes) {
    if (!bytes) { return ""; }
    var units = [["G", 1073741824], ["M", 1048576], ["K", 1024]];
    for (var i = 0; i < units.length; i++) {
      if (bytes >= units[i][1] && bytes % units[i][1] === 0) { return bytes / units[i][1] + units[i][0]; }
    }
    return bytes + "B";
  }

  function formatAge(epoch) {
    if (!epoch) { return "-"; }
    var secs = Math.max(0, Math.floor(Date.now() / 1000) - epoch);
    if (secs < 60) { return secs + "s ago"; }
    if (secs < 3600) { return Math.floor(secs / 60) + "m ago"; }
    if (secs < 86400) { return Math.floor(secs / 3600) + "h ago"; }
    return Math.floor(secs / 86400) + "d ago";
  }

  function label(c) { return c.name ? c.name + " (" + c.id + ")" : c.id; }

  function findContainer(id) {
    for (var i = 0; i < containers.length; i++) {
      if (containers[i].id === id) { return containers[i]; }
    }
    return null;
  }

  // ---- containers table ----
  function actionButton(text, className, id, action, handler) {
    var button = el("button", className, text);
    button.type = "button";
    button.disabled = !!pending[id + ":" + action];
    button.addEventListener("click", function () {
      pending[id + ":" + action] = true;
      button.disabled = true;
      Promise.resolve(handler()).then(null, function (e) { showError(e.message); })
        .then(function () { delete pending[id + ":" + action]; button.disabled = false; refresh(); });
    });
    return button;
  }

  function renderTable() {
    var rows = $("rows");
    rows.textContent = "";
    $("empty").hidden = containers.length > 0;
    $("containers").hidden = containers.length === 0;

    containers.forEach(function (c) {
      var tr = el("tr", c.id === selectedId ? "selected" : "");
      // 8 cells, in the order of the header in index.html:
      // id, name / command, state, memory, cpu, pids, started, actions
      tr.appendChild(el("td", "mono", c.id));

      var what = el("td");
      what.appendChild(el("div", "name", c.name || "-"));
      var cmd = el("div", "cmd mono", c.command || "");
      cmd.title = c.command || "";
      what.appendChild(cmd);
      tr.appendChild(what);

      // state: plain text with a small coloured square (CSS class), exit code if it has ended
      var state = el("td");
      var text = c.status === "running" || c.exit_code === undefined || c.exit_code === null
        ? String(c.status) : c.status + " (" + c.exit_code + ")";
      state.appendChild(el("span", "state state-" + (/^[a-z]+$/.test(c.status) ? c.status : "other"), text));
      tr.appendChild(state);

      // limits as the API reports them; 0 or missing means "no limit"
      tr.appendChild(el("td", "num mono", c.memory ? formatMemory(c.memory) : "-"));
      tr.appendChild(el("td", "num mono", c.cpus ? String(c.cpus) : "-"));
      tr.appendChild(el("td", "num mono", c.pids ? String(c.pids) : "-"));
      tr.appendChild(el("td", "", formatAge(c.created_at)));

      var actions = el("td");
      var row = el("div", "btn-row");
      row.appendChild(actionButton("logs / exec", "", c.id, "select", function () { select(c.id); }));
      if (c.status === "running") {
        row.appendChild(actionButton("stop", "", c.id, "stop", function () { return stopContainer(c); }));
      }
      row.appendChild(actionButton("remove", "danger", c.id, "remove", function () { return removeContainer(c); }));
      actions.appendChild(row);
      tr.appendChild(actions);
      rows.appendChild(tr);
    });
  }

  function refresh() {
    if (polling || !token) { return Promise.resolve(); }
    polling = true;
    return api("GET", "/api/containers").then(function (data) {
      clearError();
      containers = data.containers || [];
      if (selectedId && !findContainer(selectedId)) { closeDetail(); }
      renderTable();
      updateDetailHeader();
    }).then(null, function (e) { showError(e.message); })
      .then(function () { polling = false; });
  }

  // ---- actions ----
  function stopContainer(c) {
    return api("POST", "/api/containers/" + c.id + "/stop");
  }

  function removeContainer(c) {
    if (!window.confirm("Remove container " + label(c) + "? A running container is stopped first.")) {
      return Promise.resolve();
    }
    return api("DELETE", "/api/containers/" + c.id).then(function () {
      if (selectedId === c.id) { closeDetail(); }
    });
  }

  function launch(event) {
    event.preventDefault();
    var body = { command: $("f-command").value };
    ["name", "hostname", "memory", "cpus", "pids", "net"].forEach(function (key) {
      var value = $("f-" + key).value.trim();
      if (value !== "") { body[key] = value; }
    });
    if ($("f-init").checked) { body.init = true; }   // a real boolean, only when ticked
    busy($("launch-btn"), function () {
      return api("POST", "/api/containers", body).then(function () {
        clearError();
        $("f-command").value = "";
        $("f-name").value = "";
        $("f-init").checked = false;
      });
    }).then(refresh, function (e) { showError(e.message); });
  }

  // ---- detail panel: logs and exec for the selected container ----
  function select(id) {
    selectedId = id;
    $("detail").hidden = false;
    $("exec-out").textContent = "";
    $("exec-status").textContent = "";
    renderTable();
    updateDetailHeader();
    loadLogs();
    $("detail").scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  function closeDetail() {
    selectedId = null;
    $("detail").hidden = true;
    renderTable();
  }

  function updateDetailHeader() {
    var c = selectedId && findContainer(selectedId);
    if (!c) { return; }
    $("detail-title").textContent = label(c) + " - " + c.status;
    $("exec-btn").disabled = c.status !== "running" || !!pending[c.id + ":exec"];
    $("exec-command").disabled = c.status !== "running";
  }

  function loadLogs() {
    var id = selectedId;
    if (!id) { return Promise.resolve(); }
    return busy($("logs-refresh"), function () {
      return api("GET", "/api/containers/" + id + "/logs?tail=" + LOG_LINES).then(function (data) {
        if (id !== selectedId) { return; }
        var box = $("logs");
        box.textContent = data.logs || "(no output yet)";
        box.scrollTop = box.scrollHeight;
        clearError();
      });
    }).then(null, function (e) { showError(e.message); });
  }

  function runExec(event) {
    event.preventDefault();
    var id = selectedId;
    var command = $("exec-command").value;
    if (!id) { return; }
    pending[id + ":exec"] = true;
    updateDetailHeader();
    $("exec-status").textContent = "running...";
    api("POST", "/api/containers/" + id + "/exec", { command: command }).then(function (data) {
      if (id !== selectedId) { return; }
      $("exec-out").textContent = data.output || "(no output)";
      var note = "exit code " + data.exit_code;
      if (data.timed_out) { note += " - stopped after the 10 second time limit"; }
      if (data.truncated) { note += " - output cut at 64 KB"; }
      $("exec-status").textContent = note;
      clearError();
    }).then(null, function (e) {
      $("exec-status").textContent = "";
      showError(e.message);
    }).then(function () {
      delete pending[id + ":exec"];
      updateDetailHeader();
    });
  }

  // ---- start ----
  function init() {
    $("launch-form").addEventListener("submit", launch);
    $("logs-refresh").addEventListener("click", loadLogs);
    $("detail-close").addEventListener("click", closeDetail);
    $("exec-form").addEventListener("submit", runExec);
    document.addEventListener("visibilitychange", function () { if (!document.hidden) { refresh(); } });

    if (!token) {
      showError("No access token. Open the URL printed by the server when it started " +
                "(http://127.0.0.1:<port>/?token=...).");
      $("launch-btn").disabled = true;
      return;
    }
    refresh();
    setInterval(function () { if (!document.hidden) { refresh(); } }, POLL_MS);
  }

  init();
})();
