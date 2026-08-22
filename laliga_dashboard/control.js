/* control.js — the ⚡ Scraper button.
 *
 * Talks to the local control API in ../server.py. When that server isn't running (the public
 * GitHub Pages visit), every probe fails quietly and nothing is injected — the site looks and
 * behaves exactly as it always did. Start the server on the PC that owns the scrapers:
 *
 *     py server.py     →  http://localhost:8778/laliga_dashboard/index.html
 *
 * and the button appears in the header, on the local copy *and* on the live site (the API
 * sends the CORS + Private-Network headers Chrome wants for an https page calling loopback).
 */
(function () {
  "use strict";

  var PORT = 8778;
  var TOKEN_KEY = "ll_control_token";
  var PUSH_KEY = "ll_control_push";        // remembered "publish when done" preference
  var api = null;          // resolved base URL, e.g. "http://127.0.0.1:8778"
  var health = null;
  var offset = 0;
  var poll = null;
  var els = {};
  var state = [];          // per-season counts from /api/state, for the one-click path

  // ── plumbing ───────────────────────────────────────────────────────
  function bases() {
    var here = location.origin;
    var local = /^(localhost|127\.0\.0\.1|\[::1\])$/.test(location.hostname);
    var list = [];
    if (local && location.protocol.indexOf("http") === 0) list.push(here);
    list.push("http://127.0.0.1:" + PORT, "http://localhost:" + PORT);
    return list.filter(function (b, i) { return list.indexOf(b) === i; });
  }

  function token() { try { return localStorage.getItem(TOKEN_KEY) || ""; } catch (e) { return ""; } }

  function call(path, opts, timeoutMs) {
    opts = opts || {};
    var ctrl = new AbortController();
    var timer = setTimeout(function () { ctrl.abort(); }, timeoutMs || 8000);
    var headers = { "Content-Type": "application/json" };
    if (token()) headers["X-Control-Token"] = token();
    return fetch(api + path, {
      method: opts.method || "GET",
      headers: headers,
      body: opts.body ? JSON.stringify(opts.body) : undefined,
      signal: ctrl.signal,
      mode: "cors"
    }).then(function (r) {
      clearTimeout(timer);
      return r.json().catch(function () { return {}; }).then(function (j) {
        if (!r.ok) throw new Error(j.error || ("HTTP " + r.status));
        return j;
      });
    });
  }

  function probe() {
    var candidates = bases();
    var i = 0;
    function next() {
      if (i >= candidates.length) return Promise.resolve(null);
      api = candidates[i++];
      return fetch(api + "/api/health", { mode: "cors", signal: timeoutSignal(2000) })
        .then(function (r) { return r.ok ? r.json() : null; })
        .catch(function () { return null; })
        .then(function (j) { return j && j.ok ? j : next(); });
    }
    return next();
  }

  function timeoutSignal(ms) {
    var c = new AbortController();
    setTimeout(function () { c.abort(); }, ms);
    return c.signal;
  }

  function el(tag, cls, html) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (html != null) n.innerHTML = html;
    return n;
  }

  // ── UI ─────────────────────────────────────────────────────────────
  function build() {
    var btn = el("button", "ctl-open", "⚡ Scraper");
    btn.title = "Run the scraper / rebuild the dashboard (local control server)";
    btn.addEventListener("click", toggle);
    var host = document.querySelector(".header-right");
    if (host) host.insertBefore(btn, host.firstChild); else document.body.appendChild(btn);

    var actions = health.actions || {};
    var opts = Object.keys(actions).map(function (k) {
      return '<option value="' + k + '">' + actions[k] + "</option>";
    }).join("");

    var panel = el("div", "ctl-panel");
    panel.innerHTML =
      '<div class="ctl-head"><b>Scraper</b><span class="ctl-sub" id="ctlRoot"></span>' +
      '<button class="ctl-x" title="Close">✕</button></div>' +
      '<div class="ctl-body">' +
      '  <button class="ctl-primary" id="ctlOneClick">⚡ Update everything</button>' +
      '  <div class="ctl-primary-sub" id="ctlOneClickSub">refresh fixtures → scrape what\'s missing → publish</div>' +
      '  <div class="ctl-actions"><button class="ctl-stop" id="ctlStop" disabled>Stop</button>' +
      '    <button class="ctl-ghost" id="ctlProgress">Progress log</button>' +
      '    <button class="ctl-ghost" id="ctlAdvToggle">Advanced ▾</button></div>' +
      '  <div class="ctl-adv" id="ctlAdv">' +
      '  <div class="ctl-row"><label>Season</label><select id="ctlSeason"></select></div>' +
      '  <div class="ctl-row"><label>Action</label><select id="ctlAction">' + opts + "</select></div>" +
      '  <div class="ctl-row" id="ctlIdsRow"><label id="ctlIdsLbl">Match id(s)</label>' +
      '    <input id="ctlIds" type="text" placeholder="1914240,1914241" /></div>' +
      '  <div class="ctl-row" id="ctlLimitRow"><label>Limit / matchday</label>' +
      '    <span class="ctl-two"><input id="ctlLimit" type="number" min="1" placeholder="all" />' +
      '    <input id="ctlMatchday" type="number" min="1" max="38" placeholder="any MD" /></span></div>' +
      '  <label class="ctl-check"><input id="ctlPush" type="checkbox" /> commit &amp; push to GitHub when it finishes</label>' +
      '  <div class="ctl-actions"><button class="ctl-run" id="ctlRun">Run this action</button>' +
      '    <button class="ctl-push" id="ctlPushNow" title="Commit and push whatever is already built">Commit &amp; push</button>' +
      '  </div></div>' +
      '  <div class="ctl-status" id="ctlStatus">Idle.</div>' +
      '  <pre class="ctl-log" id="ctlLog"></pre>' +
      '  <div class="ctl-note"><select id="ctlNoteKind">' +
      '      <option value="worked">✅ What worked</option>' +
      '      <option value="failed">❌ What didn\'t</option>' +
      '      <option value="platform">🛠 Platform change</option>' +
      "    </select>" +
      '    <input id="ctlNoteText" type="text" placeholder="note for PROGRESS.md" />' +
      '    <button id="ctlNoteSave">Save</button></div>' +
      "</div>";
    document.body.appendChild(panel);

    els = {
      btn: btn, panel: panel,
      season: panel.querySelector("#ctlSeason"), action: panel.querySelector("#ctlAction"),
      ids: panel.querySelector("#ctlIds"), idsRow: panel.querySelector("#ctlIdsRow"),
      idsLbl: panel.querySelector("#ctlIdsLbl"), limitRow: panel.querySelector("#ctlLimitRow"),
      limit: panel.querySelector("#ctlLimit"), matchday: panel.querySelector("#ctlMatchday"),
      push: panel.querySelector("#ctlPush"), run: panel.querySelector("#ctlRun"),
      pushNow: panel.querySelector("#ctlPushNow"),
      stop: panel.querySelector("#ctlStop"), status: panel.querySelector("#ctlStatus"),
      log: panel.querySelector("#ctlLog"), root: panel.querySelector("#ctlRoot"),
      progress: panel.querySelector("#ctlProgress"), noteKind: panel.querySelector("#ctlNoteKind"),
      oneClick: panel.querySelector("#ctlOneClick"), oneClickSub: panel.querySelector("#ctlOneClickSub"),
      adv: panel.querySelector("#ctlAdv"), advToggle: panel.querySelector("#ctlAdvToggle"),
      noteText: panel.querySelector("#ctlNoteText"), noteSave: panel.querySelector("#ctlNoteSave")
    };

    panel.querySelector(".ctl-x").addEventListener("click", toggle);
    els.action.addEventListener("change", syncFields);
    els.oneClick.addEventListener("click", oneClick);
    els.advToggle.addEventListener("click", function () {
      var open = els.adv.classList.toggle("open");
      els.advToggle.textContent = open ? "Advanced ▴" : "Advanced ▾";
    });
    els.run.addEventListener("click", function () { run(); });
    els.pushNow.addEventListener("click", function () { run("deploy"); });
    // Publish-when-done is on by default; the choice sticks between visits.
    var remembered = null;
    try { remembered = localStorage.getItem(PUSH_KEY); } catch (e) { /* private mode */ }
    els.push.checked = remembered === null ? true : remembered === "1";
    els.push.addEventListener("change", function () {
      try { localStorage.setItem(PUSH_KEY, els.push.checked ? "1" : "0"); } catch (e) { /* ignore */ }
    });
    els.stop.addEventListener("click", stop);
    els.progress.addEventListener("click", showProgress);
    els.noteSave.addEventListener("click", saveNote);
    els.noteText.addEventListener("keydown", function (e) { if (e.key === "Enter") saveNote(); });
    syncFields();
  }

  function syncFields() {
    var a = els.action.value;
    els.push.parentNode.style.display = a === "deploy" ? "none" : "";
    var wantsIds = a === "scrape_ids" || a === "scrape_match";
    els.idsRow.style.display = wantsIds ? "" : "none";
    els.limitRow.style.display = a === "scrape_new" ? "" : "none";
    els.idsLbl.textContent = a === "scrape_match" ? "FotMob id" : "WhoScored id(s)";
    els.ids.placeholder = a === "scrape_match" ? "4837123" : "1914240,1914241";
  }

  function toggle() {
    var open = els.panel.classList.toggle("open");
    if (open) refreshState();
  }

  function refreshState() {
    call("/api/state").then(function (s) {
      var cur = els.season.value;
      var seasons = s.seasons || [];
      state = seasons;
      els.season.innerHTML = seasons.map(function (x) {
        return '<option value="' + x.season + '">' + x.season.replace("-", "/") +
          " · " + x.played + " played · " + x.scraped + " scraped" +
          (x.pending ? " · " + x.pending + " pending" : "") + "</option>";
      }).join("");
      // Default to the season with work left to do, else the newest.
      var pending = seasons.filter(function (x) { return x.pending > 0; });
      els.season.value = cur || (pending.length ? pending[pending.length - 1].season
        : (seasons.length ? seasons[seasons.length - 1].season : ""));
      describeOneClick();
      if (s.job) adopt(s.job);
    }).catch(function (e) { setStatus("Control server unreachable — " + e.message, true); });
  }

  function setStatus(text, bad) {
    els.status.textContent = text;
    els.status.classList.toggle("bad", !!bad);
  }

  function setRunning(on) {
    els.oneClick.disabled = on;
    els.run.disabled = on;
    els.pushNow.disabled = on;
    els.stop.disabled = !on;
    els.btn.classList.toggle("busy", on);
  }

  // ── the one button ─────────────────────────────────────────────────
  function oneClick() {
    // Whole update in one go: refresh the fixture list, scrape everything still
    // missing, rebuild, commit and push. Season is chosen for you — the one with
    // unscraped played matches, else the newest.
    var season = pickSeason();
    if (!season) { setStatus("No seasons found — is the schedules folder there?", true); return; }
    els.season.value = season;
    run("scrape_new", { season: season, push: true, ids: null, limit: null, matchday: null });
  }

  function pickSeason() {
    var pending = state.filter(function (x) { return x.pending > 0; });
    if (pending.length) return pending[pending.length - 1].season;
    return state.length ? state[state.length - 1].season : els.season.value;
  }

  function describeOneClick() {
    var season = pickSeason();
    var row = state.filter(function (x) { return x.season === season; })[0];
    if (!row) { els.oneClickSub.textContent = "refresh fixtures → scrape what's missing → publish"; return; }
    els.oneClickSub.textContent = season.replace("-", "/") + " · " +
      (row.pending ? row.pending + " match(es) to scrape" : "nothing pending — will check for new results") +
      " → publish";
  }

  // ── running a job ──────────────────────────────────────────────────
  function run(actionOverride, overrides) {
    // Called straight from a click handler too, so ignore the event object.
    var action = typeof actionOverride === "string" ? actionOverride : els.action.value;
    var body = {
      action: action,
      season: els.season.value,
      ids: els.ids.value.trim(),
      push: els.push.checked
    };
    if (els.limit.value) body.limit = parseInt(els.limit.value, 10);
    if (els.matchday.value) body.matchday = parseInt(els.matchday.value, 10);
    if (overrides) {
      Object.keys(overrides).forEach(function (k) {
        if (overrides[k] === null) delete body[k]; else body[k] = overrides[k];
      });
    }

    if (health.auth && !token()) {
      var t = prompt("Control token (LALIGA_CONTROL_TOKEN on the server):", "");
      if (t) { try { localStorage.setItem(TOKEN_KEY, t.trim()); } catch (e) { /* private mode */ } }
    }
    els.log.textContent = "";
    offset = 0;
    setRunning(true);
    setStatus("Starting…");
    call("/api/scrape", { method: "POST", body: body }).then(function (r) {
      adopt(r.job);
      startPolling();
    }).catch(function (e) {
      setRunning(false);
      setStatus(e.message, true);
    });
  }

  function stop() {
    call("/api/job/stop", { method: "POST" }).catch(function (e) { setStatus(e.message, true); });
  }

  function adopt(job) {
    if (!job) return;
    setRunning(job.running);
    var mins = Math.floor(job.elapsed / 60), secs = job.elapsed % 60;
    var took = (mins ? mins + "m " : "") + secs + "s";
    if (job.running) {
      setStatus(job.label + " · " + job.target + " — running " + took + "…");
      startPolling();
    } else if (job.stopped) {
      setStatus(job.label + " — stopped after " + took + ".", true);
    } else if (job.returncode === 0) {
      setStatus(job.label + " — finished in " + took + ". Logged to PROGRESS.md.");
    } else {
      setStatus(job.label + " — failed (exit " + job.returncode + ") after " + took +
        ". See the log; it's in PROGRESS.md too.", true);
    }
  }

  function startPolling() {
    if (poll) return;
    poll = setInterval(function () {
      call("/api/job/log?offset=" + offset, {}, 15000).then(function (r) {
        if (r.lines && r.lines.length) {
          offset = r.offset;
          var atBottom = els.log.scrollTop + els.log.clientHeight >= els.log.scrollHeight - 24;
          els.log.textContent += r.lines.join("\n") + "\n";
          if (atBottom) els.log.scrollTop = els.log.scrollHeight;
        } else if (typeof r.offset === "number") {
          offset = r.offset;
        }
        if (r.job && !r.job.running) {
          clearInterval(poll); poll = null;
          adopt(r.job);
          refreshState();
        } else if (r.job) {
          adopt(r.job);
        }
      }).catch(function () { /* transient — keep polling */ });
    }, 1200);
  }

  // ── PROGRESS.md ────────────────────────────────────────────────────
  function showProgress() {
    call("/api/progress").then(function (r) {
      els.log.textContent = r.text || "(PROGRESS.md is empty)";
      els.log.scrollTop = 0;
      setStatus("PROGRESS.md — newest entries.");
    }).catch(function (e) { setStatus(e.message, true); });
  }

  function saveNote() {
    var text = els.noteText.value.trim();
    if (!text) return;
    call("/api/progress", { method: "POST", body: { kind: els.noteKind.value, text: text } })
      .then(function () {
        els.noteText.value = "";
        setStatus("Saved to PROGRESS.md.");
      })
      .catch(function (e) { setStatus(e.message, true); });
  }

  // ── boot ───────────────────────────────────────────────────────────
  probe().then(function (h) {
    if (!h) return;                       // no local server → public site, inject nothing
    health = h;
    build();
    els.root.textContent = h.root || "";
    refreshState();
  }).catch(function () { /* stay invisible */ });
})();
