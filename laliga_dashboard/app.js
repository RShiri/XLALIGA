/* La Liga dashboard front-end. Consumes window.LL_DATA (data.js) + window.LL_PLAYERS
   (players.js). No build step, no network at view time. One payload per season with a
   season switcher; a round-robin league so we render a single standings table + a
   remaining-fixtures projection instead of the WC group tables / knockout bracket.
   The xG analysis lab reuses the WC2026 scatter engine verbatim and lights up as
   matches get deep-scraped (see laliga/backfill.py). */
(function () {
  "use strict";
  var ALL = window.LL_DATA;
  if (!ALL) { document.body.innerHTML = "<p style='padding:40px'>data.js failed to load.</p>"; return; }
  var PLAYERS_ALL = window.LL_PLAYERS || {};

  var season = ALL.defaultSeason;
  var D = ALL.seasons[season];
  var PLAYERS = PLAYERS_ALL[season] || [];
  var tooltip = document.getElementById("tooltip");

  /* European / relegation zones (La Liga): UCL top 4, Europa 5, Conference play-off 6,
     relegation bottom 3. Purely cosmetic shading + a legend. */
  function zoneOf(rank, total) {
    if (rank <= 4) return "z-ucl";
    if (rank === 5) return "z-uel";
    if (rank === 6) return "z-uecl";
    if (rank > total - 3) return "z-rel";
    return "";
  }

  /* ---------------- tiny DOM helpers ---------------- */
  function el(tag, cls, html) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (html != null) e.innerHTML = html;
    return e;
  }
  function esc(s) {
    return String(s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }
  function crestUrl(team) { return (D.crests && D.crests[team]) || ""; }
  function logoImg(team, cls) {
    var url = crestUrl(team), safe = esc(team);
    if (!url) return '<span class="crest ' + (cls || "") + ' noimg" title="' + safe + '"></span>';
    return '<img class="crest ' + (cls || "") + '" src="' + esc(url) +
      '" alt="" loading="lazy" onerror="this.style.visibility=\'hidden\'" title="' + safe + '">';
  }
  function fmtDate(d) {
    if (!d) return "";
    var dt = new Date(d + "T00:00:00");
    if (isNaN(dt)) return d;
    return dt.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
  }

  /* ---------------- Shared scatter engine (ported verbatim from the WC2026 dashboard) ---- */
  function niceMax(v) {
    if (!(v > 0)) return 1;
    var pow = Math.pow(10, Math.floor(Math.log10(v))), n = v / pow;
    var step = n <= 1 ? 1 : n <= 2 ? 2 : n <= 2.5 ? 2.5 : n <= 5 ? 5 : 10;
    return step * pow;
  }
  function niceTicks(max, target) {
    target = target || 5;
    var raw = max / target, pow = Math.pow(10, Math.floor(Math.log10(raw))), n = raw / pow;
    var step = (n <= 1 ? 1 : n <= 2 ? 2 : n <= 2.5 ? 2.5 : n <= 5 ? 5 : 10) * pow;
    var ticks = [];
    for (var v = 0; v <= max + 1e-9; v += step) ticks.push(+v.toFixed(4));
    return ticks;
  }
  function fmtTick(v) { return Math.round(v) === v ? String(v) : v.toFixed(v < 1 ? 2 : 1); }
  function declutter(pts, fontPx) {
    var LH = fontPx + 2.6, CW = fontPx * 0.56, placed = [];
    pts.map(function (p, i) { return i; })
      .sort(function (a, b) { return pts[a].cy - pts[b].cy; })
      .forEach(function (i) {
        var p = pts[i], w = String(p.team).length * CW + 5;
        var lx = p.cx + 8, ly = p.cy + 3, guard = 0, moved = true;
        while (moved && guard++ < 400) {
          moved = false;
          for (var j = 0; j < placed.length; j++) {
            var q = placed[j];
            if (lx < q.x2 && q.lx < lx + w && Math.abs(ly - q.ly) < LH) { ly = q.ly + LH; moved = true; }
          }
        }
        p.lx = lx; p.ly = ly; p.led = Math.abs(ly - (p.cy + 3)) > 3.5;
        placed.push({ lx: lx, x2: lx + w, ly: ly });
      });
    return pts;
  }
  function chartLegend(items, note) {
    return '<div class="chart-legend">' + items.map(function (it) {
      return '<span class="cl-item"><span class="cl-sw" style="background:' + it[0] + '"></span>' + it[1] + "</span>";
    }).join("") + (note ? '<span class="cl-note">' + note + "</span>" : "") + "</div>";
  }
  function teamScatter(hostId, rows, cfg) {
    var host = document.getElementById(hostId);
    if (!host) return;
    if (!rows.length) { host.innerHTML = '<p class="hint">Not enough data yet — deep-scrape matches to populate xG.</p>'; return; }
    var W = 960, H = cfg.h || 560, padL = 58, padR = 14, padT = 24, padB = 50;
    var plotW = W - padL - padR, plotH = H - padT - padB;
    var xMax, yMax;
    if (cfg.centerAvg && cfg.avgX != null && cfg.avgY != null) {
      var dx = Math.max.apply(null, rows.map(function (r) { return Math.abs(r.x - cfg.avgX); }));
      var dy = Math.max.apply(null, rows.map(function (r) { return Math.abs(r.y - cfg.avgY); }));
      xMax = cfg.avgX + dx; yMax = cfg.avgY + dy;
    } else {
      xMax = niceMax(Math.max.apply(null, rows.map(function (r) { return r.x; }).concat([cfg.xMin || 0])) * 1.08);
      yMax = niceMax(Math.max.apply(null, rows.map(function (r) { return r.y; }).concat([cfg.yMin || 0])) * 1.08);
    }
    function sx(v) { return padL + (v / xMax) * plotW; }
    function sy(v) { return cfg.flipY ? padT + (v / yMax) * plotH : (padT + plotH) - (v / yMax) * plotH; }
    var svg = ['<svg viewBox="0 0 ' + W + " " + H + '" width="100%" class="scatter-svg">'];
    niceTicks(xMax).forEach(function (t) {
      var X = sx(t);
      svg.push('<line x1="' + X.toFixed(1) + '" y1="' + padT + '" x2="' + X.toFixed(1) + '" y2="' + (padT + plotH) + '" stroke="#222b44" stroke-width="0.6"/>');
      svg.push('<text x="' + X.toFixed(1) + '" y="' + (padT + plotH + 16) + '" fill="#93a0bd" font-size="10" text-anchor="middle">' + fmtTick(t) + "</text>");
    });
    niceTicks(yMax).forEach(function (t) {
      var Y = sy(t);
      svg.push('<line x1="' + padL + '" y1="' + Y.toFixed(1) + '" x2="' + (padL + plotW) + '" y2="' + Y.toFixed(1) + '" stroke="#222b44" stroke-width="0.6"/>');
      svg.push('<text x="' + (padL - 8) + '" y="' + (Y + 3).toFixed(1) + '" fill="#93a0bd" font-size="10" text-anchor="end">' + fmtTick(t) + "</text>");
    });
    if (cfg.avgX != null) {
      var AX = sx(cfg.avgX);
      svg.push('<line x1="' + AX.toFixed(1) + '" y1="' + padT + '" x2="' + AX.toFixed(1) + '" y2="' + (padT + plotH) + '" stroke="#5d6a90" stroke-width="1" stroke-dasharray="4 4"/>');
      svg.push('<text x="' + (AX + 3).toFixed(1) + '" y="' + (padT + 11) + '" fill="#7e8bb0" font-size="9.5">avg</text>');
    }
    if (cfg.avgY != null) {
      var AY = sy(cfg.avgY);
      svg.push('<line x1="' + padL + '" y1="' + AY.toFixed(1) + '" x2="' + (padL + plotW) + '" y2="' + AY.toFixed(1) + '" stroke="#5d6a90" stroke-width="1" stroke-dasharray="4 4"/>');
      svg.push('<text x="' + (padL + plotW - 4) + '" y="' + (AY - 4).toFixed(1) + '" fill="#7e8bb0" font-size="9.5" text-anchor="end">avg</text>');
    }
    (cfg.corners || []).forEach(function (c) {
      var x = c.h === "l" ? padL + 8 : padL + plotW - 8, anc = c.h === "l" ? "start" : "end";
      var y = c.v === "t" ? padT + 14 : padT + plotH - 8;
      svg.push('<text x="' + x + '" y="' + y + '" fill="' + c.color + '" font-size="11" font-weight="700" text-anchor="' + anc + '" opacity="0.85">' + c.text + "</text>");
    });
    svg.push('<text x="' + (padL + plotW / 2) + '" y="' + (H - 6) + '" fill="#e8edf7" font-size="12.5" text-anchor="middle">' + cfg.xLabel + "</text>");
    svg.push('<text x="15" y="' + (padT + plotH / 2) + '" fill="#e8edf7" font-size="12.5" text-anchor="middle" transform="rotate(-90 15 ' + (padT + plotH / 2) + ')">' + cfg.yLabel + "</text>");
    rows.forEach(function (r) { r.cx = sx(r.x); r.cy = sy(r.y); });
    rows.forEach(function (r) {
      svg.push('<circle cx="' + r.cx.toFixed(1) + '" cy="' + r.cy.toFixed(1) + '" r="5" fill="' + (r.col || "#4ea8ff") +
        '" fill-opacity="0.9" stroke="#0b0f1a" stroke-width="0.9"><title>' + esc(r.team) + (cfg.tip ? " — " + cfg.tip(r) : "") + "</title></circle>");
    });
    declutter(rows, 8.7);
    rows.forEach(function (r) {
      if (r.led) svg.push('<line x1="' + r.cx.toFixed(1) + '" y1="' + r.cy.toFixed(1) + '" x2="' + (r.lx - 1).toFixed(1) + '" y2="' + (r.ly - 3).toFixed(1) + '" stroke="#46527a" stroke-width="0.6"/>');
      svg.push('<text x="' + r.lx.toFixed(1) + '" y="' + r.ly.toFixed(1) + '" fill="#c2cce0" font-size="8.7">' + esc(r.team) + "</text>");
    });
    svg.push("</svg>");
    host.innerHTML = svg.join("") + (cfg.legend || "");
  }

  /* ================= VIEWS ================= */

  /* ---- Overview cards ---- */
  function renderOverview() {
    var c = D.counts || {};
    var wrap = document.getElementById("overviewStats");
    wrap.innerHTML = "";
    [["v accent", c.played || 0, "Matches played"],
     ["v", (c.total || 0) - (c.played || 0), "Still to come"],
     ["v blue", c.teams || 0, "Teams"],
     ["v", "MD " + (c.current_matchday || 0), "Current matchday"],
     ["v", c.with_xg || 0, "Matches with xG"]
    ].forEach(function (it) {
      var s = el("div", "stat");
      s.innerHTML = '<div class="' + it[0] + '">' + it[1] + '</div><div class="k">' + it[2] + "</div>";
      wrap.appendChild(s);
    });
  }

  /* ---- Standings ---- */
  function formDots(form) {
    return '<span class="form">' + (form || []).map(function (r) {
      return '<span class="fd f-' + r + '" title="' + r + '">' + r + "</span>";
    }).join("") + "</span>";
  }
  function renderStandings() {
    var host = document.getElementById("standings");
    var rows = D.standings || [];
    if (!rows.length) { host.innerHTML = '<p class="hint">No results yet this season.</p>'; return; }
    var total = rows.length;
    var body = rows.map(function (r) {
      return '<tr class="' + zoneOf(r.rank, total) + '">' +
        '<td class="pos">' + r.rank + "</td>" +
        '<td class="team"><div class="team-cell">' + logoImg(r.team) + '<span class="nm">' + esc(r.team) + "</span></div></td>" +
        "<td>" + r.P + "</td><td>" + r.W + "</td><td>" + r.D + "</td><td>" + r.L + "</td>" +
        "<td>" + r.GF + "</td><td>" + r.GA + "</td><td>" + (r.GD > 0 ? "+" + r.GD : r.GD) + "</td>" +
        '<td class="pts">' + r.Pts + "</td>" +
        '<td class="form-cell">' + formDots(r.form) + "</td></tr>";
    }).join("");
    host.innerHTML =
      "<table class='standings'><thead><tr><th>#</th><th class='team'>Team</th>" +
      "<th>P</th><th>W</th><th>D</th><th>L</th><th>GF</th><th>GA</th><th>GD</th><th>Pts</th><th class='form-cell'>Form</th>" +
      "</tr></thead><tbody>" + body + "</tbody></table>" +
      "<div class='zone-legend'>" +
      "<span><i class='z-ucl'></i>Champions League</span>" +
      "<span><i class='z-uel'></i>Europa League</span>" +
      "<span><i class='z-uecl'></i>Conference play-off</span>" +
      "<span><i class='z-rel'></i>Relegation</span></div>";
  }

  /* ---- Matches (grouped by matchday) ---- */
  var mSearch, mStatus, mMatchday;
  function scoreCell(m) {
    if (m.played) return '<span class="sc">' + m.hs + " – " + m.as + "</span>";
    var t = m.kickoff ? new Date(m.kickoff) : null;
    var time = (t && !isNaN(t)) ? t.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "";
    return '<span class="sc up">' + (time || "vs") + "</span>";
  }
  function matchRow(m) {
    var link = m.has_events ? ("match.html?season=" + encodeURIComponent(season) + "&id=" + encodeURIComponent(m.id)) : null;
    var inner =
      '<div class="mc-side home"><span class="nm">' + esc(m.home) + "</span>" + logoImg(m.home) + "</div>" +
      '<div class="mc-score">' + scoreCell(m) + "</div>" +
      '<div class="mc-side away">' + logoImg(m.away) + '<span class="nm">' + esc(m.away) + "</span></div>";
    var xg = (m.xg_home != null && m.xg_away != null)
      ? '<div class="mc-xg">xG ' + m.xg_home.toFixed(2) + " – " + m.xg_away.toFixed(2) + (m.xg_estimated ? " (est)" : "") + "</div>" : "";
    var cls = "match-card" + (m.played ? " played" : " upcoming") + (link ? " has-link" : "");
    var open = link ? '<a class="match-card-link" href="' + link + '">' : "<div class='" + cls + "'>";
    var close = link ? "</a>" : "</div>";
    if (link) open = '<a class="' + cls + '" href="' + link + '">';
    return open + '<div class="mc-date">' + fmtDate(m.date) + "</div>" + inner + xg + close;
  }
  function renderMatches() {
    var q = (mSearch.value || "").toLowerCase().trim();
    var mode = mStatus.value;
    var mdFilter = mMatchday.value;
    var list = document.getElementById("matchList");
    var ms = (D.matches || []).filter(function (m) {
      if (mode === "played" && !m.played) return false;
      if (mode === "upcoming" && m.played) return false;
      if (mdFilter !== "all" && String(m.matchday) !== mdFilter) return false;
      if (q && (m.home + " " + m.away).toLowerCase().indexOf(q) < 0) return false;
      return true;
    });
    if (!ms.length) { list.innerHTML = '<p class="hint">No matches match your filters.</p>'; return; }
    // group by matchday
    var byMd = {};
    ms.forEach(function (m) { (byMd[m.matchday] = byMd[m.matchday] || []).push(m); });
    var out = Object.keys(byMd).map(Number).sort(function (a, b) { return a - b; }).map(function (md) {
      var group = byMd[md].sort(function (a, b) { return (a.kickoff || a.date).localeCompare(b.kickoff || b.date); });
      return '<div class="md-block"><h3 class="md-head">Matchday ' + md + "</h3>" +
        '<div class="match-grid">' + group.map(matchRow).join("") + "</div></div>";
    }).join("");
    list.innerHTML = out;
  }
  function populateMatchdayFilter() {
    var mds = {};
    (D.matches || []).forEach(function (m) { if (m.matchday) mds[m.matchday] = 1; });
    var opts = ['<option value="all">All matchdays</option>'].concat(
      Object.keys(mds).map(Number).sort(function (a, b) { return a - b; }).map(function (md) {
        return '<option value="' + md + '">Matchday ' + md + "</option>";
      }));
    mMatchday.innerHTML = opts.join("");
    var cur = (D.counts || {}).current_matchday;
    if (cur) mMatchday.value = String(cur);
  }

  /* ---- xG analysis lab (reuses teamScatter; lights up post-backfill) ---- */
  function teamXgAgg() {
    var R = D.xgRecords || [], agg = {};
    R.forEach(function (r) {
      var a = agg[r.team] = agg[r.team] || { team: r.team, n: 0, xgf: 0, xga: 0, gf: 0, ga: 0 };
      a.n++; a.xgf += r.xgf; a.xga += r.xga; a.gf += r.gf; a.ga += r.ga;
    });
    return Object.keys(agg).map(function (t) {
      var a = agg[t];
      return { team: t, n: a.n, xgfpg: a.xgf / a.n, xgapg: a.xga / a.n,
               xgf: a.xgf, xga: a.xga, gf: a.gf, ga: a.ga, xgd: (a.xgf - a.xga) / a.n };
    });
  }
  function renderXg() {
    var rows = teamXgAgg();
    var note = document.getElementById("xgNote");
    if (note) note.textContent = rows.length
      ? (rows.length + " teams with xG data (" + (D.counts.with_xg || 0) + " matches deep-scraped).")
      : "No xG yet — run the deep-scrape backfill to populate this section.";
    // Attack vs defence: xG for (x) vs xG against (y, flipped so up-right = best)
    if (rows.length) {
      var avgF = rows.reduce(function (s, r) { return s + r.xgfpg; }, 0) / rows.length;
      var avgA = rows.reduce(function (s, r) { return s + r.xgapg; }, 0) / rows.length;
      teamScatter("xgScatter", rows.map(function (r) {
        return { team: r.team, x: r.xgfpg, y: r.xgapg, col: r.xgd >= 0 ? "#39d98a" : "#ff6b6b" };
      }), {
        xLabel: "xG created per game", yLabel: "xG conceded per game", flipY: true,
        avgX: avgF, avgY: avgA,
        corners: [{ h: "r", v: "t", text: "dominant", color: "#39d98a" }, { h: "l", v: "b", text: "outplayed", color: "#ff6b6b" }],
        tip: function (r) { return "xGF " + r.x.toFixed(2) + " / xGA " + r.y.toFixed(2); },
        legend: chartLegend([["#39d98a", "positive xG diff"], ["#ff6b6b", "negative xG diff"]], "Top-right = creates lots, concedes little.")
      });
    } else teamScatter("xgScatter", [], {});
    // Goals vs xG (finishing): actual GF (x) vs xGF (y)
    if (rows.length) {
      teamScatter("xgFinish", rows.map(function (r) {
        return { team: r.team, x: r.xgf, y: r.gf, col: r.gf >= r.xgf ? "#4ea8ff" : "#f7b955" };
      }), {
        xLabel: "Total xG", yLabel: "Actual goals", diagonal: false,
        corners: [{ h: "l", v: "t", text: "clinical", color: "#4ea8ff" }, { h: "r", v: "b", text: "wasteful", color: "#f7b955" }],
        tip: function (r) { return r.y + " goals from " + r.x.toFixed(1) + " xG"; },
        legend: chartLegend([["#4ea8ff", "outscoring xG"], ["#f7b955", "underscoring xG"]])
      });
    } else teamScatter("xgFinish", [], {});
  }

  /* ---- Projection: Poisson strengths + Monte-Carlo over remaining fixtures ---- */
  function poisson(lambda, k) {   // P(X=k) for X~Poisson(lambda)
    var f = 1;
    for (var i = 2; i <= k; i++) f *= i;
    return Math.pow(lambda, k) * Math.exp(-lambda) / f;
  }
  function computeStrengths() {
    var played = (D.matches || []).filter(function (m) { return m.played; });
    if (played.length < 20) return null;   // too little signal
    var homeG = 0, awayG = 0, n = played.length;
    played.forEach(function (m) { homeG += m.hs; awayG += m.as; });
    var lgHome = homeG / n, lgAway = awayG / n;
    var t = {};
    (D.standings || []).forEach(function (r) {
      t[r.team] = { gfH: 0, gaH: 0, nH: 0, gfA: 0, gaA: 0, nA: 0 };
    });
    played.forEach(function (m) {
      if (!t[m.home] || !t[m.away]) return;
      t[m.home].gfH += m.hs; t[m.home].gaH += m.as; t[m.home].nH++;
      t[m.away].gfA += m.as; t[m.away].gaA += m.hs; t[m.away].nA++;
    });
    // Attack/defence strengths (home & away split), shrunk toward 1.0 for stability
    // when a team has few games (prior weight w games of league-average).
    var w = 4;
    var str = {};
    Object.keys(t).forEach(function (tm) {
      var x = t[tm];
      var atkH = ((x.gfH + w * lgHome) / (x.nH + w)) / lgHome;
      var defH = ((x.gaH + w * lgAway) / (x.nH + w)) / lgAway;
      var atkA = ((x.gfA + w * lgAway) / (x.nA + w)) / lgAway;
      var defA = ((x.gaA + w * lgHome) / (x.nA + w)) / lgHome;
      str[tm] = { atkH: atkH, defH: defH, atkA: atkA, defA: defA };
    });
    return { lgHome: lgHome, lgAway: lgAway, str: str };
  }
  function matchLambdas(S, home, away) {
    var h = S.str[home], a = S.str[away];
    if (!h || !a) return null;
    return [S.lgHome * h.atkH * a.defA, S.lgAway * a.atkA * h.defH];
  }
  function samplePoisson(lambda) {
    var Lp = Math.exp(-lambda), k = 0, p = 1;
    do { k++; p *= Math.random(); } while (p > Lp);
    return k - 1;
  }
  function runProjection() {
    var S = computeStrengths();
    if (!S) return null;
    var remaining = (D.matches || []).filter(function (m) { return !m.played; });
    var base = {};
    (D.standings || []).forEach(function (r) { base[r.team] = r.Pts; });
    var teams = Object.keys(base);
    // Deterministic expected points from match win/draw/loss probabilities.
    var exp = {}; teams.forEach(function (t) { exp[t] = base[t]; });
    remaining.forEach(function (m) {
      var L = matchLambdas(S, m.home, m.away); if (!L) return;
      var pH = 0, pD = 0, pA = 0;
      for (var i = 0; i <= 8; i++) for (var j = 0; j <= 8; j++) {
        var pr = poisson(L[0], i) * poisson(L[1], j);
        if (i > j) pH += pr; else if (i === j) pD += pr; else pA += pr;
      }
      exp[m.home] += 3 * pH + pD; exp[m.away] += 3 * pA + pD;
    });
    // Monte-Carlo for title / top-4 / European / relegation probabilities.
    var N = 3000, tally = {};
    teams.forEach(function (t) { tally[t] = { title: 0, top4: 0, europe: 0, rel: 0, ptsSum: 0 }; });
    var gd0 = {}; (D.standings || []).forEach(function (r) { gd0[r.team] = r.GD; });
    for (var s = 0; s < N; s++) {
      var pts = {}, gd = {};
      teams.forEach(function (t) { pts[t] = base[t]; gd[t] = gd0[t]; });
      remaining.forEach(function (m) {
        var L = matchLambdas(S, m.home, m.away); if (!L) return;
        var hg = samplePoisson(L[0]), ag = samplePoisson(L[1]);
        gd[m.home] += hg - ag; gd[m.away] += ag - hg;
        if (hg > ag) pts[m.home] += 3; else if (hg < ag) pts[m.away] += 3; else { pts[m.home]++; pts[m.away]++; }
      });
      var order = teams.slice().sort(function (a, b) {
        return (pts[b] - pts[a]) || (gd[b] - gd[a]) || (Math.random() - 0.5);
      });
      order.forEach(function (t, idx) {
        var rk = idx + 1;
        tally[t].ptsSum += pts[t];
        if (rk === 1) tally[t].title++;
        if (rk <= 4) tally[t].top4++;
        if (rk <= 6) tally[t].europe++;
        if (rk > teams.length - 3) tally[t].rel++;
      });
    }
    var proj = teams.map(function (t) {
      return {
        team: t, curPts: base[t], expPts: exp[t], projPts: tally[t].ptsSum / N,
        title: tally[t].title / N, top4: tally[t].top4 / N, europe: tally[t].europe / N, rel: tally[t].rel / N
      };
    }).sort(function (a, b) { return b.projPts - a.projPts; });
    return proj;
  }
  function pct(x) { return x <= 0 ? "–" : x >= 0.995 ? "99%+" : (x * 100).toFixed(x < 0.1 ? 1 : 0) + "%"; }
  function bar(x, cls) { return '<div class="pbar"><span class="' + cls + '" style="width:' + Math.max(2, x * 100).toFixed(0) + '%"></span><em>' + pct(x) + "</em></div>"; }
  function renderProjection() {
    var host = document.getElementById("projTable");
    var banner = document.getElementById("projChamp");
    if (D.status === "not_started") {
      host.innerHTML = '<p class="hint">This season has not started yet. Add the fixture schedule and the projection will populate.</p>';
      if (banner) banner.innerHTML = ""; return;
    }
    var P = runProjection();
    if (!P) { host.innerHTML = '<p class="hint">Not enough matches played yet to project.</p>'; if (banner) banner.innerHTML = ""; return; }
    if (banner) {
      var champ = P[0];
      banner.innerHTML = '<div class="champ-card">' + logoImg(champ.team, "champ-crest") +
        '<div><div class="champ-lbl">Projected champion</div><div class="champ-team">' + esc(champ.team) +
        '</div><div class="champ-odds">' + pct(champ.title) + " title chance · ~" + champ.projPts.toFixed(0) + " pts</div></div></div>";
    }
    var body = P.map(function (r, i) {
      return "<tr><td class='pos'>" + (i + 1) + "</td>" +
        "<td class='team'><div class='team-cell'>" + logoImg(r.team) + "<span class='nm'>" + esc(r.team) + "</span></div></td>" +
        "<td>" + r.curPts + "</td><td class='pts'>" + r.projPts.toFixed(1) + "</td>" +
        "<td>" + bar(r.title, "b-title") + "</td>" +
        "<td>" + bar(r.top4, "b-top4") + "</td>" +
        "<td>" + bar(r.europe, "b-eu") + "</td>" +
        "<td>" + bar(r.rel, "b-rel") + "</td></tr>";
    }).join("");
    host.innerHTML =
      "<table class='proj'><thead><tr><th>#</th><th class='team'>Team</th><th>Pts now</th><th>Proj pts</th>" +
      "<th>Title</th><th>Top 4</th><th>Top 6</th><th>Relegated</th></tr></thead><tbody>" + body + "</tbody></table>" +
      "<p class='hint'>Poisson model on this season's goals (home/away attack &amp; defence strengths, shrunk toward league average), " +
      "Monte-Carlo over the " + (D.matches.filter(function (m) { return !m.played; }).length) + " remaining fixtures (3,000 sims).</p>";
  }

  /* ---- Players ---- */
  function renderPlayers() {
    var host = document.getElementById("playersTable");
    if (!PLAYERS.length) {
      host.innerHTML = '<p class="hint">Player data populates as matches are deep-scraped (goals, assists, xG, xA). Run the backfill to fill this in.</p>';
      return;
    }
    var preset = (document.querySelector("#view-players .seg-btn.active") || {}).dataset;
    var key = (preset && preset.preset) || "ga";
    // players.js fields: g (goals), a (assists), xg, rating, mp (matches), mins, keyPasses.
    var metric = { ga: function (p) { return (p.g || 0) + (p.a || 0); },
                   g: function (p) { return p.g || 0; }, a: function (p) { return p.a || 0; },
                   xg: function (p) { return p.xg || 0; }, rating: function (p) { return p.rating || 0; } }[key] || function (p) { return 0; };
    var rows = PLAYERS.slice().sort(function (a, b) { return metric(b) - metric(a); }).slice(0, 50);
    var body = rows.map(function (p, i) {
      return "<tr><td class='pos'>" + (i + 1) + "</td>" +
        "<td class='team'><div class='team-cell'>" + logoImg(p.team) + "<span class='nm'>" + esc(p.name) + "</span></div></td>" +
        "<td>" + esc(p.team) + "</td><td>" + (p.mp || 0) + "</td><td>" + (p.g || 0) + "</td><td>" + (p.a || 0) + "</td>" +
        "<td>" + (p.xg != null ? p.xg.toFixed(2) : "–") + "</td><td>" + (p.rating != null ? p.rating.toFixed(2) : "–") + "</td></tr>";
    }).join("");
    host.innerHTML = "<table><thead><tr><th>#</th><th class='team'>Player</th><th>Team</th><th>MP</th><th>G</th><th>A</th><th>xG</th><th>Rating</th></tr></thead><tbody>" + body + "</tbody></table>";
  }

  /* ---- Data dump ---- */
  function renderData() {
    var host = document.getElementById("dataTable");
    var ms = (D.matches || []).filter(function (m) { return m.played; })
      .sort(function (a, b) { return (b.kickoff || b.date).localeCompare(a.kickoff || a.date); });
    var body = ms.map(function (m) {
      return "<tr><td>" + (m.matchday || "") + "</td><td>" + fmtDate(m.date) + "</td>" +
        "<td class='team'>" + esc(m.home) + "</td><td class='sc'>" + m.hs + "–" + m.as + "</td><td class='team'>" + esc(m.away) + "</td>" +
        "<td>" + (m.xg_home != null ? m.xg_home.toFixed(2) : "–") + "</td><td>" + (m.xg_away != null ? m.xg_away.toFixed(2) : "–") + "</td>" +
        "<td>" + (m.png ? '<a href="' + esc(m.png) + '" target="_blank">PNG</a>' : "–") + "</td></tr>";
    }).join("");
    host.innerHTML = "<table><thead><tr><th>MD</th><th>Date</th><th>Home</th><th>Score</th><th>Away</th><th>xG H</th><th>xG A</th><th>Info</th></tr></thead><tbody>" + body + "</tbody></table>";
  }

  /* ================= wiring ================= */
  function renderAll() {
    D = ALL.seasons[season];
    PLAYERS = PLAYERS_ALL[season] || [];
    renderOverview();
    renderStandings();
    populateMatchdayFilter();
    renderMatches();
    renderXg();
    renderProjection();
    renderPlayers();
    renderData();
    var foot = document.getElementById("footNote");
    if (foot) foot.textContent = "Season " + season + " · generated " + ALL.generated + " · " +
      (D.counts.played || 0) + " matches played · " + (D.counts.with_xg || 0) + " with xG · La Liga analytics pipeline.";
  }

  function initControls() {
    // tabs
    var tabs = document.querySelectorAll("nav.tabs button");
    tabs.forEach(function (b) {
      b.addEventListener("click", function () {
        tabs.forEach(function (x) { x.classList.remove("active"); });
        b.classList.add("active");
        document.querySelectorAll(".view").forEach(function (v) { v.classList.remove("active"); });
        document.getElementById("view-" + b.dataset.view).classList.add("active");
        window.scrollTo({ top: 0, behavior: "smooth" });
      });
    });
    // season switcher
    var sel = document.getElementById("seasonSel");
    if (sel) {
      sel.innerHTML = Object.keys(ALL.seasons).sort().map(function (s) {
        var label = s.replace("-", "/") + (ALL.seasons[s].status === "not_started" ? " (upcoming)" : "");
        return '<option value="' + s + '"' + (s === season ? " selected" : "") + ">" + label + "</option>";
      }).join("");
      sel.addEventListener("change", function () { season = sel.value; renderAll(); });
    }
    // match filters
    mSearch = document.getElementById("mSearch");
    mStatus = document.getElementById("mStatus");
    mMatchday = document.getElementById("mMatchday");
    [mSearch, mStatus, mMatchday].forEach(function (c) { if (c) c.addEventListener("input", renderMatches); });
    if (mStatus) mStatus.addEventListener("change", renderMatches);
    if (mMatchday) mMatchday.addEventListener("change", renderMatches);
    // player preset buttons
    document.querySelectorAll("#view-players .seg-btn").forEach(function (b) {
      b.addEventListener("click", function () {
        document.querySelectorAll("#view-players .seg-btn").forEach(function (x) { x.classList.remove("active"); });
        b.classList.add("active"); renderPlayers();
      });
    });
  }

  initControls();
  renderAll();
})();
