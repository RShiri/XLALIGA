/* Interactive single-match dashboard: clickable shot map + pass explorer with a
   minute timeline. Loads matches_detail/<id>.js (set via ?id=) as a <script> tag
   so it works on file:// without fetch/CORS. */
(function () {
  "use strict";

  var LOGO = "../team_logos/laliga/";
  var PW = 100, PH = 64; // pitch SVG units (length x width)
  var tooltip = document.getElementById("tooltip");

  // The dashboard data is season-keyed (window.LL_DATA.seasons[season]); the rest of this
  // file reads window.WC_DATA for head-to-head context, so alias the right season into it.
  (function () {
    var season = (location.search.match(/[?&]season=([^&]+)/) || [])[1];
    var LL = window.LL_DATA;
    if (LL && LL.seasons) {
      season = season ? decodeURIComponent(season) : LL.defaultSeason;
      window.WC_DATA = LL.seasons[season] || LL.seasons[LL.defaultSeason] || { matches: [] };
    }
  })();

  function el(t, c, h) { var e = document.createElement(t); if (c) e.className = c; if (h != null) e.innerHTML = h; return e; }
  function esc(s) { return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
    return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); }
  function logoImg(team) {
    return '<img src="' + LOGO + encodeURIComponent(team) + '.png" alt="" ' +
      'onerror="this.style.visibility=\'hidden\'">';
  }
  function qid() {
    var m = location.search.match(/[?&]id=([^&]+)/);
    return m ? decodeURIComponent(m[1]) : null;
  }

  var id = qid();
  if (!id) { fail("No match id in the URL. Open this page from the dashboard."); return; }

  // Inject the match data file.
  var s = document.createElement("script");
  s.src = "matches_detail/" + encodeURIComponent(id) + ".js?v=" + Date.now();
  s.onload = function () {
    if (window.MATCH_DETAIL) boot(window.MATCH_DETAIL);
    else fail("Match data loaded but was empty.");
  };
  s.onerror = function () { fail("No detailed data for this match (it may not have shot/pass events yet)."); };
  document.head.appendChild(s);

  function fail(msg) {
    document.getElementById("matchRoot").innerHTML =
      '<div class="card" style="text-align:center;padding:40px">' + esc(msg) +
      '<br><br><a class="back-link" href="index.html">← Back to all matches</a></div>';
  }

  /* ---- coordinate transforms (WhoScored 0-100 → pitch units) ---- */
  // Home attacks right; away is rotated 180° so it attacks left on the same pitch.
  // WhoScored y grows towards the attacker's left, so we flip it (PH - y) to match
  // the broadcast/PNG orientation; away gets the opposite flip (180° rotation).
  function tx(side, x) { return side === "home" ? x : PW - x; }
  function ty(side, y) { return side === "home" ? PH - y * (PH / 100) : y * (PH / 100); }

  /* ---- pitch markings ---- */
  function pitchMarkup() {
    var p = [];
    p.push('<rect class="pitch-bg" x="0" y="0" width="' + PW + '" height="' + PH + '" rx="1.5"/>');
    // subtle mowing stripes
    for (var i = 0; i < 10; i++) {
      if (i % 2 === 0) p.push('<rect x="' + (i * 10) + '" y="0" width="10" height="' + PH +
        '" fill="rgba(255,255,255,0.025)"/>');
    }
    var L = "pitch-line";
    p.push('<rect class="' + L + '" x="0.6" y="0.6" width="' + (PW - 1.2) + '" height="' + (PH - 1.2) + '"/>');
    p.push('<line class="' + L + '" x1="50" y1="0.6" x2="50" y2="' + (PH - 0.6) + '"/>');
    p.push('<circle class="' + L + '" cx="50" cy="32" r="8.3"/>');
    p.push('<circle cx="50" cy="32" r="0.5" fill="rgba(255,255,255,0.5)"/>');
    // boxes (left + right)
    var by1 = 13, by2 = PH - 13, sby1 = 23.4, sby2 = PH - 23.4;
    var GW1 = 28.5, GW2 = 35.5;          // goal-mouth y span
    [["L"], ["R"]].forEach(function (sd) {
      var right = sd[0] === "R";
      var bx = right ? PW - 15.7 : 0.6, sbx = right ? PW - 5.6 : 0.6;
      p.push('<rect class="' + L + '" x="' + bx + '" y="' + by1 + '" width="15.1" height="' + (by2 - by1) + '"/>');
      p.push('<rect class="' + L + '" x="' + sbx + '" y="' + sby1 + '" width="5" height="' + (sby2 - sby1) + '"/>');
      var spot = right ? PW - 10.5 : 10.5;
      p.push('<circle cx="' + spot + '" cy="32" r="0.5" fill="rgba(255,255,255,0.5)"/>');
      var gx = right ? PW - 0.6 : 0.6;
      p.push('<line class="' + L + '" x1="' + gx + '" y1="' + GW1 + '" x2="' + gx + '" y2="' + GW2 + '" stroke-width="0.8"/>');
      // penalty arc (the "D"): the slice of the 8.3-radius circle round the spot that pokes
      // out beyond the penalty box.
      var arcR = 8.3, boxEdge = right ? bx : bx + 15.1, ddx = Math.abs(boxEdge - spot);
      if (ddx < arcR) {
        var ddy = Math.sqrt(arcR * arcR - ddx * ddx), sweep = right ? 0 : 1;
        p.push('<path class="' + L + '" d="M ' + boxEdge.toFixed(2) + " " + (32 - ddy).toFixed(2) +
          " A " + arcR + " " + arcR + " 0 0 " + sweep + " " + boxEdge.toFixed(2) + " " + (32 + ddy).toFixed(2) + '"/>');
      }
      // goal frame (posts + net) just outside the goal line
      var gd = 1.6, gxOut = right ? PW - 0.6 : 0.6 - gd;
      p.push('<rect class="pitch-goal" x="' + gxOut.toFixed(2) + '" y="' + GW1 + '" width="' + gd + '" height="' + (GW2 - GW1) + '"/>');
      p.push('<line class="pitch-net" x1="' + gxOut.toFixed(2) + '" y1="32" x2="' + (gxOut + gd).toFixed(2) + '" y2="32"/>');
      p.push('<line class="pitch-net" x1="' + (gxOut + gd / 2).toFixed(2) + '" y1="' + GW1 + '" x2="' + (gxOut + gd / 2).toFixed(2) + '" y2="' + GW2 + '"/>');
    });
    return p.join("");
  }

  /* ---- goal-mouth ("behind the net") view, shared by the on-target shot map and
     the penalty-shootout map. WhoScored GoalMouthY runs across the goal (posts at
     45.2 / 54.8), GoalMouthZ is height (crossbar ≈ 38). Exposes the frame markup,
     the coordinate mappers and key frame positions. ---- */
  var GM = (function () {
    var GW = 100, GROUND = 50, GY0 = 43, GYR = 14, GZTOP = 48;
    function gxOf(gy) { return (Math.max(GY0, Math.min(GY0 + GYR, gy)) - GY0) / GYR * GW; }
    function gyOf(gz) { return GROUND - Math.max(0, Math.min(GZTOP, gz)); }
    var postL = gxOf(45.2), postR = gxOf(54.8), barY = gyOf(38);
    function frame() {
      var fr = "#f4f6fb", ng = "rgba(255,255,255,0.14)", net = [];
      net.push('<rect x="-2" y="-2" width="' + (GW + 4) + '" height="' + (GROUND + 6) + '" fill="#0d1420"/>');
      net.push('<rect x="' + postL.toFixed(1) + '" y="' + barY.toFixed(1) + '" width="' + (postR - postL).toFixed(1) +
        '" height="' + (GROUND - barY).toFixed(1) + '" fill="rgba(255,255,255,0.04)"/>');
      for (var nx = postL; nx <= postR + 0.01; nx += 3.2)
        net.push('<line x1="' + nx.toFixed(1) + '" y1="' + barY.toFixed(1) + '" x2="' + nx.toFixed(1) + '" y2="' + GROUND + '" stroke="' + ng + '" stroke-width="0.18"/>');
      for (var ny = barY; ny <= GROUND + 0.01; ny += 3.0)
        net.push('<line x1="' + postL.toFixed(1) + '" y1="' + ny.toFixed(1) + '" x2="' + postR.toFixed(1) + '" y2="' + ny.toFixed(1) + '" stroke="' + ng + '" stroke-width="0.18"/>');
      net.push('<line x1="0" y1="' + GROUND + '" x2="' + GW + '" y2="' + GROUND + '" stroke="rgba(255,255,255,0.3)" stroke-width="0.4"/>');
      net.push('<line x1="' + postL.toFixed(1) + '" y1="' + GROUND + '" x2="' + postL.toFixed(1) + '" y2="' + barY.toFixed(1) + '" stroke="' + fr + '" stroke-width="1.1"/>');
      net.push('<line x1="' + postR.toFixed(1) + '" y1="' + GROUND + '" x2="' + postR.toFixed(1) + '" y2="' + barY.toFixed(1) + '" stroke="' + fr + '" stroke-width="1.1"/>');
      net.push('<line x1="' + (postL - 0.55).toFixed(1) + '" y1="' + barY.toFixed(1) + '" x2="' + (postR + 0.55).toFixed(1) + '" y2="' + barY.toFixed(1) + '" stroke="' + fr + '" stroke-width="1.1"/>');
      return net.join("");
    }
    return { GW: GW, GROUND: GROUND, gxOf: gxOf, gyOf: gyOf, postL: postL, postR: postR,
             barY: barY, frame: frame, viewBox: "-2 -2 " + (GW + 4) + " " + (GROUND + 8) };
  })();

  /* ================= BOOT ================= */
  function boot(D) {
    document.title = D.home.name + " " + D.home.score + "-" + D.away.score + " " + D.away.name + " · LALIGA";
    document.documentElement.style.setProperty("--c-home", D.home.color);
    document.documentElement.style.setProperty("--c-away", D.away.color);

    var rec = matchRecord();                       // head-to-head team stats from data.js
    var hasStats = !!(rec && rec.stats);

    var root = document.getElementById("matchRoot");
    // Single scrolling page: every section is rendered one under the other.
    function block(title, id) {
      return '<section class="mv-block"><h2 class="mv-title">' + title + '</h2><div id="' + id + '"></div></section>';
    }
    var hasDribbles = !!(D.dribbles && D.dribbles.length);
    var hasGoals = !!(D.goals && D.goals.length);
    var hasShootout = !!(D.shootout && D.shootout.length);
    root.innerHTML = scoreboard(D) +
      (hasStats ? block("Match stats", "mv-stats") : "") +
      block("Shot map", "mv-shots") +
      // On-target shot map sits directly under the xG shot map.
      block("On-target shots", "mv-shots-ot") +
      block("Pass explorer", "mv-passes") +
      (hasDribbles ? block("Dribbles", "mv-dribbles") : "") +
      block("Pass network", "mv-network") +
      block("Average position", "mv-avgpos") +
      block("Line-ups", "mv-lineups") +
      // All Goals Map sits below every stats section.
      (hasGoals ? block("All goals map", "mv-goals") : "") +
      // Animated "movie" replays of each goal sit directly below the static map.
      (hasGoals ? block("Goal replays", "mv-goals-anim") : "") +
      // Penalty shootout (goal-mouth placement) is the last block — below all graphs.
      (hasShootout ? block("Penalty shootout", "mv-shootout") : "");

    if (hasStats) buildMatchStats(rec, D);
    buildShots(D);
    buildOnTargetShots(D);
    buildPasses(D);
    if (hasDribbles) buildDribbles(D);
    buildNetwork(D);
    buildAvgPos(D);
    buildLineups(D);
    if (hasGoals) buildAllGoals(D);
    if (hasGoals) buildGoalReplays(D);
    if (hasShootout) buildShootout(D);
  }

  function scoreboard(D) {
    var xgTxt = "";
    var hs = D.shots.filter(function (s) { return s.team === "home"; });
    var as = D.shots.filter(function (s) { return s.team === "away"; });
    function sum(a) { return a.reduce(function (t, s) { return t + s.xg; }, 0); }
    xgTxt = '<div class="sb-xg">Expected goals (xG): <b>' + sum(hs).toFixed(2) + "</b> — <b>" +
      sum(as).toFixed(2) + '</b> <span class="est">· model-estimated from ' + D.shots.length + " shots</span></div>";

    var goals = D.goals.map(function (g) {
      var as = g.assist ? '<span class="as">(' + esc(g.assist) + ")</span>" : "";
      return '<span class="goal-ev ' + g.team + '"><span class="min">' + g.min + "'</span> ⚽ " +
        esc(g.scorer) + (g.pen ? " (P)" : "") + (g.own ? " (OG)" : "") + " " + as + "</span>";
    }).join("");

    var pngBtn = D.png
      ? '<a class="png-btn" href="' + esc(D.png) + '" target="_blank" rel="noopener" download>' +
        '🖼️ Infographic PNG</a>'
      : "";

    return '<div class="match-top">' + pngBtn + "</div>" +
      '<div class="scoreboard"><div class="sb-main">' +
        '<div class="sb-team home"><span class="nm">' + esc(D.home.name) + "</span>" + logoImg(D.home.name) + "</div>" +
        '<div class="sb-score">' + (D.home.score == null ? "-" : D.home.score) + " : " +
          (D.away.score == null ? "-" : D.away.score) +
          (D.home.pens != null && D.away.pens != null
            ? '<span class="sb-pens">(' + D.home.pens + "-" + D.away.pens + " pens)</span>" : "") +
          "</div>" +
        '<div class="sb-team away">' + logoImg(D.away.name) + '<span class="nm">' + esc(D.away.name) + "</span></div>" +
      "</div>" + xgTxt +
      '<div class="sb-meta">' + esc(D.stage || "") + (D.venue ? " · " + esc(D.venue) : "") +
        (D.date ? " · " + esc(D.date) : "") + "</div>" +
      (goals ? '<div class="timeline">' + goals + "</div>" : "") +
      "</div>";
  }

  /* ================= MATCH STATS (head-to-head, from data.js) ================= */
  var STAT_DEFS = [
    ["possession", "Possession", true, true],
    ["xg", "Expected goals (xG)", false, true],
    ["shots", "Shots", false, true],
    ["sot", "Shots on target", false, true],
    ["big_chances", "Big chances", false, true],
    ["passes", "Passes", false, true],
    ["pass_acc", "Pass accuracy", true, true],
    ["saves", "Saves", false, true],
    ["duels_won", "Duels won", true, true],
    ["fouls", "Fouls", false, false],
  ];

  function matchRecord() {
    var arr = (window.WC_DATA && window.WC_DATA.matches) || [];
    for (var i = 0; i < arr.length; i++) if (arr[i].id === id) return arr[i];
    return null;
  }

  // Stats we can derive from the shot/pass event stream when the provider feed
  // is missing them (e.g. re-scraped matches that only carry xG + pass accuracy).
  function eventStats(D) {
    function compute(t) {
      var sh = D.shots.filter(function (x) { return x.team === t; });
      var ps = D.passes.filter(function (x) { return x.team === t; });
      var okp = ps.filter(function (p) { return p.ok; }).length;
      return {
        xg: +sh.reduce(function (a, s) { return a + s.xg; }, 0).toFixed(2),
        shots: sh.length,
        sot: sh.filter(function (s) { return s.onTarget; }).length,
        big_chances: sh.filter(function (s) { return s.big; }).length,
        passes: ps.length,
        pass_acc: ps.length ? Math.round((100 * okp) / ps.length) : null,
      };
    }
    return { home: compute("home"), away: compute("away") };
  }

  function buildMatchStats(rec, D) {
    var host = document.getElementById("mv-stats");
    if (!host) return;
    var s = rec.stats || {};
    var es = eventStats(D);
    var rows = STAT_DEFS.map(function (def) {
      var key = def[0];
      var pair = s[key] || [null, null];
      // fall back to event-derived numbers when the provider gave us nothing
      if (pair[0] == null && pair[1] == null && es.home[key] != null) {
        pair = [es.home[key], es.away[key]];
      }
      var h = pair[0], a = pair[1];
      if (h == null && a == null) return "";
      var hv = h == null ? 0 : h, av = a == null ? 0 : a;
      var total = hv + av;
      var hpct = total > 0 ? (hv / total) * 100 : 50;
      var suffix = def[2] ? "%" : "";
      function disp(x) { return x == null ? "–" : (def[0] === "xg" ? x.toFixed(2) : x) + suffix; }
      var hBetter = def[3] ? hv > av : hv < av;
      var aBetter = def[3] ? av > hv : av < hv;
      return '<div class="stat-cmp">' +
        '<div class="sc-val ' + (hBetter ? "win" : "") + '">' + disp(h) + "</div>" +
        '<div class="sc-mid"><div class="sc-label">' + def[1] + '</div>' +
          '<div class="sc-bar"><div class="sc-fill h" style="width:' + hpct.toFixed(1) + '%"></div>' +
          '<div class="sc-fill a" style="width:' + (100 - hpct).toFixed(1) + '%"></div></div></div>' +
        '<div class="sc-val ' + (aBetter ? "win" : "") + '">' + disp(a) + "</div>" +
        "</div>";
    }).join("");
    var LABELS = { fotmob: "FotMob", whoscored: "WhoScored", sofascore: "SofaScore" };
    var srcs = (rec.sources || []).map(function (s) { return LABELS[s] || s; });
    var cap = srcs.length
      ? '<div class="stat-src" style="text-align:center;font-size:.78em;opacity:.6;margin-top:8px">' +
          (srcs.length > 1 ? "Combined from " + srcs.length + " sources: " : "Source: ") +
          srcs.join(" · ") + "</div>"
      : "";
    host.innerHTML = '<div class="stat-panel" style="border-top:none">' +
      '<div class="sp-head"><span>' + esc(D.home.name) + "</span><span>" + esc(D.away.name) + "</span></div>" +
      rows + cap + "</div>";
  }

  /* ================= SHOT MAP ================= */
  function buildShots(D) {
    var host = document.getElementById("mv-shots");
    host.innerHTML =
      '<div class="controls-bar">' +
        '<span class="chip-toggle on home" id="shHome">' + esc(D.home.name) + "</span>" +
        '<span class="chip-toggle on away" id="shAway">' + esc(D.away.name) + "</span>" +
        '<span class="chip-toggle" id="shGoals">Goals only</span>' +
        '<span class="grp">Min xG <input type="text" id="shMinXg" value="0" size="3" style="width:42px"></span>' +
      "</div>" +
      '<div class="pitch-wrap"><svg class="pitch-svg" viewBox="-2 -2 ' + (PW + 4) + " " + (PH + 8) + '">' +
        pitchMarkup() +
        '<text class="dir-label" x="3" y="' + (PH + 4) + '">◀ ' + esc(D.away.name) + "</text>" +
        '<text class="dir-label" x="' + (PW - 3) + '" y="' + (PH + 4) + '" text-anchor="end">' + esc(D.home.name) + " ▶</text>" +
        '<g id="shotLayer"></g>' +
      "</svg></div>" +
      '<div class="legend-row">' +
        '<span><i class="dot" style="background:var(--c-home);border:1.5px solid #ffd34e"></i>Goal (team colour)</span>' +
        '<span><i class="dot" style="background:var(--c-home)"></i>On target</span>' +
        '<span><i class="dot" style="background:transparent;border:1px solid var(--muted)"></i>Off target</span>' +
        '<span><i class="dot" style="background:#7a869f"></i>Blocked</span>' +
        '<span>● size = xG</span>' +
      "</div>" +
      '<div class="shot-detail empty" id="shotDetail">Click any shot to see who took it, when, and its xG.</div>';

    var state = { home: true, away: true, goalsOnly: false, minXg: 0, sel: null };
    var layer = document.getElementById("shotLayer");
    var detail = document.getElementById("shotDetail");

    function draw() {
      layer.innerHTML = "";
      D.shots.forEach(function (sh, i) {
        if (!state[sh.team]) return;
        if (state.goalsOnly && !sh.goal) return;
        if (sh.xg < state.minXg) return;
        var cx = tx(sh.team, sh.x), cy = ty(sh.team, sh.y);
        // dot radius scaled to the pitch (viewBox 100×64) so dots don't overlap
        var r = 0.55 + Math.sqrt(sh.xg) * 2.0;
        var col = sh.team === "home" ? D.home.color : D.away.color;
        var NS = "http://www.w3.org/2000/svg";
        var fill, stroke = "none", op = 0.85;
        if (sh.goal) { fill = col; op = 1; }                        // goals = team colour
        else if (sh.blocked) { fill = "#7a869f"; op = 0.7; }
        else if (sh.onTarget) { fill = col; }
        else { fill = "none"; stroke = col; }
        // goals: a tight ring around the (same-size) dot so they read as goals
        if (sh.goal) {
          var ring = document.createElementNS(NS, "circle");
          ring.setAttribute("cx", cx.toFixed(2)); ring.setAttribute("cy", cy.toFixed(2));
          ring.setAttribute("r", (r + 0.55).toFixed(2));
          ring.setAttribute("fill", "none"); ring.setAttribute("stroke", "#ffd34e");
          ring.setAttribute("stroke-width", "0.45");
          layer.appendChild(ring);
        }
        var c = document.createElementNS(NS, "circle");
        c.setAttribute("class", "shot-dot");
        c.setAttribute("cx", cx.toFixed(2)); c.setAttribute("cy", cy.toFixed(2));
        c.setAttribute("r", r.toFixed(2));
        c.setAttribute("fill", fill); c.setAttribute("fill-opacity", op);
        if (stroke !== "none") { c.setAttribute("stroke", stroke); c.setAttribute("stroke-width", fill === "none" ? 0.5 : 0.3); }
        c.addEventListener("click", function () { select(sh, c); });
        c.addEventListener("mousemove", function (e) {
          showTip(e, "<b>" + esc(sh.player) + "</b> " + sh.min + "'<br>xG " + sh.xg.toFixed(2) +
            " · " + (sh.goal ? "GOAL" : sh.onTarget ? "On target" : sh.blocked ? "Blocked" : "Off target"));
        });
        c.addEventListener("mouseleave", hideTip);
        layer.appendChild(c);
      });
    }
    function select(sh, node) {
      layer.querySelectorAll(".sel").forEach(function (n) { n.classList.remove("sel"); });
      node.classList.add("sel");
      detail.className = "shot-detail";
      var teamName = sh.team === "home" ? D.home.name : D.away.name;
      detail.innerHTML = '<div class="sd-head">' + esc(sh.player) + " · " + esc(teamName) + "</div>" +
        '<div class="sd-grid">' +
        "<div><span>Minute</span><br>" + sh.min + "'</div>" +
        "<div><span>xG</span><br>" + sh.xg.toFixed(2) + "</div>" +
        "<div><span>Outcome</span><br>" + (sh.goal ? "⚽ Goal" : sh.onTarget ? "On target" : sh.blocked ? "Blocked" : "Off target") + "</div>" +
        "<div><span>Body</span><br>" + esc(sh.body) + "</div>" +
        "<div><span>Situation</span><br>" + esc(sh.sit) + (sh.big ? " · Big chance" : "") + "</div>" +
        "</div>";
    }

    document.getElementById("shHome").addEventListener("click", function () {
      state.home = !state.home; this.classList.toggle("on"); draw(); });
    document.getElementById("shAway").addEventListener("click", function () {
      state.away = !state.away; this.classList.toggle("on"); draw(); });
    document.getElementById("shGoals").addEventListener("click", function () {
      state.goalsOnly = !state.goalsOnly; this.classList.toggle("on"); draw(); });
    document.getElementById("shMinXg").addEventListener("input", function () {
      state.minXg = parseFloat(this.value) || 0; draw(); });
    draw();
  }

  /* ================= ON-TARGET SHOT MAP (goal-mouth view) ================= */
  // A "behind the goal" view: every on-target attempt (goal + saved) plotted at the
  // spot in the goal it was aimed at (WhoScored GoalMouthY/Z), coloured green for a
  // goal and blue for a save. Dot size = xG. Team chooser isolates a side. (Note:
  // WhoScored often gives saved shots a default height, so saves cluster at one row.)
  function buildOnTargetShots(D) {
    var host = document.getElementById("mv-shots-ot");
    if (!host) return;
    var onT = D.shots.filter(function (s) { return s.onTarget && s.gy != null && s.gz != null; });
    var noCoord = D.shots.filter(function (s) { return s.onTarget && (s.gy == null || s.gz == null); }).length;
    if (!onT.length) {
      host.innerHTML = '<div class="shot-detail empty">No on-target shots with goal-placement data for this match.</div>';
      return;
    }
    var GOAL_COL = "#37c978", SAVE_COL = "#5a9bff";
    host.innerHTML =
      '<div class="controls-bar">' +
        '<span class="chip-toggle on home" id="otHome">' + esc(D.home.name) + "</span>" +
        '<span class="chip-toggle on away" id="otAway">' + esc(D.away.name) + "</span>" +
        '<span class="chip-toggle" id="otGoals">Goals only</span>' +
        '<span class="ot-count" id="otCount"></span>' +
      "</div>" +
      '<div class="pitch-wrap"><svg class="pitch-svg" viewBox="' + GM.viewBox + '">' +
        GM.frame() +
        '<text class="dir-label" x="' + (GM.GW / 2) + '" y="' + (GM.GROUND + 5) +
          '" text-anchor="middle">Goal-mouth view · where on-target shots were aimed</text>' +
        '<g id="otLayer"></g>' +
      "</svg></div>" +
      '<div class="legend-row">' +
        '<span><i class="dot" style="background:' + GOAL_COL + '"></i>Goal</span>' +
        '<span><i class="dot" style="background:' + SAVE_COL + '"></i>On target (saved)</span>' +
        '<span><i class="dot" style="background:#222;border:2px solid var(--c-home)"></i>Team ring</span>' +
        '<span>● size = xG</span>' +
      "</div>" +
      '<div class="shot-detail empty" id="otDetail">' +
        "Each dot is an on-target shot, placed where it was aimed in the goal. Click one for details." +
        (noCoord ? " (" + noCoord + " on-target shot" + (noCoord === 1 ? "" : "s") + " had no placement data.)" : "") +
      "</div>";

    var state = { home: true, away: true, goalsOnly: false };
    var layer = document.getElementById("otLayer");
    var detail = document.getElementById("otDetail");
    var countEl = document.getElementById("otCount");
    var NS = "http://www.w3.org/2000/svg";

    function draw() {
      layer.innerHTML = "";
      var shown = 0, hg = 0, ag = 0;
      onT.forEach(function (sh) {
        if (!state[sh.team]) return;
        if (state.goalsOnly && !sh.goal) return;
        shown++;
        if (sh.team === "home") hg++; else ag++;
        var cx = GM.gxOf(sh.gy), cy = GM.gyOf(sh.gz);
        var r = 1.4 + Math.sqrt(sh.xg) * 2.6;
        var ring = sh.team === "home" ? D.home.color : D.away.color;
        var c = document.createElementNS(NS, "circle");
        c.setAttribute("cx", cx.toFixed(2)); c.setAttribute("cy", cy.toFixed(2));
        c.setAttribute("r", r.toFixed(2));
        c.setAttribute("fill", sh.goal ? GOAL_COL : SAVE_COL);
        c.setAttribute("fill-opacity", sh.goal ? 0.95 : 0.8);
        c.setAttribute("stroke", ring); c.setAttribute("stroke-width", "0.7");
        c.style.cursor = "pointer";
        c.addEventListener("click", function () { select(sh, c); });
        c.addEventListener("mousemove", function (e) {
          showTip(e, "<b>" + esc(sh.player) + "</b> " + sh.min + "'<br>xG " + sh.xg.toFixed(2) +
            " · " + (sh.goal ? "GOAL" : "On target (saved)"));
        });
        c.addEventListener("mouseleave", hideTip);
        layer.appendChild(c);
      });
      countEl.textContent = shown + " on target · " + hg + "–" + ag;
    }
    function select(sh, node) {
      layer.querySelectorAll(".sel").forEach(function (n) { n.classList.remove("sel"); });
      node.classList.add("sel");
      detail.className = "shot-detail";
      var teamName = sh.team === "home" ? D.home.name : D.away.name;
      detail.innerHTML = '<div class="sd-head">' + esc(sh.player) + " · " + esc(teamName) + "</div>" +
        '<div class="sd-grid">' +
        "<div><span>Minute</span><br>" + sh.min + "'</div>" +
        "<div><span>xG</span><br>" + sh.xg.toFixed(2) + "</div>" +
        "<div><span>Outcome</span><br>" + (sh.goal ? "⚽ Goal" : "On target (saved)") + "</div>" +
        "<div><span>Body</span><br>" + esc(sh.body) + "</div>" +
        "<div><span>Situation</span><br>" + esc(sh.sit) + (sh.big ? " · Big chance" : "") + "</div>" +
        "</div>";
    }

    document.getElementById("otHome").addEventListener("click", function () {
      state.home = !state.home; this.classList.toggle("on"); draw(); });
    document.getElementById("otAway").addEventListener("click", function () {
      state.away = !state.away; this.classList.toggle("on"); draw(); });
    document.getElementById("otGoals").addEventListener("click", function () {
      state.goalsOnly = !state.goalsOnly; this.classList.toggle("on"); draw(); });
    draw();
  }

  /* ================= PASS EXPLORER ================= */
  function buildPasses(D) {
    var host = document.getElementById("mv-passes");
    var players = {};
    D.passes.forEach(function (p) { (players[p.team] = players[p.team] || {})[p.player] = 1; });
    function opts(side) {
      return Object.keys(players[side] || {}).sort().map(function (n) {
        return '<option value="' + esc(n) + '">' + esc(n) + "</option>"; }).join("");
    }

    host.innerHTML =
      '<div class="controls-bar">' +
        '<span class="chip-toggle on home" id="paHome">' + esc(D.home.name) + "</span>" +
        '<span class="chip-toggle on away" id="paAway">' + esc(D.away.name) + "</span>" +
        '<span class="grp">Player <select id="paPlayer"><option value="">All players</option>' +
          '<optgroup label="' + esc(D.home.name) + '" data-side="home">' + opts("home") + "</optgroup>" +
          '<optgroup label="' + esc(D.away.name) + '" data-side="away">' + opts("away") + "</optgroup></select></span>" +
        '<span class="grp">Type <select id="paType">' +
          '<option value="all">All passes</option><option value="ok">Completed</option>' +
          '<option value="fail">Incomplete</option><option value="prog">Progressive</option>' +
          '<option value="key">Key passes</option>' +
          '<option value="assist">Assists</option><option value="cross">Crosses</option>' +
          '<option value="through">Through balls</option></select></span>' +
        '<span class="chip-toggle" id="paThird">Final third</span>' +
        '<span class="chip-toggle" id="paWindow">5-min window</span>' +
      "</div>" +
      '<div class="timeline-scrub">' +
        '<button class="play-btn" id="paPlay">▶</button>' +
        '<input type="range" id="paRange" min="0" max="' + (D.maxMin || 90) + '" value="' + (D.maxMin || 90) + '">' +
        '<span class="minlab" id="paMinLab"></span>' +
      "</div>" +
      '<div class="pitch-wrap"><svg class="pitch-svg" viewBox="-2 -2 ' + (PW + 4) + " " + (PH + 8) + '">' +
        pitchMarkup() +
        '<text class="dir-label" x="3" y="' + (PH + 4) + '">◀ ' + esc(D.away.name) + "</text>" +
        '<text class="dir-label" x="' + (PW - 3) + '" y="' + (PH + 4) + '" text-anchor="end">' + esc(D.home.name) + " ▶</text>" +
        '<g id="passLayer"></g>' +
      "</svg></div>" +
      '<div class="legend-row">' +
        '<span><i class="dot" style="background:#43e8a0"></i>completed · progressive/key</span>' +
        '<span><i class="dot" style="background:#1f9d5e"></i>completed · normal</span>' +
        '<span><i class="dot" style="background:#ff5e7a"></i>incomplete · forward/key</span>' +
        '<span><i class="dot" style="background:#a83646"></i>incomplete · normal</span>' +
        '<span>dashed = incomplete · dot = start</span>' +
      "</div>" +
      '<div class="stat-note" id="paCount"></div>';

    var state = { home: true, away: true, player: "", type: "all", third: false, windowMode: false, upper: D.maxMin || 90 };
    var layer = document.getElementById("passLayer");
    var minLab = document.getElementById("paMinLab");
    var countEl = document.getElementById("paCount");
    var SVGNS = "http://www.w3.org/2000/svg";

    function pass_passes_filter(p) {
      if (!state[p.team]) return false;
      if (state.player && p.player !== state.player) return false;
      var t = state.type;
      if (t === "ok" && !p.ok) return false;
      if (t === "fail" && p.ok) return false;
      if (t === "prog" && !p.prog) return false;
      if (t === "key" && !p.key && !p.assist) return false;
      if (t === "assist" && !p.assist) return false;
      if (t === "cross" && !p.cross) return false;
      if (t === "through" && !p.through) return false;
      // Final third = pass that ENDS in the attacking third (WhoScored x ≥ 66.7).
      if (state.third && p.ex < 200 / 3) return false;
      if (p.min > state.upper) return false;
      if (state.windowMode && p.min < state.upper - 5) return false;
      return true;
    }

    function draw() {
      layer.innerHTML = "";
      var shown = 0;
      var frag = document.createDocumentFragment();
      D.passes.forEach(function (p) {
        if (!pass_passes_filter(p)) return;
        shown++;
        var x1 = tx(p.team, p.x), y1 = ty(p.team, p.y);
        var x2 = tx(p.team, p.ex), y2 = ty(p.team, p.ey);
        // Two greens for completed (bright = progressive/key/assist, dim = normal);
        // two reds for incomplete (bright = forward/key attempt, dim = normal).
        var dangerous = p.prog || p.key || p.assist || p.through;
        var col = p.ok
          ? (dangerous ? "#43e8a0" : "#1f9d5e")
          : (dangerous ? "#ff5e7a" : "#a83646");
        var ln = document.createElementNS(SVGNS, "line");
        var cls = "pass-line" + (p.assist ? " assist" : p.key || p.prog ? " key" : "");
        ln.setAttribute("class", cls);
        ln.setAttribute("x1", x1.toFixed(2)); ln.setAttribute("y1", y1.toFixed(2));
        ln.setAttribute("x2", x2.toFixed(2)); ln.setAttribute("y2", y2.toFixed(2));
        ln.setAttribute("stroke", col);
        if (!p.ok) ln.setAttribute("stroke-dasharray", "0.9 0.9");
        ln.addEventListener("mousemove", function (e) {
          showTip(e, "<b>" + esc(p.player) + "</b> " + p.min + "'" +
            (p.recv ? " → " + esc(p.recv) : "") + "<br>" +
            (p.ok ? "Completed" : "Incomplete") + (p.assist ? " · ASSIST" : p.key ? " · key pass" :
              p.through ? " · through ball" : p.cross ? " · cross" : p.prog ? " · progressive" : ""));
        });
        ln.addEventListener("mouseleave", hideTip);
        frag.appendChild(ln);
        var d0 = document.createElementNS(SVGNS, "circle");
        d0.setAttribute("class", "pass-dot");
        d0.setAttribute("cx", x1.toFixed(2)); d0.setAttribute("cy", y1.toFixed(2));
        d0.setAttribute("r", "0.6"); d0.setAttribute("fill", col);
        frag.appendChild(d0);
      });
      layer.appendChild(frag);
      countEl.textContent = shown + " passes shown" + (state.windowMode ? " (minutes " + Math.max(0, state.upper - 5) + "–" + state.upper + ")" : " (up to " + state.upper + "')");
    }
    function setUpper(v) {
      state.upper = v;
      minLab.textContent = (state.windowMode ? Math.max(0, v - 5) + "–" + v : "0–" + v) + "'";
      draw();
    }

    document.getElementById("paHome").addEventListener("click", function () { state.home = !state.home; this.classList.toggle("on"); draw(); });
    document.getElementById("paAway").addEventListener("click", function () { state.away = !state.away; this.classList.toggle("on"); draw(); });
    document.getElementById("paPlayer").addEventListener("change", function () { state.player = this.value; draw(); });
    document.getElementById("paType").addEventListener("change", function () { state.type = this.value; draw(); });
    document.getElementById("paThird").addEventListener("click", function () { state.third = !state.third; this.classList.toggle("on"); draw(); });
    document.getElementById("paWindow").addEventListener("click", function () { state.windowMode = !state.windowMode; this.classList.toggle("on"); setUpper(state.upper); });
    var range = document.getElementById("paRange");
    range.addEventListener("input", function () { stopPlay(); setUpper(parseInt(this.value, 10)); });

    // play animation
    var timer = null;
    var playBtn = document.getElementById("paPlay");
    function stopPlay() { if (timer) { clearInterval(timer); timer = null; playBtn.classList.remove("playing"); playBtn.textContent = "▶"; } }
    playBtn.addEventListener("click", function () {
      if (timer) { stopPlay(); return; }
      if (state.upper >= (D.maxMin || 90)) { range.value = 0; setUpper(0); }
      playBtn.classList.add("playing"); playBtn.textContent = "❚❚";
      timer = setInterval(function () {
        var v = state.upper + 1;
        if (v > (D.maxMin || 90)) { stopPlay(); return; }
        range.value = v; setUpper(v);
      }, 180);
    });

    setUpper(state.upper);
  }

  /* ================= DRIBBLES (take-ons) ================= */
  function buildDribbles(D) {
    var host = document.getElementById("mv-dribbles");
    if (!host) return;
    var players = {};
    D.dribbles.forEach(function (p) { (players[p.team] = players[p.team] || {})[p.player] = 1; });
    function opts(side) {
      return Object.keys(players[side] || {}).sort().map(function (n) {
        return '<option value="' + esc(n) + '">' + esc(n) + "</option>"; }).join("");
    }

    host.innerHTML =
      '<div class="controls-bar">' +
        '<span class="chip-toggle on home" id="drHome">' + esc(D.home.name) + "</span>" +
        '<span class="chip-toggle on away" id="drAway">' + esc(D.away.name) + "</span>" +
        '<span class="grp">Player <select id="drPlayer"><option value="">All players</option>' +
          '<optgroup label="' + esc(D.home.name) + '">' + opts("home") + "</optgroup>" +
          '<optgroup label="' + esc(D.away.name) + '">' + opts("away") + "</optgroup></select></span>" +
        '<span class="grp">Outcome <select id="drType">' +
          '<option value="all">All dribbles</option><option value="ok">Successful</option>' +
          '<option value="fail">Unsuccessful</option></select></span>' +
        '<span class="chip-toggle" id="drWindow">5-min window</span>' +
      "</div>" +
      '<div class="timeline-scrub">' +
        '<button class="play-btn" id="drPlay">▶</button>' +
        '<input type="range" id="drRange" min="0" max="' + (D.maxMin || 90) + '" value="' + (D.maxMin || 90) + '">' +
        '<span class="minlab" id="drMinLab"></span>' +
      "</div>" +
      '<div class="pitch-wrap"><svg class="pitch-svg" viewBox="-2 -2 ' + (PW + 4) + " " + (PH + 8) + '">' +
        '<defs>' +
          '<marker id="drArrG" markerWidth="4" markerHeight="4" refX="3.1" refY="2" orient="auto"><path d="M0,0 L4,2 L0,4 Z" fill="#43e8a0"/></marker>' +
          '<marker id="drArrR" markerWidth="4" markerHeight="4" refX="3.1" refY="2" orient="auto"><path d="M0,0 L4,2 L0,4 Z" fill="#ff5e7a"/></marker>' +
        "</defs>" +
        pitchMarkup() +
        '<text class="dir-label" x="3" y="' + (PH + 4) + '">◀ ' + esc(D.away.name) + "</text>" +
        '<text class="dir-label" x="' + (PW - 3) + '" y="' + (PH + 4) + '" text-anchor="end">' + esc(D.home.name) + " ▶</text>" +
        '<g id="dribLayer"></g>' +
      "</svg></div>" +
      '<div class="legend-row">' +
        '<span><i class="dot" style="background:#43e8a0"></i>successful</span>' +
        '<span><i class="dot" style="background:transparent;border:1px solid #ff5e7a"></i>unsuccessful</span>' +
        '<span>● = where the take-on happened · → carry direction (next touch)</span>' +
      "</div>" +
      '<div class="stat-note" id="drCount"></div>';

    // Carry direction: build_match_details now ships ex/ey for each take-on (the player's
    // next on-ball event, found across the FULL event stream). For any older match file
    // without it, fall back to inferring from the next tracked touch (pass/shot/dribble).
    (function () {
      var byPlayer = {}, T = function (o) { return (o.min || 0) * 60 + (o.sec || 0); };
      function add(t, pl, tt, x, y) { var k = t + "|" + pl; (byPlayer[k] = byPlayer[k] || []).push({ t: tt, x: x, y: y }); }
      (D.passes || []).forEach(function (p) { add(p.team, p.player, T(p), p.x, p.y); });
      (D.shots || []).forEach(function (s) { add(s.team, s.player, T(s), s.x, s.y); });
      (D.dribbles || []).forEach(function (d) { add(d.team, d.player, T(d), d.x, d.y); });
      Object.keys(byPlayer).forEach(function (k) { byPlayer[k].sort(function (a, b) { return a.t - b.t; }); });
      D.dribbles.forEach(function (d) {
        if (d.ex != null) { d._ex = d.ex; d._ey = d.ey; return; }   // prefer the server-computed end
        var arr = byPlayer[d.team + "|" + d.player] || [], dt = T(d), nxt = null;
        for (var i = 0; i < arr.length; i++) { if (arr[i].t > dt && arr[i].t - dt <= 8) { nxt = arr[i]; break; } }
        if (nxt && (Math.abs(nxt.x - d.x) > 0.8 || Math.abs(nxt.y - d.y) > 0.8)) { d._ex = nxt.x; d._ey = nxt.y; }
      });
    })();

    var state = { home: true, away: true, player: "", type: "all", windowMode: false, upper: D.maxMin || 90 };
    var layer = document.getElementById("dribLayer");
    var minLab = document.getElementById("drMinLab");
    var countEl = document.getElementById("drCount");
    var SVGNS = "http://www.w3.org/2000/svg";

    function filt(p) {
      if (!state[p.team]) return false;
      if (state.player && p.player !== state.player) return false;
      if (state.type === "ok" && !p.ok) return false;
      if (state.type === "fail" && p.ok) return false;
      if (p.min > state.upper) return false;
      if (state.windowMode && p.min < state.upper - 5) return false;
      return true;
    }

    function draw() {
      layer.innerHTML = "";
      var shown = 0, ok = 0;
      var frag = document.createDocumentFragment();
      D.dribbles.forEach(function (p) {
        if (!filt(p)) return;
        shown++; if (p.ok) ok++;
        var cx = tx(p.team, p.x), cy = ty(p.team, p.y);
        var col = p.ok ? "#43e8a0" : "#ff5e7a";
        if (p._ex != null) {
          var ax = tx(p.team, p._ex), ay = ty(p.team, p._ey);
          var arr = document.createElementNS(SVGNS, "line");
          arr.setAttribute("class", "drib-arrow");
          arr.setAttribute("x1", cx.toFixed(2)); arr.setAttribute("y1", cy.toFixed(2));
          arr.setAttribute("x2", ax.toFixed(2)); arr.setAttribute("y2", ay.toFixed(2));
          arr.setAttribute("stroke", col); arr.setAttribute("stroke-width", "0.45");
          arr.setAttribute("stroke-opacity", "0.85");
          arr.setAttribute("marker-end", p.ok ? "url(#drArrG)" : "url(#drArrR)");
          arr.addEventListener("mousemove", function (e) {
            showTip(e, "<b>" + esc(p.player) + "</b> " + p.min + "'<br>" +
              (p.ok ? "Successful dribble" : "Unsuccessful dribble") + " · carry direction");
          });
          arr.addEventListener("mouseleave", hideTip);
          frag.appendChild(arr);
        }
        var c = document.createElementNS(SVGNS, "circle");
        c.setAttribute("class", "drib-dot");
        c.setAttribute("cx", cx.toFixed(2)); c.setAttribute("cy", cy.toFixed(2));
        c.setAttribute("r", "1.1");
        c.setAttribute("fill", p.ok ? col : "none");
        c.setAttribute("stroke", col); c.setAttribute("stroke-width", "0.4");
        c.setAttribute("fill-opacity", "0.85");
        c.addEventListener("mousemove", function (e) {
          showTip(e, "<b>" + esc(p.player) + "</b> " + p.min + "'<br>" +
            (p.ok ? "Successful dribble" : "Unsuccessful dribble"));
        });
        c.addEventListener("mouseleave", hideTip);
        frag.appendChild(c);
      });
      layer.appendChild(frag);
      var pct = shown ? Math.round((100 * ok) / shown) : 0;
      countEl.textContent = shown + " dribbles · " + ok + " successful (" + pct + "%)" +
        (state.windowMode ? " · minutes " + Math.max(0, state.upper - 5) + "–" + state.upper
          : " · up to " + state.upper + "'");
    }
    function setUpper(v) {
      state.upper = v;
      minLab.textContent = (state.windowMode ? Math.max(0, v - 5) + "–" + v : "0–" + v) + "'";
      draw();
    }

    document.getElementById("drHome").addEventListener("click", function () { state.home = !state.home; this.classList.toggle("on"); draw(); });
    document.getElementById("drAway").addEventListener("click", function () { state.away = !state.away; this.classList.toggle("on"); draw(); });
    document.getElementById("drPlayer").addEventListener("change", function () { state.player = this.value; draw(); });
    document.getElementById("drType").addEventListener("change", function () { state.type = this.value; draw(); });
    document.getElementById("drWindow").addEventListener("click", function () { state.windowMode = !state.windowMode; this.classList.toggle("on"); setUpper(state.upper); });
    var range = document.getElementById("drRange");
    range.addEventListener("input", function () { stopPlay(); setUpper(parseInt(this.value, 10)); });

    var timer = null;
    var playBtn = document.getElementById("drPlay");
    function stopPlay() { if (timer) { clearInterval(timer); timer = null; playBtn.classList.remove("playing"); playBtn.textContent = "▶"; } }
    playBtn.addEventListener("click", function () {
      if (timer) { stopPlay(); return; }
      if (state.upper >= (D.maxMin || 90)) { range.value = 0; setUpper(0); }
      playBtn.classList.add("playing"); playBtn.textContent = "❚❚";
      timer = setInterval(function () {
        var v = state.upper + 1;
        if (v > (D.maxMin || 90)) { stopPlay(); return; }
        range.value = v; setUpper(v);
      }, 180);
    });

    setUpper(state.upper);
  }

  /* ================= PASS NETWORK (avg position + links) ================= */
  function buildNetwork(D) {
    var host = document.getElementById("mv-network");
    // name -> shirt number, per side, from the line-ups
    var numMap = { home: {}, away: {} };
    ["home", "away"].forEach(function (sd) {
      (D.lineups[sd].starters.concat(D.lineups[sd].subs)).forEach(function (p) {
        numMap[sd][p.name] = p.num;
      });
    });

    host.innerHTML =
      '<div class="controls-bar">' +
        '<span class="chip-toggle on home" id="nwHome">' + esc(D.home.name) + "</span>" +
        '<span class="chip-toggle away" id="nwAway">' + esc(D.away.name) + "</span>" +
        '<span class="grp">Min. combined passes <input type="range" id="nwMin" min="1" max="10" value="3" style="width:90px"> <b id="nwMinLab">3</b></span>' +
      "</div>" +
      '<div class="pitch-wrap"><svg class="pitch-svg" viewBox="-2 -2 ' + (PW + 4) + " " + (PH + 8) + '">' +
        pitchMarkup() +
        '<text class="dir-label" x="' + (PW / 2) + '" y="' + (PH + 4) + '" text-anchor="middle" id="nwDir">attacking →</text>' +
        '<g id="nwLinks"></g><g id="nwNodes"></g>' +
      "</svg></div>" +
      '<div class="legend-row">' +
        '<span>● node = player at average position · size = passes involved</span>' +
        '<span>line thickness = passes between the pair</span>' +
        '<span>starting XI, until the first substitution</span>' +
      "</div>" +
      '<div class="stat-note" id="nwNote"></div>';

    var state = { side: "home", minLink: 3 };
    var linkG = document.getElementById("nwLinks");
    var nodeG = document.getElementById("nwNodes");
    var note = document.getElementById("nwNote");
    var NS = "http://www.w3.org/2000/svg";

    // Standard pass network: we choose the longest interval during the match in which
    // the same 11 players were on the pitch together. This prevents early substitutions
    // (e.g. injuries in the first 5 minutes) from making the network empty and useless.
    function compute() {
      var maxMin = D.maxMin || 90;
      var side = state.side;
      var players = D.lineups[side].starters.concat(D.lineups[side].subs);

      // Collect all transition minutes (subs, etc.)
      var transitionMins = [0, maxMin];
      players.forEach(function (p) {
        if (p.on != null) transitionMins.push(p.on);
        if (p.off != null) transitionMins.push(p.off);
      });
      transitionMins = Array.from(new Set(transitionMins)).sort(function (a, b) { return a - b; });

      // Find the longest interval between substitutions
      var start = 0;
      var end = maxMin;
      var maxDuration = 0;
      for (var i = 0; i < transitionMins.length - 1; i++) {
        var s = transitionMins[i];
        var e = transitionMins[i + 1];
        var duration = e - s;
        if (duration > maxDuration) {
          maxDuration = duration;
          start = s;
          end = e;
        }
      }

      // Find which players were active at the midpoint of this longest interval
      var mid = (start + end) / 2;
      var active = {};
      players.forEach(function (p) {
        var playerOn = p.on != null ? p.on : 0;
        var playerOff = p.off != null ? p.off : maxMin;
        if (playerOn <= mid && playerOff >= mid) {
          active[p.name] = true;
        }
      });

      var nodes = {}; // name -> {sx,sy,n, passes}
      var links = {}; // "a|b" -> count
      function node(name) {
        return nodes[name] || (nodes[name] = { name: name, x: 0, y: 0, n: 0, passes: 0 });
      }

      D.passes.forEach(function (p) {
        if (p.team !== side || !p.ok || p.min < start || p.min > end) return;
        if (!active[p.player]) return;            // passer must be active in this interval
        var passer = node(p.player);
        passer.x += p.x; passer.y += p.y; passer.n++; passer.passes++;
        if (p.recv && active[p.recv]) {           // receiver must be active too
          var r = node(p.recv);
          r.x += p.ex; r.y += p.ey; r.n++; r.passes++;
          var key = [p.player, p.recv].sort().join("|");
          links[key] = (links[key] || 0) + 1;
        }
      });

      Object.keys(nodes).forEach(function (k) {
        var nd = nodes[k]; if (nd.n) { nd.x /= nd.n; nd.y /= nd.n; }
      });

      return { nodes: nodes, links: links, start: start, end: end };
    }

    function draw() {
      var net = compute();
      var col = state.side === "home" ? D.home.color : D.away.color;
      linkG.innerHTML = ""; nodeG.innerHTML = "";
      var maxPasses = 1;
      Object.keys(net.nodes).forEach(function (k) { maxPasses = Math.max(maxPasses, net.nodes[k].passes); });
      var maxLink = 1;
      Object.keys(net.links).forEach(function (k) { maxLink = Math.max(maxLink, net.links[k]); });

      var nLinks = 0;
      Object.keys(net.links).forEach(function (key) {
        var count = net.links[key];
        if (count < state.minLink) return;
        var parts = key.split("|");
        var a = net.nodes[parts[0]], b = net.nodes[parts[1]];
        if (!a || !b) return;
        nLinks++;
        var ln = document.createElementNS(NS, "line");
        ln.setAttribute("x1", tx(state.side, a.x).toFixed(2)); ln.setAttribute("y1", ty(state.side, a.y).toFixed(2));
        ln.setAttribute("x2", tx(state.side, b.x).toFixed(2)); ln.setAttribute("y2", ty(state.side, b.y).toFixed(2));
        ln.setAttribute("stroke", col);
        ln.setAttribute("stroke-opacity", (0.25 + 0.6 * count / maxLink).toFixed(2));
        ln.setAttribute("stroke-width", (0.3 + 1.5 * count / maxLink).toFixed(2));
        ln.setAttribute("stroke-linecap", "round");
        ln.addEventListener("mousemove", function (e) {
          showTip(e, "<b>" + esc(parts[0]) + " ↔ " + esc(parts[1]) + "</b><br>" + count + " passes");
        });
        ln.addEventListener("mouseleave", hideTip);
        linkG.appendChild(ln);
      });

      Object.keys(net.nodes).forEach(function (k) {
        var nd = net.nodes[k];
        var cx = tx(state.side, nd.x), cy = ty(state.side, nd.y);
        var r = 1.6 + 2.6 * nd.passes / maxPasses;
        var c = document.createElementNS(NS, "circle");
        c.setAttribute("cx", cx.toFixed(2)); c.setAttribute("cy", cy.toFixed(2));
        c.setAttribute("r", r.toFixed(2));
        c.setAttribute("fill", col); c.setAttribute("fill-opacity", "0.92");
        c.setAttribute("stroke", "#0b0f1a"); c.setAttribute("stroke-width", "0.3");
        c.addEventListener("mousemove", function (e) {
          showTip(e, "<b>" + esc(nd.name) + "</b><br>" + nd.passes + " passes involved");
        });
        c.addEventListener("mouseleave", hideTip);
        nodeG.appendChild(c);
        var num = numMap[state.side][nd.name];
        if (num != null) {
          var t = document.createElementNS(NS, "text");
          t.setAttribute("x", cx.toFixed(2)); t.setAttribute("y", (cy + 0.9).toFixed(2));
          t.setAttribute("text-anchor", "middle"); t.setAttribute("font-size", "2.4");
          t.setAttribute("font-weight", "800"); t.setAttribute("fill", "#fff");
          t.setAttribute("pointer-events", "none");
          t.textContent = num;
          nodeG.appendChild(t);
        }
      });
      var teamName = state.side === "home" ? D.home.name : D.away.name;
      note.textContent = teamName + " · " + Object.keys(net.nodes).length + " players · " +
        nLinks + " passing links (min " + state.minLink + ") · positions during minutes " + net.start + "–" + net.end + "'";
      document.getElementById("nwDir").textContent =
        (state.side === "home" ? teamName + " attacking →" : "← " + teamName + " attacking");
    }

    function setSide(side) {
      state.side = side;
      document.getElementById("nwHome").classList.toggle("on", side === "home");
      document.getElementById("nwAway").classList.toggle("on", side === "away");
      draw();
    }
    document.getElementById("nwHome").addEventListener("click", function () { setSide("home"); });
    document.getElementById("nwAway").addEventListener("click", function () { setSide("away"); });
    document.getElementById("nwMin").addEventListener("input", function () {
      state.minLink = parseInt(this.value, 10); document.getElementById("nwMinLab").textContent = this.value; draw();
    });
    draw();
  }

  /* ================= AVERAGE POSITION (windowed, scrubbable) =================
     Like the pass network but it averages each player's touch positions over a sliding
     time window the user scrubs (or plays). Players appear/disappear as they're subbed
     on/off, so you can watch a team's shape shift across the game. */
  function buildAvgPos(D) {
    var host = document.getElementById("mv-avgpos");
    if (!host) return;
    var maxMin = D.maxMin || 90;
    var info = { home: {}, away: {} };           // name -> {num, on, off, starter} per side
    ["home", "away"].forEach(function (sd) {
      D.lineups[sd].starters.forEach(function (p) {
        info[sd][p.name] = { num: p.num, on: (p.on != null ? p.on : 0), off: (p.off != null ? p.off : maxMin), starter: true };
      });
      D.lineups[sd].subs.forEach(function (p) {
        info[sd][p.name] = { num: p.num, on: (p.on != null ? p.on : 0), off: (p.off != null ? p.off : maxMin), starter: false };
      });
    });

    host.innerHTML =
      '<div class="controls-bar">' +
        '<span class="chip-toggle on home" id="apHome">' + esc(D.home.name) + "</span>" +
        '<span class="chip-toggle away" id="apAway">' + esc(D.away.name) + "</span>" +
        '<span class="grp">Window <select id="apWin">' +
          '<option value="10">10 min</option><option value="15" selected>15 min</option>' +
          '<option value="20">20 min</option><option value="0">Full (0→now)</option></select></span>' +
      "</div>" +
      '<div class="timeline-scrub">' +
        '<button class="play-btn" id="apPlay">▶</button>' +
        '<input type="range" id="apRange" min="0" max="' + maxMin + '" value="' + maxMin + '">' +
        '<span class="minlab" id="apMinLab"></span>' +
      "</div>" +
      '<div class="pitch-wrap"><svg class="pitch-svg" viewBox="-2 -2 ' + (PW + 4) + " " + (PH + 8) + '">' +
        pitchMarkup() +
        '<text class="dir-label" x="' + (PW / 2) + '" y="' + (PH + 4) + '" text-anchor="middle" id="apDir">attacking →</text>' +
        '<g id="apNodes"></g>' +
      "</svg></div>" +
      '<div class="legend-row">' +
        "<span>● node = player's average position in the window · size = touches</span>" +
        "<span>players appear/disappear as they're subbed on and off</span>" +
        "<span>scrub or press ▶ to watch the shape change</span>" +
      "</div>" +
      '<div class="stat-note" id="apNote"></div>';

    var state = { side: "home", win: 15, upper: maxMin };
    var nodeG = document.getElementById("apNodes");
    var minLab = document.getElementById("apMinLab");
    var note = document.getElementById("apNote");
    var NS = "http://www.w3.org/2000/svg";

    function compute() {
      var lo = state.win === 0 ? 0 : Math.max(0, state.upper - state.win), hi = state.upper, side = state.side;
      var acc = {};
      function inWin(m) { return m >= lo && m <= hi; }
      function add(name, x, y) { if (!info[side][name]) return; var a = acc[name] || (acc[name] = { x: 0, y: 0, n: 0 }); a.x += x; a.y += y; a.n++; }
      D.passes.forEach(function (p) {
        if (p.team !== side || !inWin(p.min)) return;
        add(p.player, p.x, p.y);
        if (p.recv) add(p.recv, p.ex, p.ey);     // receiver counts at where the ball arrived
      });
      (D.dribbles || []).forEach(function (d) { if (d.team === side && inWin(d.min)) add(d.player, d.x, d.y); });
      (D.shots || []).forEach(function (s) { if (s.team === side && inWin(s.min)) add(s.player, s.x, s.y); });

      // Build substitution slots to prevent displaying more than 11 players when players sub each other in the current window
      var activeInWindow = {};
      if (state.win !== 0) {
        var starters = D.lineups[side].starters.map(function (p) {
          var pi = info[side][p.name];
          return { name: p.name, num: p.num, on: pi.on, off: pi.off };
        });
        var subs = D.lineups[side].subs.map(function (p) {
          var pi = info[side][p.name];
          return { name: p.name, num: p.num, on: pi.on, off: pi.off };
        }).sort(function (a, b) { return a.on - b.on; });

        var slots = starters.map(function (p) { return [p]; });
        subs.forEach(function (s) {
          var bestSlot = -1;
          var minDiff = 999;
          slots.forEach(function (slot, idx) {
            var lastPlayer = slot[slot.length - 1];
            var diff = Math.abs(lastPlayer.off - s.on);
            if (diff < minDiff) {
              minDiff = diff;
              bestSlot = idx;
            }
          });
          if (bestSlot !== -1) {
            slots[bestSlot].push(s);
          }
        });

        slots.forEach(function (slot) {
          var bestPlayer = null;
          var maxDur = 0;
          slot.forEach(function (p) {
            var dur = Math.max(0, Math.min(hi, p.off) - Math.max(lo, p.on));
            if (dur > maxDur) {
              maxDur = dur;
              bestPlayer = p;
            }
          });
          if (bestPlayer) {
            activeInWindow[bestPlayer.name] = true;
          }
        });
      }

      var out = [];
      Object.keys(acc).forEach(function (name) {
        var a = acc[name], pi = info[side][name];
        if (!a.n || !pi || !(pi.on < hi && pi.off > lo)) return;   // must be on the pitch in window
        if (state.win === 0) {
          if (!pi.starter) return; // in avg position full display only starting 11
        } else {
          if (!activeInWindow[name]) return; // display only the player with more time in current window
        }
        out.push({ name: name, x: a.x / a.n, y: a.y / a.n, n: a.n, num: pi.num });
      });
      return { players: out, lo: lo, hi: hi };
    }

    function draw() {
      var net = compute();
      var col = state.side === "home" ? D.home.color : D.away.color;
      nodeG.innerHTML = "";
      var maxN = 1; net.players.forEach(function (p) { maxN = Math.max(maxN, p.n); });
      net.players.forEach(function (p) {
        var cx = tx(state.side, p.x), cy = ty(state.side, p.y);
        var r = 1.6 + 2.4 * p.n / maxN;
        var c = document.createElementNS(NS, "circle");
        c.setAttribute("cx", cx.toFixed(2)); c.setAttribute("cy", cy.toFixed(2));
        c.setAttribute("r", r.toFixed(2));
        c.setAttribute("fill", col); c.setAttribute("fill-opacity", "0.92");
        c.setAttribute("stroke", "#0b0f1a"); c.setAttribute("stroke-width", "0.3");
        (function (pl) {
          c.addEventListener("mousemove", function (e) {
            var pi = info[state.side][pl.name] || {};
            showTip(e, "<b>" + esc(pl.name) + "</b><br>" + pl.n + " touches · on pitch " + pi.on + "–" + pi.off + "'");
          });
          c.addEventListener("mouseleave", hideTip);
        })(p);
        nodeG.appendChild(c);
        if (p.num != null) {
          var t = document.createElementNS(NS, "text");
          t.setAttribute("x", cx.toFixed(2)); t.setAttribute("y", (cy + 0.9).toFixed(2));
          t.setAttribute("text-anchor", "middle"); t.setAttribute("font-size", "2.4");
          t.setAttribute("font-weight", "800"); t.setAttribute("fill", "#fff");
          t.setAttribute("pointer-events", "none");
          t.textContent = p.num;
          nodeG.appendChild(t);
        }
      });
      var teamName = state.side === "home" ? D.home.name : D.away.name;
      note.textContent = teamName + " · " + net.players.length + " players on pitch · average positions, minutes " +
        net.lo + "–" + net.hi + (state.win === 0 ? " (full)" : "");
      document.getElementById("apDir").textContent =
        (state.side === "home" ? teamName + " attacking →" : "← " + teamName + " attacking");
    }

    function setUpper(v) {
      state.upper = v;
      var lo = state.win === 0 ? 0 : Math.max(0, v - state.win);
      minLab.textContent = lo + "–" + v + "'";
      draw();
    }
    function setSide(side) {
      state.side = side;
      document.getElementById("apHome").classList.toggle("on", side === "home");
      document.getElementById("apAway").classList.toggle("on", side === "away");
      draw();
    }
    document.getElementById("apHome").addEventListener("click", function () { setSide("home"); });
    document.getElementById("apAway").addEventListener("click", function () { setSide("away"); });
    document.getElementById("apWin").addEventListener("change", function () { state.win = parseInt(this.value, 10); setUpper(state.upper); });
    var range = document.getElementById("apRange");
    range.addEventListener("input", function () { stopPlay(); setUpper(parseInt(this.value, 10)); });

    var timer = null;
    var playBtn = document.getElementById("apPlay");
    function stopPlay() { if (timer) { clearInterval(timer); timer = null; playBtn.classList.remove("playing"); playBtn.textContent = "▶"; } }
    playBtn.addEventListener("click", function () {
      if (timer) { stopPlay(); return; }
      if (state.upper >= maxMin) { range.value = 0; setUpper(0); }
      playBtn.classList.add("playing"); playBtn.textContent = "❚❚";
      timer = setInterval(function () {
        var v = state.upper + 1;
        if (v > maxMin) { stopPlay(); return; }
        range.value = v; setUpper(v);
      }, 180);
    });

    setUpper(state.upper);
  }

  /* ================= LINE-UPS ================= */
  function ratingColor(r) {
    return r >= 7.5 ? "#1a8a1a" : r >= 6.5 ? "#5b9e1e" : r >= 6.0 ? "#9aa6bd" : "#cc4400";
  }
  function buildLineups(D) {
    function badges(p) {
      var b = "";
      for (var i = 0; i < (p.g || 0); i++) b += "⚽";
      if (p.a) b += ' <span class="ast">A' + (p.a > 1 ? "×" + p.a : "") + "</span>";
      for (var y = 0; y < (p.yc || 0); y++) b += ' <span class="cardm yc"></span>';
      if (p.rc) b += ' <span class="cardm rc"></span>';
      return b;
    }
    function li(p) {
      var rt = p.rating != null
        ? '<span class="rt" style="background:' + ratingColor(p.rating) + '">' + p.rating.toFixed(1) + "</span>"
        : '<span class="rt none">–</span>';
      var mins = p.mins != null ? '<span class="mins">' + p.mins + "'</span>" : "";
      return "<li><span class='no'>" + (p.num == null ? "" : p.num) + "</span>" +
        "<span class='pname'>" + (p.motm ? "<span class='star'>★</span> " : "") + esc(p.name) +
        "<span class='pos'>" + esc(p.pos) + "</span></span>" +
        "<span class='badges'>" + badges(p) + "</span>" + mins + rt + "</li>";
    }
    function card(side, data) {
      var name = side === "home" ? D.home.name : D.away.name;
      return '<div class="lineup-card"><h4>' + esc(name) +
        '<span class="lh">Min · G/A · Rating</span></h4><ul>' +
        data.starters.map(li).join("") +
        (data.subs.length ? '<li class="subhdr">Substitutes used</li>' + data.subs.map(li).join("") : "") +
        "</ul></div>";
    }
    document.getElementById("mv-lineups").innerHTML =
      '<div class="lineups-grid">' + card("home", D.lineups.home) + card("away", D.lineups.away) + "</div>" +
      '<div class="legend-row" style="margin-top:14px">' +
        "<span>⚽ goal</span><span><b class='ast'>A</b> assist</span>" +
        '<span><i class="cardm yc"></i> yellow</span><span><i class="cardm rc"></i> red</span>' +
        "<span>rating coloured by value · min = minutes played</span></div>";
  }

  /* ================= ALL GOALS MAP ================= */
  // Opta-style per-goal build-up: shirt-number touch nodes, dotted passes, solid
  // carries/dribbles, red shot, orange move-start, grey keeper-save (from saves[]).
  // Reconstructed from the same arrays the other maps use; reuses tx()/ty()/pitchMarkup().
  function agmNorm(s) { return String(s == null ? "" : s).normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase().trim(); }
  function agmIni(n) { return n ? n.split(/\s+/).map(function (w) { return w[0]; }).join("").slice(0, 2).toUpperCase() : ""; }
  function agmT(o) { return (o.min || 0) * 60 + (o.sec || 0); }
  function agmNumMap(D) {
    var m = {};
    ["home", "away"].forEach(function (sd) {
      var lu = (D.lineups && D.lineups[sd]) || {};
      [].concat(lu.starters || [], lu.subs || []).forEach(function (p) {
        if (p && p.name != null && p.num != null) m[agmNorm(p.name)] = p.num;
      });
    });
    return m;
  }
  function buildGoalSequences(D) {
    var ev = [].concat(
      (D.passes || []).filter(function (p) { return p.ok; })
        .map(function (p) { return { k: "pass", t: agmT(p), team: p.team, x: p.x, y: p.y, ex: p.ex, ey: p.ey, player: p.player, cross: !!p.cross }; }),
      (D.dribbles || []).filter(function (d) { return d.ok; })
        .map(function (d) { return { k: "dribble", t: agmT(d), team: d.team, x: d.x, y: d.y, player: d.player }; })
    ).sort(function (a, b) { return a.t - b.t; });
    var saves = D.saves || [];
    function assistFor(shot) {
      var g = D.goals || [];
      for (var i = 0; i < g.length; i++) if (g[i].min === shot.min && agmNorm(g[i].scorer) === agmNorm(shot.player)) return g[i].assist;
      return null;
    }
    var seqs = (D.shots || []).filter(function (s) { return s.goal; }).map(function (shot) {
      var T = agmT(shot);
      var chain = ev.filter(function (e) { return e.team === shot.team && e.t <= T && e.t >= T - 35; });
      var seq = [];
      for (var i = chain.length - 1; i >= 0; i--) { if (seq.length && seq[0].t - chain[i].t > 6) break; seq.unshift(chain[i]); }
      var rebound = false;
      // Rebound: a same-team shot saved/blocked in the ~4s before the goal → splice in
      // the original effort + keeper save, in time order, before the goal node. Runs
      // whether or not passes preceded, so rebounds show even with a build-up chain.
      var prior = null, best = -1;
      (D.shots || []).forEach(function (s2) { var tt = agmT(s2); if (s2.team === shot.team && !s2.goal && tt < T && T - tt <= 4 && tt > best) { best = tt; prior = s2; } });
      if (prior) {
        rebound = true;
        seq.push({ k: "shot_eff", team: prior.team, x: prior.x, y: prior.y, player: prior.player, xg: prior.xg });
        var sv = null, sb = -1;
        saves.forEach(function (v) { var tt = agmT(v); if (v.team !== shot.team && Math.abs(tt - T) <= 2 && tt > sb) { sb = tt; sv = v; } });
        if (sv) seq.push({ k: "save", opp: true, team: sv.team, x: sv.x, y: sv.y, player: sv.player });  // graceful: omitted if no saves[]
      }
      seq.push({ k: "shot", team: shot.team, x: shot.x, y: shot.y, player: shot.player, xg: shot.xg });
      var ppl = {}; seq.forEach(function (s) { if (s.k !== "save" && s.player) ppl[agmNorm(s.player)] = 1; });
      return {
        scorer: shot.player, min: shot.min, side: shot.team, assist: assistFor(shot), rebound: rebound, steps: seq,
        players: Object.keys(ppl).length,
        passes: seq.filter(function (s) { return s.k === "pass"; }).length,
        crosses: seq.filter(function (s) { return s.k === "pass" && s.cross; }).length,
        dribbles: seq.filter(function (s) { return s.k === "dribble"; }).length, xg: shot.xg
      };
    });
    // Own goals: reconstruct the BENEFICIARY team's build-up (passes/dribbles) in the
    // seconds before the own goal, then finish on a single red "OG" node at the end they
    // were attacking (coords were mirrored into their frame in build_match_details).
    // Own-goal events carry only a minute (no second), so anchor on the end of that minute
    // to capture the attacking team's touches that forced it.
    (D.goals || []).forEach(function (g) {
      if (!g.own || g.x == null) return;
      var benef = g.team;                                      // side that benefits from the OG
      var T = (g.min || 0) * 60 + 59;
      var chain = ev.filter(function (e) { return e.team === benef && e.t <= T && e.t >= T - 30; });
      var seq = [];
      for (var k = chain.length - 1; k >= 0; k--) { if (seq.length && seq[0].t - chain[k].t > 6) break; seq.unshift(chain[k]); }
      seq.push({ k: "shot", team: benef, x: g.x, y: g.y, player: g.scorer, og: true });
      var ppl = {}; seq.forEach(function (s) { if (s.k !== "save" && s.player && !s.og) ppl[agmNorm(s.player)] = 1; });
      seqs.push({
        scorer: "Own goal", ogBy: g.scorer, min: g.min, side: benef, assist: null, rebound: false, own: true,
        steps: seq,
        players: Object.keys(ppl).length,
        passes: seq.filter(function (s) { return s.k === "pass"; }).length,
        crosses: seq.filter(function (s) { return s.k === "pass" && s.cross; }).length,
        dribbles: seq.filter(function (s) { return s.k === "dribble"; }).length, xg: null
      });
    });
    seqs.sort(function (a, b) { return (a.min || 0) - (b.min || 0); });
    return seqs;
  }
  function agmLine(x1, y1, x2, y2, cls) {
    var m = cls === "agm-pass" ? "agmAp" : (cls === "agm-shotln" ? "agmAs" : "agmAc");
    return '<line class="' + cls + '" x1="' + x1.toFixed(2) + '" y1="' + y1.toFixed(2) + '" x2="' + x2.toFixed(2) +
      '" y2="' + y2.toFixed(2) + '" marker-end="url(#' + m + ')"/>';
  }
  // A cross renders like a pass (same fine dots) but CURVED — a quadratic arc bowed
  // perpendicular to the line, so it reads as a cross while staying a pass-type delivery.
  function agmArc(x1, y1, x2, y2, cls) {
    var dx = x2 - x1, dy = y2 - y1, len = Math.hypot(dx, dy) || 1;
    var mx = (x1 + x2) / 2, my = (y1 + y2) / 2;
    var off = Math.min(7, len * 0.2);                      // bow height, capped
    var cx = mx + (-dy / len) * off, cy = my + (dx / len) * off;
    return '<path class="' + cls + '" d="M' + x1.toFixed(2) + ',' + y1.toFixed(2) +
      ' Q' + cx.toFixed(2) + ',' + cy.toFixed(2) + ' ' + x2.toFixed(2) + ',' + y2.toFixed(2) +
      '" marker-end="url(#agmAp)"/>';
  }
  function agmNode(x, y, label, title, cls) {
    var dark = (cls === "start" || cls === "shot" || cls === "save");
    return '<g><title>' + esc(title) + '</title><circle class="agm-node' + (cls ? (" " + cls) : "") +
      '" cx="' + x.toFixed(2) + '" cy="' + y.toFixed(2) + '" r="1.3"/>' +
      '<text class="agm-nt' + (dark ? " dark" : "") + '" x="' + x.toFixed(2) + '" y="' + y.toFixed(2) + '">' + esc(label) + "</text></g>";
  }
  var AGM_MAX_SEG = 52;  // pitch units (~half length); drop diagram segments longer than this (glitched source coords)
  function agmSeqSVG(seq, numMap, D) {
    var defs = '<defs>' +
      '<marker id="agmAp" markerWidth="3" markerHeight="3" refX="2.4" refY="1.5" orient="auto"><path d="M0,0 L3,1.5 L0,3 Z" class="agm-mk"/></marker>' +
      '<marker id="agmAc" markerWidth="3" markerHeight="3" refX="2.4" refY="1.5" orient="auto"><path d="M0,0 L3,1.5 L0,3 Z" class="agm-mk"/></marker>' +
      '<marker id="agmAs" markerWidth="3" markerHeight="3" refX="2.4" refY="1.5" orient="auto"><path d="M0,0 L3,1.5 L0,3 Z" class="agm-mk-shot"/></marker></defs>';
    var P = seq.steps.map(function (st) {
      var sd = st.team || seq.side;
      return { k: st.k, player: st.player, xg: st.xg, team: sd, cross: !!st.cross, og: !!st.og,
               x: tx(sd, st.x), y: ty(sd, st.y), ex: tx(sd, st.ex != null ? st.ex : st.x), ey: ty(sd, st.ey != null ? st.ey : st.y) };
    });
    var teamName = seq.side === "home" ? D.home.name : D.away.name;
    // Home attacks right (▶); away attacks left (◀) — match the rest of the dashboard.
    var dirTxt = seq.side === "home" ? (esc(teamName) + " attacking ▶") : ("◀ " + esc(teamName) + " attacking");
    var a = [defs, pitchMarkup(),
      '<text class="dir-label" x="' + (PW / 2) + '" y="' + (PH + 4) + '" text-anchor="middle">' + dirTxt + '</text>'];
    for (var i = 0; i < P.length; i++) {
      var pt = P[i];
      if (pt.k === "pass") a.push(pt.cross ? agmArc(pt.x, pt.y, pt.ex, pt.ey, "agm-cross")   // curved dotted = cross
                                           : agmLine(pt.x, pt.y, pt.ex, pt.ey, "agm-pass")); // straight dotted = pass
      if (i < P.length - 1) {
        var nx = P[i + 1];
        var fx = pt.k === "pass" ? pt.ex : pt.x, fy = pt.k === "pass" ? pt.ey : pt.y;
        var cls = (nx.k === "save") ? "agm-shotln" : "agm-carry";                   // saved effort→keeper = red; else solid carry
        var dcon = Math.hypot(nx.x - fx, nx.y - fy);
        if (dcon > 1.0 && dcon <= AGM_MAX_SEG) a.push(agmLine(fx, fy, nx.x, nx.y, cls)); // skip cross-pitch spans from bad source coords
      }
    }
    var L = P[P.length - 1];
    var gx = tx(L.team, 99.4), gy = ty(L.team, 50);
    // Only draw the shot→goal line when the shot is plausibly in the attacking half.
    // A few feeds carry a glitched goal coordinate (e.g. at the wrong end); without
    // this guard that produced a line straight across the whole pitch.
    if (Math.hypot(gx - L.x, gy - L.y) <= AGM_MAX_SEG) a.push(agmLine(L.x, L.y, gx, gy, "agm-shotln"));
    P.forEach(function (pt, i) {
      var cls = pt.k === "save" ? "save" : (pt.k === "shot" ? "shot" : (pt.k === "dribble" ? "drib" : (i === 0 ? "start" : "")));
      var num = numMap[agmNorm(pt.player)];
      var label = pt.og ? "OG" : ((num != null) ? num : (agmIni(pt.player) || (i + 1)));
      var ttl = pt.og ? ("Own goal — " + (pt.player || "")) :
        ((pt.k === "save" ? "Save — " : pt.k === "shot" ? "Goal — " : pt.k === "shot_eff" ? "Shot (saved) — " : "") +
        (pt.player || ("Touch " + (i + 1))) + (pt.k === "pass" && pt.cross ? " · cross" : "") + (pt.xg != null ? " · xG " + pt.xg.toFixed(2) : ""));
      a.push(agmNode(pt.x, pt.y, label, ttl, cls));
    });
    var ly = Math.max(3.4, L.y - 2.0);                                              // scorer name + xG ABOVE the finishing node
    a.push('<text class="agm-scorelab" x="' + L.x.toFixed(2) + '" y="' + (ly - 1.7).toFixed(2) + '">' + esc(seq.scorer) + "</text>");
    if (L.xg != null) a.push('<text class="agm-xglab" x="' + L.x.toFixed(2) + '" y="' + ly.toFixed(2) + '">xG ' + L.xg.toFixed(2) + "</text>");
    for (var j = 0; j < P.length; j++) if (P[j].k === "save") { a.push('<text class="agm-savelab" x="' + P[j].x.toFixed(2) + '" y="' + (P[j].y - 2.0).toFixed(2) + '" text-anchor="middle">SAVE</text>'); break; }
    return '<svg class="pitch-svg" viewBox="-2 -2 ' + (PW + 4) + " " + (PH + 8) + '">' + a.join("") + "</svg>";
  }
  // Self-contained <style> for export: the live SVG relies on external match.css
  // classes + CSS vars, which don't travel into a serialized/standalone SVG. Resolve
  // the theme vars to literal colours and embed the needed rules so the PNG matches.
  function agmExportStyle() {
    var rc = getComputedStyle(document.documentElement);
    function v(n, d) { var x = rc.getPropertyValue(n).trim(); return x || d; }
    var TEXT = v("--text", "#e8edf7"), BAD = v("--bad", "#ff6b81"), WARN = v("--warn", "#ffb454"),
        MUTED = v("--muted", "#93a0bd"), CARD2 = v("--card-2", "#1b2440"), BG = v("--bg", "#0b0f1a");
    return "<style>" +
      ".pitch-bg{fill:#14361f}.pitch-line{fill:none;stroke:rgba(255,255,255,.28);stroke-width:.3}" +
      ".dir-label{fill:rgba(255,255,255,.5);font-size:2.4px;font-weight:700;font-family:sans-serif}" +
      ".agm-pass{stroke:" + TEXT + ";stroke-width:.5;fill:none;stroke-linecap:round;stroke-dasharray:.15 1.05}" +
      ".agm-cross{stroke:" + TEXT + ";stroke-width:.5;fill:none;stroke-linecap:round;stroke-dasharray:.15 1.05}" +
      ".agm-carry{stroke:" + TEXT + ";stroke-width:.34;fill:none}" +
      ".agm-shotln{stroke:" + BAD + ";stroke-width:.45;fill:none}" +
      ".agm-mk{fill:" + TEXT + "}.agm-mk-shot{fill:" + BAD + "}" +
      ".agm-node{fill:" + CARD2 + ";stroke:" + TEXT + ";stroke-width:.22}" +
      ".agm-node.start{fill:" + WARN + ";stroke:#c98a2e}.agm-node.shot{fill:" + BAD + ";stroke:#c9455a}.agm-node.save{fill:" + MUTED + ";stroke:#6b7488}" +
      ".agm-nt{font-size:1.35px;font-weight:700;fill:" + TEXT + ";text-anchor:middle;dominant-baseline:central;font-family:sans-serif}.agm-nt.dark{fill:" + BG + "}" +
      ".agm-scorelab{font-size:2px;font-weight:700;fill:" + TEXT + ";text-anchor:middle;font-family:sans-serif}" +
      ".agm-xglab{font-size:1.7px;font-weight:700;fill:" + BAD + ";text-anchor:middle;font-family:sans-serif}" +
      ".agm-savelab{font-size:1.5px;font-weight:600;fill:" + MUTED + ";text-anchor:middle;font-family:sans-serif}" +
      "</style>";
  }
  function agmSanitize(s) { return String(s || "").replace(/[^A-Za-z0-9_]+/g, "_").replace(/^_+|_+$/g, ""); }
  function agmFmtDate(s) {
    var m = /^(\d{4})-(\d{2})-(\d{2})/.exec(s || ""); if (!m) return s || "";
    var mo = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
    return (+m[3]) + " " + mo[(+m[2]) - 1] + " " + m[1];
  }
  // Export one goal's SVG to a PNG (dependency-free) with an "Rshiri" watermark.
  function exportGoalPNG(svgEl, g, D, btn) {
    try {
      var rc = getComputedStyle(document.documentElement);
      function cvar(n, d) { var x = rc.getPropertyValue(n).trim(); return x || d; }
      var bg = cvar("--card", "#161d31"), text = cvar("--text", "#e8edf7"), muted = cvar("--muted", "#93a0bd"),
          bad = cvar("--bad", "#ff6b81"), line = cvar("--line", "#26304d");
      var F = "-apple-system,'Segoe UI',Arial,sans-serif";
      var clone = svgEl.cloneNode(true);
      clone.setAttribute("xmlns", "http://www.w3.org/2000/svg");
      clone.insertAdjacentHTML("afterbegin", agmExportStyle());   // embed resolved styles
      var scale = 12, W = (PW + 4) * scale, SH = (PH + 8) * scale; // svg (pitch) area
      var band = Math.round(W * 0.11);                            // header band height
      var H = SH + band;
      clone.setAttribute("width", W); clone.setAttribute("height", SH);
      var svgStr = new XMLSerializer().serializeToString(clone);
      var url = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(svgStr);
      var img = new Image();
      img.onload = function () {
        var cv = document.createElement("canvas"); cv.width = W; cv.height = H;
        var ctx = cv.getContext("2d");
        ctx.fillStyle = bg; ctx.fillRect(0, 0, W, H);            // dark card background
        var pad = Math.round(W * 0.022);
        // ---- header band ----
        var hn = (D.home && D.home.name) || "Home", an = (D.away && D.away.name) || "Away";
        var hs = (D.home && D.home.score != null) ? D.home.score : "", as = (D.away && D.away.score != null) ? D.away.score : "";
        var title = hn + "  " + hs + "–" + as + "  " + an;   // en-dash score
        var sub = [(D.stage || "").trim(), agmFmtDate(D.date)].filter(Boolean).join("   ·   ");
        ctx.textAlign = "left"; ctx.textBaseline = "alphabetic";
        ctx.fillStyle = text; ctx.font = "bold " + Math.round(band * 0.32) + "px " + F;
        ctx.fillText(title, pad, Math.round(band * 0.40));
        ctx.fillStyle = muted; ctx.font = Math.round(band * 0.20) + "px " + F;
        ctx.fillText(sub, pad, Math.round(band * 0.74));
        // scorer · minute (right side of band, accent red)
        ctx.textAlign = "right"; ctx.fillStyle = bad; ctx.font = "bold " + Math.round(band * 0.24) + "px " + F;
        ctx.fillText("⚽ " + (g.scorer || "") + "  " + g.min + "'", W - pad, Math.round(band * 0.55));
        // divider under band
        ctx.strokeStyle = line; ctx.lineWidth = 2; ctx.beginPath(); ctx.moveTo(0, band - 1); ctx.lineTo(W, band - 1); ctx.stroke();
        // ---- pitch diagram below the band ----
        ctx.drawImage(img, 0, band, W, SH);
        // ---- credit, bottom-right corner ----
        ctx.textAlign = "right"; ctx.textBaseline = "bottom";
        ctx.fillStyle = "rgba(255,255,255,0.5)"; ctx.font = "bold " + Math.round(W * 0.017) + "px " + F;
        ctx.fillText("All rights reserved to @RShiri", W - pad, H - Math.round(W * 0.012));
        var fname = agmSanitize((D.id || "match") + "_" + (g.scorer || "goal") + "_" + g.min) + ".png";
        cv.toBlob(function (blob) {
          var a = document.createElement("a"); a.download = fname; a.href = URL.createObjectURL(blob);
          document.body.appendChild(a); a.click();
          setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 200);
          if (btn) { btn.textContent = "✓ Saved"; setTimeout(function () { btn.textContent = "⤓ Download PNG"; }, 1500); }
        }, "image/png");
      };
      img.onerror = function () { if (btn) btn.textContent = "Export failed"; };
      img.src = url;
    } catch (e) { if (btn) btn.textContent = "Export failed"; }
  }
  function buildAllGoals(D) {
    var host = document.getElementById("mv-goals");
    if (!host) return;
    var seqs = buildGoalSequences(D);
    if (!seqs.length) { if (host.parentNode) host.parentNode.style.display = "none"; return; }  // 0–0 etc → render nothing
    var numMap = agmNumMap(D);
    function meta(g) {
      if (g.own) {
        var ohas = (g.passes + g.dribbles) > 0;
        var olead = ohas
          ? g.players + " player" + (g.players === 1 ? "" : "s") + " · " + g.passes + " pass" + (g.passes === 1 ? "" : "es") +
            (g.dribbles ? " · " + g.dribbles + " dribble" + (g.dribbles === 1 ? "" : "s") : "")
          : "Own goal";
        return '<div class="agm-meta"><span class="agm-pill lead">' + olead + "</span>" +
          (g.crosses ? '<span class="agm-pill">' + g.crosses + " cross" + (g.crosses === 1 ? "" : "es") + "</span>" : "") +
          '<span class="agm-pill">forced own goal by <b>' + esc(g.ogBy || "") + "</b></span></div>";
      }
      var counts = g.players + " player" + (g.players === 1 ? "" : "s") + " · " + g.passes + " pass" + (g.passes === 1 ? "" : "es") +
        " · " + g.dribbles + " dribble" + (g.dribbles === 1 ? "" : "s");
      var assist = g.assist ? '<span class="agm-pill">assist <b>' + esc(g.assist) + "</b></span>" : '<span class="agm-pill">unassisted</span>';
      var reb = g.rebound ? '<span class="agm-pill">rebound</span>' : "";
      var crs = g.crosses ? '<span class="agm-pill">' + g.crosses + " cross" + (g.crosses === 1 ? "" : "es") + "</span>" : "";
      return '<div class="agm-meta"><span class="agm-pill lead">' + counts + '</span><span class="agm-pill">xG <b>' +
        (g.xg != null ? g.xg.toFixed(2) : "—") + "</b></span>" + crs + assist + reb + "</div>";
    }
    function roster(g) {
      if (g.own) {
        var oseen = [], onum = {};
        g.steps.forEach(function (st) { if (!st.og && st.k !== "save" && st.player && oseen.indexOf(st.player) < 0) { oseen.push(st.player); onum[st.player] = numMap[agmNorm(st.player)]; } });
        var obuild = oseen.length
          ? "<b>Build-up:</b> " + oseen.map(function (n) { return esc(n) + (onum[n] != null ? " (#" + onum[n] + ")" : ""); }).join(" · ") + " · "
          : "";
        return '<div class="agm-roster">' + obuild + "<b>Own goal</b> by " + esc(g.ogBy || "") + "</div>";
      }
      var seen = [], num = {};
      g.steps.forEach(function (st) { if (st.k !== "save" && st.player && seen.indexOf(st.player) < 0) { seen.push(st.player); num[st.player] = numMap[agmNorm(st.player)]; } });
      var sv = null; g.steps.forEach(function (s) { if (s.k === "save") sv = s; });
      var sx = sv ? " · saved by " + esc(sv.player) : "";
      return '<div class="agm-roster"><b>Players in move:</b> ' +
        seen.map(function (n) { return esc(n) + (num[n] != null ? " (#" + num[n] + ")" : ""); }).join(" · ") + sx + "</div>";
    }
    var legend = '<div class="agm-legend">' +
      '<span class="it"><span class="agm-lz">7</span>Touch (shirt #)</span>' +
      '<span class="it"><span class="agm-lz start"></span>Move start</span>' +
      '<span class="it"><span class="agm-lz save"></span>Keeper save</span>' +
      '<span class="it"><span class="agm-lz shot"></span>Shot (xG)</span>' +
      '<span class="it"><span class="agm-lln"></span>Pass</span>' +
      '<span class="it"><svg class="agm-crosslg" width="20" height="9" viewBox="0 0 20 9"><path d="M1,7 Q10,0 19,7"/></svg>Cross</span>' +
      '<span class="it"><span class="agm-lln carry"></span>Carry / dribble</span>' +
      '<span class="it"><span class="agm-lln shot"></span>Shot</span></div>';
    var tabs = '<div class="agm-tabs">' + seqs.map(function (g, i) {
      var col = g.side === "home" ? D.home.color : D.away.color;
      return '<button class="agm-tab" data-i="' + i + '"><span class="sw" style="background:' + col + '"></span><span class="mm">' +
        g.min + "'</span> " + esc(g.scorer) + "</button>";
    }).join("") + "</div>";
    host.innerHTML = tabs + '<div id="agm-feat"></div>';
    var feat = document.getElementById("agm-feat");
    function sel(i) {
      [].forEach.call(host.querySelectorAll(".agm-tab"), function (b, j) { b.classList.toggle("active", j === i); });
      var g = seqs[i];
      feat.innerHTML = meta(g) +
        '<div class="agm-actions"><button type="button" class="agm-dl">⤓ Download PNG</button></div>' +
        roster(g) + '<div class="pitch-wrap">' + agmSeqSVG(g, numMap, D) + "</div>" + legend;
      var btn = feat.querySelector(".agm-dl");
      if (btn) btn.addEventListener("click", function () {
        exportGoalPNG(feat.querySelector("svg.pitch-svg"), g, D, btn);
      });
    }
    host.querySelector(".agm-tabs").addEventListener("click", function (e) {
      var b = e.target.closest(".agm-tab"); if (!b) return; sel(+b.getAttribute("data-i"));
    });
    var def = 0; seqs.forEach(function (g, i) { if (g.players > seqs[def].players) def = i; });  // open on the richest move
    sel(def);
  }

  /* ================= ANIMATED GOAL REPLAYS ================= */
  // A "movie" version of the All Goals Map: the ball physically travels the build-up —
  // gliding along passes, curving on crosses, carrying on dribbles, then firing the shot
  // into the net. Reuses the SAME geometry (tx/ty, pitchMarkup, buildGoalSequences) and
  // CSS classes as the static map, so the still frame at rest IS the All Goals Map.
  var AGM_ANIM_LABEL = { pass: "Pass", cross: "Cross", carry: "Carry / dribble", shotln: "Shot", shot: "Shot" };
  function agmClamp(v, a, b) { return Math.max(a, Math.min(b, v)); }
  function agmSegClass(t) { return t === "pass" ? "agm-pass" : t === "cross" ? "agm-cross" : t === "carry" ? "agm-carry" : "agm-shotln"; }
  function agmSegMarker(t) { return t === "pass" ? "agmAp2" : t === "cross" ? "agmAc2" : t === "carry" ? "agmAc2" : "agmAs2"; }
  function agmSegDpath(m) {
    if (m.type === "cross") {
      var dx = m.x2 - m.x1, dy = m.y2 - m.y1, len = Math.hypot(dx, dy) || 1, off = Math.min(7, len * 0.2);
      var mx = (m.x1 + m.x2) / 2, my = (m.y1 + m.y2) / 2, cx = mx + (-dy / len) * off, cy = my + (dx / len) * off;
      return "M" + m.x1.toFixed(2) + "," + m.y1.toFixed(2) + " Q" + cx.toFixed(2) + "," + cy.toFixed(2) + " " + m.x2.toFixed(2) + "," + m.y2.toFixed(2);
    }
    return "M" + m.x1.toFixed(2) + "," + m.y1.toFixed(2) + " L" + m.x2.toFixed(2) + "," + m.y2.toFixed(2);
  }
  // Order the goal's steps into ball-travel "moves": node→(pass)→pass-end→(carry)→next
  // node … →(shot)→goal. Mirrors agmSeqSVG's segment logic so the path matches exactly.
  function agmJourney(P) {
    var mv = [];
    for (var i = 0; i < P.length; i++) {
      var pt = P[i];
      if (pt.k === "pass") {
        mv.push({ type: pt.cross ? "cross" : "pass", x1: pt.x, y1: pt.y, x2: pt.ex, y2: pt.ey, litNode: null });
        if (i < P.length - 1) {
          var nx = P[i + 1], L = Math.hypot(nx.x - pt.ex, nx.y - pt.ey);
          mv.push({ type: nx.k === "save" ? "shotln" : "carry", x1: pt.ex, y1: pt.ey, x2: nx.x, y2: nx.y, litNode: i + 1, hidden: !(L > 1.0 && L <= AGM_MAX_SEG) });
        }
      } else if (i < P.length - 1) {
        var nx2 = P[i + 1], L2 = Math.hypot(nx2.x - pt.x, nx2.y - pt.y);
        mv.push({ type: nx2.k === "save" ? "shotln" : "carry", x1: pt.x, y1: pt.y, x2: nx2.x, y2: nx2.y, litNode: i + 1, hidden: !(L2 > 1.0 && L2 <= AGM_MAX_SEG) });
      }
    }
    var Lp = P[P.length - 1], gx = tx(Lp.team, 99.4), gy = ty(Lp.team, 50), sl = Math.hypot(gx - Lp.x, gy - Lp.y);
    mv.push({ type: "shot", x1: Lp.x, y1: Lp.y, x2: gx, y2: gy, litNode: null, hidden: !(sl <= AGM_MAX_SEG) });
    mv.forEach(function (m) {
      var len = Math.hypot(m.x2 - m.x1, m.y2 - m.y1);
      if (m.hidden) { m.dur = 160; m.dwell = 0; }
      else if (m.type === "pass" || m.type === "cross") { m.dur = agmClamp(380 + len * 9, 380, 950); m.dwell = 150; }
      else if (m.type === "shot") { m.dur = 470; m.dwell = 0; }
      else if (m.type === "shotln") { m.dur = 380; m.dwell = 120; }
      else { m.dur = agmClamp(360 + len * 30, 360, 1500); m.dwell = 220; }   // carry/dribble — deliberately slower than a pass (you can't dribble at pass speed)
    });
    return mv;
  }
  // Generic rAF driver. R = { node(i), seg(i,state), action(type), ball(x,y), trail(pts[]) }.
  // Returns a cancel fn. Used by BOTH the live SVG and the canvas video export.
  function agmAnimateMove(mv, speed, R, done) {
    var i = 0, t0 = performance.now(), arrived = false, trail = [], raf = null;
    R.node(0);
    function lerp(m, f) { return { x: m.x1 + (m.x2 - m.x1) * f, y: m.y1 + (m.y2 - m.y1) * f }; }
    function loop(now) {
      var m = mv[i], dur = Math.max(60, m.dur / speed), e = now - t0, f = Math.min(e, dur) / dur;
      var pos = m.el ? m.el.getPointAtLength(f * m.len) : lerp(m, f);
      R.seg(i, true); R.action(m.type); R.ball(pos.x, pos.y);
      trail.push([pos.x, pos.y]); if (trail.length > 16) trail.shift(); R.trail(trail);
      if (e >= dur && !arrived) { arrived = true; if (m.litNode != null) R.node(m.litNode); R.seg(i, "done"); }
      if (e >= dur + (m.dwell || 0) / speed) { i++; arrived = false; t0 = now; if (i >= mv.length) { done(); return; } }
      raf = requestAnimationFrame(loop);
    }
    raf = requestAnimationFrame(loop);
    return function () { if (raf) cancelAnimationFrame(raf); };
  }
  function agmBuildAnimSVG(seq, numMap, D) {
    function E(n, a) { var e = document.createElementNS("http://www.w3.org/2000/svg", n); if (a) for (var k in a) e.setAttribute(k, a[k]); return e; }
    var P = seq.steps.map(function (st) {
      var sd = st.team || seq.side;
      return { k: st.k, player: st.player, xg: st.xg, team: sd, cross: !!st.cross, og: !!st.og,
               x: tx(sd, st.x), y: ty(sd, st.y), ex: tx(sd, st.ex != null ? st.ex : st.x), ey: ty(sd, st.ey != null ? st.ey : st.y) };
    });
    var mv = agmJourney(P);
    var teamName = seq.side === "home" ? D.home.name : D.away.name;
    var dirTxt = seq.side === "home" ? (esc(teamName) + " attacking ▶") : ("◀ " + esc(teamName) + " attacking");
    var defs = '<defs>' +
      '<marker id="agmAp2" markerWidth="3" markerHeight="3" refX="2.4" refY="1.5" orient="auto"><path d="M0,0 L3,1.5 L0,3 Z" class="agm-mk"/></marker>' +
      '<marker id="agmAc2" markerWidth="3" markerHeight="3" refX="2.4" refY="1.5" orient="auto"><path d="M0,0 L3,1.5 L0,3 Z" class="agm-mk"/></marker>' +
      '<marker id="agmAs2" markerWidth="3" markerHeight="3" refX="2.4" refY="1.5" orient="auto"><path d="M0,0 L3,1.5 L0,3 Z" class="agm-mk-shot"/></marker></defs>';
    var L = P[P.length - 1], ly = Math.max(3.4, L.y - 2.0), labels = "";
    labels += '<text class="agm-scorelab" x="' + L.x.toFixed(2) + '" y="' + (ly - 1.7).toFixed(2) + '">' + esc(seq.scorer) + "</text>";
    if (L.xg != null) labels += '<text class="agm-xglab" x="' + L.x.toFixed(2) + '" y="' + ly.toFixed(2) + '">xG ' + L.xg.toFixed(2) + "</text>";
    for (var j = 0; j < P.length; j++) if (P[j].k === "save") { labels += '<text class="agm-savelab" x="' + P[j].x.toFixed(2) + '" y="' + (P[j].y - 2.0).toFixed(2) + '" text-anchor="middle">SAVE</text>'; break; }
    var svg = E("svg", { class: "pitch-svg", viewBox: "-2 -2 " + (PW + 4) + " " + (PH + 8) });
    var gPitch = E("g", {});
    gPitch.innerHTML = defs + pitchMarkup() +
      '<text class="dir-label" x="' + (PW / 2) + '" y="' + (PH + 4) + '" text-anchor="middle">' + dirTxt + "</text>" + labels;
    svg.appendChild(gPitch);
    mv.forEach(function (m) {
      if (m.hidden) { m.len = Math.hypot(m.x2 - m.x1, m.y2 - m.y1); m.el = null; return; }
      var p = E("path", { class: agmSegClass(m.type), d: agmSegDpath(m), "marker-end": "url(#" + agmSegMarker(m.type) + ")" });
      svg.appendChild(p); m.el = p; m.len = p.getTotalLength();
    });
    var nodeEls = P.map(function (pt, i) {
      var cls = pt.k === "save" ? "save" : (pt.k === "shot" ? "shot" : (pt.k === "dribble" ? "drib" : (i === 0 ? "start" : "")));
      var num = numMap[agmNorm(pt.player)], label = pt.og ? "OG" : ((num != null) ? num : (agmIni(pt.player) || (i + 1)));
      var dark = (cls === "start" || cls === "shot" || cls === "save");
      var ttl = pt.og ? ("Own goal — " + (pt.player || "")) :
        ((pt.k === "save" ? "Save — " : pt.k === "shot" ? "Goal — " : pt.k === "shot_eff" ? "Shot (saved) — " : "") +
        (pt.player || ("Touch " + (i + 1))) + (pt.k === "pass" && pt.cross ? " · cross" : "") + (pt.xg != null ? " · xG " + pt.xg.toFixed(2) : ""));
      var g = E("g", {});
      var t = E("title"); t.textContent = ttl; g.appendChild(t);
      g.appendChild(E("circle", { class: "agm-node" + (cls ? " " + cls : ""), cx: pt.x.toFixed(2), cy: pt.y.toFixed(2), r: 1.3 }));
      var tx2 = E("text", { class: "agm-nt" + (dark ? " dark" : ""), x: pt.x.toFixed(2), y: pt.y.toFixed(2) }); tx2.textContent = label;
      g.appendChild(tx2); svg.appendChild(g); return g;
    });
    var trail = E("polyline", { class: "agm-trail", points: "" }); svg.appendChild(trail);
    var ball = E("circle", { class: "agm-ball", cx: P[0].x.toFixed(2), cy: P[0].y.toFixed(2), r: 1.45 }); ball.style.opacity = "0"; svg.appendChild(ball);
    var glast = mv[mv.length - 1], gx = glast.x2, gy = glast.y2;
    var goalText = E("text", { class: "agm-goalflash", x: (seq.side === "home" ? gx - 10 : gx + 10).toFixed(2), y: Math.max(6, gy - 7).toFixed(2), "text-anchor": "middle" });
    goalText.textContent = "Goal!"; goalText.style.opacity = "0"; svg.appendChild(goalText);

    // Scorer-as-player: the finishing node (last shot touch) runs ONTO the ball — i.e. it
    // travels with the ball through the carry that delivers play to the scorer, then plants
    // and shoots. Skipped for own goals (no real scorer movement to show).
    var scorerIdx = P.length - 1, moveScorer = scorerIdx > 0 && !P[scorerIdx].og, carryMoveIdx = null;
    for (var mi = 0; mi < mv.length; mi++) if (mv[mi].litNode === scorerIdx) { carryMoveIdx = mi; break; }
    if (carryMoveIdx == null) moveScorer = false;
    var scorerG = nodeEls[scorerIdx], restX = P[scorerIdx].x, restY = P[scorerIdx].y;
    if (moveScorer) svg.appendChild(scorerG);   // keep the scorer marker above the ball while it runs with it

    var api = { svg: svg, mv: mv, P: P, gx: gx, gy: gy, onAction: null, onDone: null, _cancel: null };
    function goalFlash() { var t0 = performance.now(); (function f(now) { var e = (now - t0) / 600; if (e >= 1) { goalText.style.opacity = "1"; return; } goalText.style.opacity = String(e); requestAnimationFrame(f); })(performance.now()); }
    api.stop = function () { if (api._cancel) { api._cancel(); api._cancel = null; } };
    api.restState = function () {
      api.stop();
      mv.forEach(function (m) { if (m.el) m.el.style.opacity = "1"; });
      nodeEls.forEach(function (g) { g.style.opacity = "1"; g.removeAttribute("transform"); });
      ball.style.opacity = "0"; trail.setAttribute("points", ""); goalText.style.opacity = "0";
    };
    api.play = function (speed) {
      api.stop();
      mv.forEach(function (m) { if (m.el) m.el.style.opacity = "0.16"; });
      nodeEls.forEach(function (g, i) { g.style.opacity = i === 0 ? "1" : "0.28"; g.removeAttribute("transform"); });
      if (moveScorer) scorerG.style.opacity = "0";   // scorer appears only once they get on the ball
      goalText.style.opacity = "0"; trail.setAttribute("points", "");
      ball.style.opacity = "1"; ball.setAttribute("cx", P[0].x.toFixed(2)); ball.setAttribute("cy", P[0].y.toFixed(2));
      var curMove = -1;
      var R = {
        node: function (i) { if (nodeEls[i]) nodeEls[i].style.opacity = "1"; },
        seg: function (i, st) { curMove = i; if (mv[i].el) mv[i].el.style.opacity = (st === "done") ? "0.6" : "1"; },
        action: function (t) { if (api.onAction) api.onAction(t); },
        ball: function (x, y) {
          ball.setAttribute("cx", x.toFixed(2)); ball.setAttribute("cy", y.toFixed(2));
          if (moveScorer && curMove === carryMoveIdx) {   // scorer runs onto the ball and carries it to the shot
            scorerG.style.opacity = "1";
            scorerG.setAttribute("transform", "translate(" + (x - restX).toFixed(2) + "," + (y - restY).toFixed(2) + ")");
          }
        },
        trail: function (pts) { trail.setAttribute("points", pts.map(function (p) { return p[0].toFixed(2) + "," + p[1].toFixed(2); }).join(" ")); }
      };
      api._cancel = agmAnimateMove(mv, speed, R, function () { api._cancel = null; if (moveScorer) scorerG.removeAttribute("transform"); goalFlash(); if (api.onDone) api.onDone(); });
    };
    api.restState();
    return api;
  }
  // Record the replay to a WebM video (dependency-free: MediaRecorder + canvas.captureStream).
  // Backdrop = the canonical static All Goals Map (agmSeqSVG) rasterised once; the ball +
  // comet trail are drawn per frame on top, with the same header band + credit as the PNG.
  function exportGoalVideo(anim, seq, g, D, numMap, btn, speed) {
    var canRec = window.MediaRecorder && typeof document.createElement("canvas").captureStream === "function";
    if (!canRec) { if (btn) btn.textContent = "Saving PNG…"; exportGoalPNG(anim.svg, g, D, btn); return; }
    try {
      var rc = getComputedStyle(document.documentElement);
      function cvar(n, d) { var x = rc.getPropertyValue(n).trim(); return x || d; }
      var bg = cvar("--card", "#161d31"), text = cvar("--text", "#e8edf7"), muted = cvar("--muted", "#93a0bd"),
          bad = cvar("--bad", "#ff6b81"), line = cvar("--line", "#26304d");
      var F = "-apple-system,'Segoe UI',Arial,sans-serif";
      var wrap = document.createElement("div"); wrap.innerHTML = agmSeqSVG(seq, numMap, D);
      var s = wrap.firstChild; s.setAttribute("xmlns", "http://www.w3.org/2000/svg");
      s.insertAdjacentHTML("afterbegin", agmExportStyle());
      var scale = 10, W = (PW + 4) * scale, SH = (PH + 8) * scale, band = Math.round(W * 0.11), H = SH + band;
      s.setAttribute("width", W); s.setAttribute("height", SH);
      var url = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(new XMLSerializer().serializeToString(s));
      var img = new Image();
      img.onerror = function () { if (btn) btn.textContent = "Export failed"; };
      img.onload = function () {
        var cv = document.createElement("canvas"); cv.width = W; cv.height = H;
        var ctx = cv.getContext("2d");
        function MX(x) { return (x + 2) * scale; } function MY(y) { return band + (y + 2) * scale; }
        function drawBase() {
          ctx.fillStyle = bg; ctx.fillRect(0, 0, W, H);
          var pad = Math.round(W * 0.022);
          var hn = (D.home && D.home.name) || "Home", an = (D.away && D.away.name) || "Away";
          var hs = (D.home && D.home.score != null) ? D.home.score : "", as = (D.away && D.away.score != null) ? D.away.score : "";
          ctx.textAlign = "left"; ctx.textBaseline = "alphabetic";
          ctx.fillStyle = text; ctx.font = "bold " + Math.round(band * 0.32) + "px " + F;
          ctx.fillText(hn + "  " + hs + "–" + as + "  " + an, pad, Math.round(band * 0.40));
          ctx.fillStyle = muted; ctx.font = Math.round(band * 0.20) + "px " + F;
          ctx.fillText([(D.stage || "").trim(), agmFmtDate(D.date)].filter(Boolean).join("   ·   "), pad, Math.round(band * 0.74));
          ctx.textAlign = "right"; ctx.fillStyle = bad; ctx.font = "bold " + Math.round(band * 0.24) + "px " + F;
          ctx.fillText("⚽ " + (g.scorer || "") + "  " + g.min + "'", W - pad, Math.round(band * 0.55));
          ctx.strokeStyle = line; ctx.lineWidth = 2; ctx.beginPath(); ctx.moveTo(0, band - 1); ctx.lineTo(W, band - 1); ctx.stroke();
          ctx.drawImage(img, 0, band, W, SH);
          ctx.textAlign = "right"; ctx.textBaseline = "bottom";
          ctx.fillStyle = "rgba(255,255,255,0.5)"; ctx.font = "bold " + Math.round(W * 0.017) + "px " + F;
          ctx.fillText("All rights reserved to @RShiri", W - pad, H - Math.round(W * 0.012));
        }
        function drawBall(pos, trail) {
          if (trail && trail.length > 1) {
            ctx.strokeStyle = "#fff7c2"; ctx.lineWidth = 0.7 * scale; ctx.lineJoin = "round"; ctx.lineCap = "round"; ctx.globalAlpha = 0.6;
            ctx.beginPath(); trail.forEach(function (p, k) { var X = MX(p[0]), Y = MY(p[1]); if (k) ctx.lineTo(X, Y); else ctx.moveTo(X, Y); }); ctx.stroke(); ctx.globalAlpha = 1;
          }
          ctx.fillStyle = "#fff"; ctx.strokeStyle = bg; ctx.lineWidth = 0.3 * scale;
          ctx.beginPath(); ctx.arc(MX(pos[0]), MY(pos[1]), 1.45 * scale, 0, 6.2832); ctx.fill(); ctx.stroke();
        }
        drawBase();
        var stream = cv.captureStream(30);
        var mime = ["video/webm;codecs=vp9", "video/webm;codecs=vp8", "video/webm"].filter(function (m) { return MediaRecorder.isTypeSupported(m); })[0] || "video/webm";
        var rec = new MediaRecorder(stream, { mimeType: mime, videoBitsPerSecond: 4000000 }), chunks = [];
        rec.ondataavailable = function (e) { if (e.data && e.data.size) chunks.push(e.data); };
        rec.onstop = function () {
          var blob = new Blob(chunks, { type: mime });
          var fname = agmSanitize((D.id || "match") + "_" + (g.scorer || "goal") + "_" + g.min) + ".webm";
          var a = document.createElement("a"); a.download = fname; a.href = URL.createObjectURL(blob);
          document.body.appendChild(a); a.click();
          setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 200);
          if (btn) { btn.textContent = "✓ Saved"; setTimeout(function () { btn.textContent = "⤓ Download video"; }, 1800); }
        };
        rec.start();
        var cur = [anim.P[0].x, anim.P[0].y], curTrail = [];
        var R = {
          node: function () {}, seg: function () {}, action: function () {},
          ball: function (x, y) { cur = [x, y]; },
          trail: function (pts) { curTrail = pts.slice(); drawBase(); drawBall(cur, curTrail); }
        };
        agmAnimateMove(anim.mv, speed, R, function () {
          var t0 = performance.now();
          (function gf(now) {
            var e = (now - t0) / 800; drawBase(); drawBall([anim.gx, anim.gy], curTrail);
            ctx.textAlign = "center"; ctx.textBaseline = "middle"; ctx.globalAlpha = Math.min(1, e * 1.4);
            ctx.fillStyle = bad; ctx.font = "bold " + Math.round(band * 0.55) + "px " + F;
            ctx.fillText("Goal!", MX(anim.gx) + (seq.side === "home" ? -10 * scale : 10 * scale), MY(anim.gy) - 7 * scale);
            ctx.globalAlpha = 1;
            if (e < 1) requestAnimationFrame(gf); else setTimeout(function () { try { rec.stop(); } catch (z) {} }, 140);
          })(performance.now());
        });
        if (btn) btn.textContent = "● Recording…";
      };
      img.src = url;
    } catch (e) { if (btn) btn.textContent = "Export failed"; }
  }
  function buildGoalReplays(D) {
    var host = document.getElementById("mv-goals-anim");
    if (!host) return;
    var seqs = buildGoalSequences(D);
    if (!seqs.length) { if (host.parentNode) host.parentNode.style.display = "none"; return; }
    var numMap = agmNumMap(D);
    var legend = '<div class="agm-legend">' +
      '<span class="it"><span class="agm-lz">7</span>Touch (shirt #)</span>' +
      '<span class="it"><span class="agm-lz start"></span>Move start</span>' +
      '<span class="it"><span class="agm-lz shot"></span>Shot (xG)</span>' +
      '<span class="it"><span class="agm-lln"></span>Pass</span>' +
      '<span class="it"><svg class="agm-crosslg" width="20" height="9" viewBox="0 0 20 9"><path d="M1,7 Q10,0 19,7"/></svg>Cross</span>' +
      '<span class="it"><span class="agm-lln carry"></span>Carry / dribble</span>' +
      '<span class="it"><span class="agm-lln shot"></span>Shot</span></div>';
    function metaLine(g) {
      var counts = g.players + " player" + (g.players === 1 ? "" : "s") + " · " + g.passes + " pass" + (g.passes === 1 ? "" : "es") +
        " · " + g.dribbles + " dribble" + (g.dribbles === 1 ? "" : "s");
      return '<div class="agm-meta"><span class="agm-pill lead">' + counts + '</span>' +
        (g.xg != null ? '<span class="agm-pill">xG <b>' + g.xg.toFixed(2) + "</b></span>" : "") +
        (g.assist ? '<span class="agm-pill">assist <b>' + esc(g.assist) + "</b></span>" : "") + "</div>";
    }
    var tabs = '<div class="agm-tabs">' + seqs.map(function (g, i) {
      var col = g.side === "home" ? D.home.color : D.away.color;
      return '<button class="agm-tab" data-i="' + i + '"><span class="sw" style="background:' + col + '"></span><span class="mm">' +
        g.min + "'</span> " + esc(g.scorer) + "</button>";
    }).join("") + "</div>";
    host.innerHTML = tabs + '<div id="agm-anim-feat"></div>';
    var feat = document.getElementById("agm-anim-feat");
    var current = null;
    function sel(i) {
      [].forEach.call(host.querySelectorAll(".agm-tab"), function (b, j) { b.classList.toggle("active", j === i); });
      if (current) current.stop();
      var g = seqs[i];
      feat.innerHTML = metaLine(g) +
        '<div class="agm-anim-bar">' +
          '<button type="button" class="agm-dl agm-play">▶ Play</button>' +
          '<span class="spd">Speed <input type="range" class="agm-spd" min="0.5" max="2" step="0.5" value="1"><b class="agm-spdv">1×</b></span>' +
          '<button type="button" class="agm-dl agm-vid">⤓ Download video</button>' +
          '<span class="agm-action">Press play</span>' +
        "</div>" +
        '<div class="pitch-wrap"></div>' + legend;
      var anim = agmBuildAnimSVG(g, numMap, D);
      current = anim;
      var actionEl = feat.querySelector(".agm-action");
      anim.onAction = function (t) { actionEl.innerHTML = "now: <b>" + (AGM_ANIM_LABEL[t] || t) + "</b>"; };
      anim.onDone = function () { actionEl.innerHTML = "now: <b>Goal!</b>"; };
      feat.querySelector(".pitch-wrap").appendChild(anim.svg);
      var spd = feat.querySelector(".agm-spd"), spdv = feat.querySelector(".agm-spdv");
      var speed = 1;
      spd.addEventListener("input", function () { speed = parseFloat(spd.value); spdv.textContent = speed + "×"; });
      var playBtn = feat.querySelector(".agm-play");
      playBtn.addEventListener("click", function () { playBtn.textContent = "↻ Replay"; anim.play(speed); });
      var vidBtn = feat.querySelector(".agm-vid");
      vidBtn.addEventListener("click", function () { exportGoalVideo(anim, g, g, D, numMap, vidBtn, speed); });
    }
    host.querySelector(".agm-tabs").addEventListener("click", function (e) {
      var b = e.target.closest(".agm-tab"); if (!b) return; sel(+b.getAttribute("data-i"));
    });
    var def = 0; seqs.forEach(function (g, i) { if (g.players > seqs[def].players) def = i; });
    sel(def);
  }

  /* ================= PENALTY SHOOTOUT ================= */
  // A goal-mouth "behind the net" view: every shootout kick plotted where it landed
  // in the goal frame (WhoScored GoalMouthY/Z), coloured by outcome (scored / saved /
  // missed) with a team-colour ring. Team chooser + a kick-by-kick list with the
  // running tally sit alongside. This is the last block on the page (below all graphs).
  function buildShootout(D) {
    var host = document.getElementById("mv-shootout");
    if (!host) return;
    var pens = D.shootout || [];
    if (!pens.length) { if (host.parentNode) host.parentNode.style.display = "none"; return; }

    // goal-mouth view mapping (WhoScored: GoalMouthY across, GoalMouthZ up).
    // Posts sit at GoalMouthY 45.2 / 54.8; crossbar at GoalMouthZ ≈ 38.
    var GW = 100, GROUND = 50, GY0 = 43, GYR = 14, GZTOP = 48;
    function gxOf(gy) { return (Math.max(GY0, Math.min(GY0 + GYR, gy)) - GY0) / GYR * GW; }
    function gyOf(gz) { return GROUND - Math.max(0, Math.min(GZTOP, gz)); }
    var postL = gxOf(45.2), postR = gxOf(54.8), barY = gyOf(38);
    var OUT_COL = { goal: "#37c978", saved: "#ff5b5b", missed: "#ffb020", post: "#ffb020" };
    var OUT_LBL = { goal: "Scored", saved: "Saved", missed: "Off target", post: "Hit post" };
    var OUT_ICON = { goal: "⚽", saved: "🧤", missed: "✗", post: "▮" };

    var fr = "#f4f6fb", ng = "rgba(255,255,255,0.14)";
    var net = [];
    net.push('<rect x="-2" y="-2" width="' + (GW + 4) + '" height="' + (GROUND + 6) + '" fill="#0d1420"/>');
    net.push('<rect x="' + postL.toFixed(1) + '" y="' + barY.toFixed(1) + '" width="' + (postR - postL).toFixed(1) +
      '" height="' + (GROUND - barY).toFixed(1) + '" fill="rgba(255,255,255,0.04)"/>');
    for (var nx = postL; nx <= postR + 0.01; nx += 3.2)
      net.push('<line x1="' + nx.toFixed(1) + '" y1="' + barY.toFixed(1) + '" x2="' + nx.toFixed(1) + '" y2="' + GROUND + '" stroke="' + ng + '" stroke-width="0.18"/>');
    for (var nyy = barY; nyy <= GROUND + 0.01; nyy += 3.0)
      net.push('<line x1="' + postL.toFixed(1) + '" y1="' + nyy.toFixed(1) + '" x2="' + postR.toFixed(1) + '" y2="' + nyy.toFixed(1) + '" stroke="' + ng + '" stroke-width="0.18"/>');
    net.push('<line x1="0" y1="' + GROUND + '" x2="' + GW + '" y2="' + GROUND + '" stroke="rgba(255,255,255,0.3)" stroke-width="0.4"/>');
    net.push('<line x1="' + postL.toFixed(1) + '" y1="' + GROUND + '" x2="' + postL.toFixed(1) + '" y2="' + barY.toFixed(1) + '" stroke="' + fr + '" stroke-width="1.1"/>');
    net.push('<line x1="' + postR.toFixed(1) + '" y1="' + GROUND + '" x2="' + postR.toFixed(1) + '" y2="' + barY.toFixed(1) + '" stroke="' + fr + '" stroke-width="1.1"/>');
    net.push('<line x1="' + (postL - 0.55).toFixed(1) + '" y1="' + barY.toFixed(1) + '" x2="' + (postR + 0.55).toFixed(1) + '" y2="' + barY.toFixed(1) + '" stroke="' + fr + '" stroke-width="1.1"/>');

    var hp = D.home.pens, ap = D.away.pens;
    var summary = "";
    if (hp != null && ap != null) {
      var win = hp > ap ? D.home.name : ap > hp ? D.away.name : null;
      summary = '<div class="so-summary"><b>' + esc(D.home.name) + " " + hp + " – " + ap + " " + esc(D.away.name) + "</b>" +
        (win ? ' <span class="so-win">' + esc(win) + " win on penalties</span>" : "") + "</div>";
    }

    host.innerHTML = summary +
      '<div class="controls-bar">' +
        '<span class="chip-toggle on home" id="soHome">' + esc(D.home.name) + "</span>" +
        '<span class="chip-toggle on away" id="soAway">' + esc(D.away.name) + "</span>" +
      "</div>" +
      '<div class="pitch-wrap"><svg class="pitch-svg" viewBox="-2 -2 ' + (GW + 4) + ' ' + (GROUND + 8) + '">' +
        net.join("") +
        '<text class="dir-label" x="' + (GW / 2) + '" y="' + (GROUND + 5) + '" text-anchor="middle">Goal-mouth view · placement of each kick</text>' +
        '<g id="soLayer"></g>' +
      "</svg></div>" +
      '<div class="legend-row">' +
        '<span><i class="dot" style="background:' + OUT_COL.goal + '"></i>Scored</span>' +
        '<span><i class="dot" style="background:' + OUT_COL.saved + '"></i>Saved</span>' +
        '<span><i class="dot" style="background:' + OUT_COL.missed + '"></i>Off target / post</span>' +
        '<span><i class="dot" style="background:#222;border:2px solid var(--c-home)"></i>Team ring</span>' +
      "</div>" +
      '<div class="so-list" id="soList"></div>';

    var layer = document.getElementById("soLayer");
    var state = { home: true, away: true };

    function draw() {
      layer.innerHTML = "";
      var NS = "http://www.w3.org/2000/svg";
      pens.forEach(function (k) {
        if (!state[k.team]) return;
        if (k.gy == null || k.gz == null) return;   // no placement coords → list only
        var cx = gxOf(k.gy), cy = gyOf(k.gz);
        var ring = k.team === "home" ? D.home.color : D.away.color;
        var c = document.createElementNS(NS, "circle");
        c.setAttribute("cx", cx.toFixed(2)); c.setAttribute("cy", cy.toFixed(2));
        c.setAttribute("r", "2.3");
        c.setAttribute("fill", OUT_COL[k.outcome] || "#ffb020");
        c.setAttribute("stroke", ring); c.setAttribute("stroke-width", "0.9");
        c.style.cursor = "pointer";
        var t = document.createElementNS(NS, "text");
        t.setAttribute("x", cx.toFixed(2)); t.setAttribute("y", (cy + 0.9).toFixed(2));
        t.setAttribute("text-anchor", "middle"); t.setAttribute("font-size", "2.6");
        t.setAttribute("font-weight", "800"); t.setAttribute("fill", "#0d1420");
        t.style.pointerEvents = "none";
        t.textContent = k.order;
        function tip(e) {
          showTip(e, "<b>#" + k.order + " " + esc(k.player) + "</b><br>" +
            esc(k.team === "home" ? D.home.name : D.away.name) + " · " + (OUT_LBL[k.outcome] || k.outcome) +
            (k.keeper ? "<br>Keeper: " + esc(k.keeper) : ""));
        }
        c.addEventListener("mousemove", tip);
        c.addEventListener("mouseleave", hideTip);
        layer.appendChild(c);
        layer.appendChild(t);
      });
    }

    // kick-by-kick list with running tally (always shows both teams, in order)
    var hC = 0, aC = 0;
    var rows = pens.map(function (k) {
      if (k.outcome === "goal") { if (k.team === "home") hC++; else aC++; }
      var col = k.team === "home" ? D.home.color : D.away.color;
      var name = k.team === "home" ? D.home.name : D.away.name;
      return '<div class="so-row ' + k.outcome + '">' +
        '<span class="so-n">' + k.order + "</span>" +
        '<span class="so-tm" style="background:' + col + '"></span>' +
        '<span class="so-pl">' + esc(k.player) + ' <span class="so-tn">' + esc(name) + "</span></span>" +
        '<span class="so-oc">' + (OUT_ICON[k.outcome] || "") + " " + (OUT_LBL[k.outcome] || k.outcome) + "</span>" +
        '<span class="so-sc">' + hC + "–" + aC + "</span>" +
        "</div>";
    }).join("");
    document.getElementById("soList").innerHTML = rows;

    document.getElementById("soHome").addEventListener("click", function () {
      state.home = !state.home; this.classList.toggle("on"); draw(); });
    document.getElementById("soAway").addEventListener("click", function () {
      state.away = !state.away; this.classList.toggle("on"); draw(); });
    draw();
  }

  /* ---- tooltip ---- */
  function showTip(e, html) {
    tooltip.innerHTML = html; tooltip.style.opacity = "1";
    tooltip.style.left = (e.clientX + 14) + "px"; tooltip.style.top = (e.clientY + 14) + "px";
  }
  function hideTip() { tooltip.style.opacity = "0"; }
})();
