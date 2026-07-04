/* Premier League dashboard front-end. Consumes window.LL_DATA (data.js) + window.LL_PLAYERS
   (players.js). No build step, no network at view time. One payload per season with a
   season switcher; a round-robin league so we render a single standings table + a
   remaining-fixtures projection instead of the WC group tables / knockout bracket.
   The xG analysis lab reuses the WC2026 scatter engine verbatim and lights up as
   matches get deep-scraped (see epl/backfill.py). */
(function () {
  "use strict";
  var ALL = window.LL_DATA;
  if (!ALL) { document.body.innerHTML = "<p style='padding:40px'>data.js failed to load.</p>"; return; }
  var PLAYERS_ALL = window.LL_PLAYERS || {};

  var season = ALL.defaultSeason;
  var D = ALL.seasons[season];
  var PLAYERS = PLAYERS_ALL[season] || [];
  var tooltip = document.getElementById("tooltip");

  /* European / relegation zones (Premier League 2025/26): UCL top 5 (England earned a
     fifth Champions League place via its UEFA coefficient that season), Europa 6,
     Conference play-off 7, relegation bottom 3. Purely cosmetic shading + a legend. */
  function zoneOf(rank, total) {
    if (rank <= 5) return "z-ucl";
    if (rank === 6) return "z-uel";
    if (rank === 7) return "z-uecl";
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
    if (cfg.diagonal) {
      var dMax = Math.min(xMax, yMax);
      svg.push('<line x1="' + sx(0).toFixed(1) + '" y1="' + sy(0).toFixed(1) + '" x2="' + sx(dMax).toFixed(1) +
        '" y2="' + sy(dMax).toFixed(1) + '" stroke="#8b96b8" stroke-width="1.2" stroke-dasharray="5 4"/>');
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
  /* ============================================================
     xG EFFICIENCY LAB — ported from the WC2026 dashboard. Driven by
     D.xgRecords (one row per team-match: xgf/xga/gf/ga/opp/home) plus
     the per-match team `stats` block for the shot-quality chart.
     Derived stats recompute per season (see computeXgDerived). ======= */
  var COL = { green: "#3ddc97", blue: "#4ea1ff", orange: "#ffb454", red: "#ff6b81" };
  var R = [], AGG = [], xgVals = [], goalVals = [], rPearson = 0, fit = { slope: 0, intercept: 0 };
  var ledgerSort = { key: "xgd", dir: -1 };
  var dbSort = { key: "gf", dir: -1 };
  var TOTALS = [];

  function pearson(xs, ys) {
    var n = xs.length, sx = 0, sy = 0, sxy = 0, sx2 = 0, sy2 = 0;
    for (var i = 0; i < n; i++) { sx += xs[i]; sy += ys[i]; sxy += xs[i] * ys[i]; sx2 += xs[i] * xs[i]; sy2 += ys[i] * ys[i]; }
    var num = n * sxy - sx * sy;
    var den = Math.sqrt((n * sx2 - sx * sx) * (n * sy2 - sy * sy));
    return den === 0 ? 0 : num / den;
  }
  function linreg(xs, ys) {
    var n = xs.length, sx = 0, sy = 0, sxy = 0, sx2 = 0;
    for (var i = 0; i < n; i++) { sx += xs[i]; sy += ys[i]; sxy += xs[i] * ys[i]; sx2 += xs[i] * xs[i]; }
    var slope = (n * sx2 - sx * sx) === 0 ? 0 : (n * sxy - sx * sy) / (n * sx2 - sx * sx);
    return { slope: slope, intercept: (sy - slope * sx) / n };
  }
  function poisP(k, lam) { var f = 1; for (var i = 2; i <= k; i++) f *= i; return Math.exp(-lam) * Math.pow(lam, k) / f; }

  function teamAggregates() {
    var t = {};
    R.forEach(function (r) {
      var a = t[r.team] || (t[r.team] = { team: r.team, gf: 0, ga: 0, xgf: 0, xga: 0, n: 0 });
      a.gf += r.gf; a.ga += r.ga; a.xgf += r.xgf; a.xga += r.xga; a.n++;
    });
    return Object.keys(t).map(function (k) {
      var a = t[k];
      a.attDelta = a.gf - a.xgf;   // finishing: + = clinical
      a.defDelta = a.xga - a.ga;   // defence/keeping: + = conceded fewer than expected
      a.xgd = a.xgf - a.xga;       // deserved margin
      return a;
    });
  }
  function computeXgDerived() {
    R = D.xgRecords || [];
    xgVals = R.map(function (r) { return r.xgf; });
    goalVals = R.map(function (r) { return r.gf; });
    rPearson = R.length ? pearson(xgVals, goalVals) : 0;
    fit = R.length ? linreg(xgVals, goalVals) : { slope: 0, intercept: 0 };
    AGG = teamAggregates();
  }

  function renderXgStats() {
    var totalGoals = goalVals.reduce(function (a, b) { return a + b; }, 0);
    var totalXg = xgVals.reduce(function (a, b) { return a + b; }, 0);
    var ratio = totalXg ? totalGoals / totalXg : 0;
    var items = [
      ["v accent", rPearson.toFixed(2), "xG↔Goals correlation (r)"],
      ["v blue", totalGoals + " / " + totalXg.toFixed(1), "Goals vs xG (total)"],
      ["v", (ratio * 100).toFixed(0) + "%", "Conversion vs expected"],
      ["v", R.length, "Team-matches analysed"],
    ];
    document.getElementById("xgStats").innerHTML = items.map(function (it) {
      return '<div class="stat"><div class="' + it[0] + '">' + it[1] + '</div><div class="k">' + it[2] + "</div></div>";
    }).join("");
  }

  /* Per team-match scatter (hand-rolled SVG): xG vs actual goals */
  function renderScatter() {
    var host = document.getElementById("scatter");
    if (!R.length) { host.innerHTML = '<p class="hint">Not enough data yet — deep-scrape matches to populate xG.</p>'; return; }
    var W = 560, H = 420, pad = 46;
    var maxV = Math.max(5, Math.ceil(Math.max.apply(null, xgVals.concat(goalVals).concat([1]))));
    function sx(v) { return pad + (v / maxV) * (W - pad - 14); }
    function sy(v) { return H - pad - (v / maxV) * (H - pad - 14); }
    var svg = ['<svg viewBox="0 0 ' + W + " " + H + '" width="100%" class="scatter-svg">'];
    for (var g = 0; g <= maxV; g++) {
      svg.push('<line x1="' + sx(g) + '" y1="' + sy(0) + '" x2="' + sx(g) + '" y2="' + sy(maxV) + '" stroke="#26304d" stroke-width="' + (g === 0 ? 1.4 : 0.5) + '"/>');
      svg.push('<line x1="' + sx(0) + '" y1="' + sy(g) + '" x2="' + sx(maxV) + '" y2="' + sy(g) + '" stroke="#26304d" stroke-width="' + (g === 0 ? 1.4 : 0.5) + '"/>');
      svg.push('<text x="' + sx(g) + '" y="' + (sy(0) + 16) + '" fill="#93a0bd" font-size="10" text-anchor="middle">' + g + "</text>");
      if (g > 0) svg.push('<text x="' + (sx(0) - 8) + '" y="' + (sy(g) + 3) + '" fill="#93a0bd" font-size="10" text-anchor="end">' + g + "</text>");
    }
    svg.push('<line x1="' + sx(0) + '" y1="' + sy(0) + '" x2="' + sx(maxV) + '" y2="' + sy(maxV) + '" stroke="#93a0bd" stroke-width="1.2" stroke-dasharray="5 4"/>');
    svg.push('<line x1="' + sx(0) + '" y1="' + sy(fit.intercept) + '" x2="' + sx(maxV) + '" y2="' + sy(fit.slope * maxV + fit.intercept) + '" stroke="#3ddc97" stroke-width="2"/>');
    svg.push('<text x="' + (W / 2) + '" y="' + (H - 6) + '" fill="#e8edf7" font-size="12" text-anchor="middle">Expected goals (xG)</text>');
    svg.push('<text x="14" y="' + (H / 2) + '" fill="#e8edf7" font-size="12" text-anchor="middle" transform="rotate(-90 14 ' + (H / 2) + ')">Actual goals</text>');
    R.forEach(function (r, i) {
      var jx = ((i * 7) % 5 - 2) * 1.2, jy = ((i * 3) % 5 - 2) * 1.2;
      var cx = sx(r.xgf) + jx, cy = sy(r.gf) + jy, over = r.gf - r.xgf;
      var col = over > 0.4 ? "#3ddc97" : over < -0.4 ? "#ff6b81" : "#4ea1ff";
      svg.push('<circle class="pt" cx="' + cx.toFixed(1) + '" cy="' + cy.toFixed(1) + '" r="5.5" fill="' + col +
        '" fill-opacity="0.78" stroke="#0b0f1a" stroke-width="1" data-team="' + esc(r.team) + '" data-opp="' + esc(r.opp) +
        '" data-g="' + r.gf + '" data-xg="' + r.xgf.toFixed(2) + '"/>');
    });
    svg.push("</svg>");
    host.innerHTML = svg.join("");
    host.querySelectorAll("circle.pt").forEach(function (c) {
      c.addEventListener("mousemove", function (e) {
        tooltip.innerHTML = '<div class="t-team">' + c.dataset.team + " vs " + c.dataset.opp + "</div>" +
          '<div class="t-line">Goals: ' + c.dataset.g + " · xG: " + c.dataset.xg + "</div>";
        tooltip.style.opacity = "1";
        tooltip.style.left = (e.clientX + 14) + "px";
        tooltip.style.top = (e.clientY + 14) + "px";
      });
      c.addEventListener("mouseleave", function () { tooltip.style.opacity = "0"; });
    });
  }

  // Distribution companion to the scatter: bucket every team-game by its xG and
  // show how many games landed in each bucket + whether those chances converted
  // (avg goals vs avg xG per bucket — green over-delivered, red under, blue ≈ even).
  function renderXgDist() {
    var host = document.getElementById("xgDist");
    if (!host) return;
    if (!R.length) { host.innerHTML = '<p class="hint">Not enough data yet — deep-scrape matches to populate xG.</p>'; return; }
    var BW = 0.5, NB = 9;                      // eight half-goal buckets + a "4+" catch-all
    var buckets = [];
    for (var b = 0; b < NB; b++) buckets.push({ n: 0, xg: 0, g: 0 });
    R.forEach(function (r) {
      var i = Math.min(Math.floor(r.xgf / BW), NB - 1);
      buckets[i].n++; buckets[i].xg += r.xgf; buckets[i].g += r.gf;
    });
    var maxN = Math.max.apply(null, buckets.map(function (bk) { return bk.n; }).concat([1]));
    var W = 560, H = 300, padL = 14, padB = 44, padT = 26;
    var slot = (W - padL - 12) / NB, bw = slot - 8;
    function by(n) { return H - padB - (n / maxN) * (H - padB - padT); }
    var svg = ['<svg viewBox="0 0 ' + W + " " + H + '" width="100%" class="scatter-svg">'];
    svg.push('<line x1="' + padL + '" y1="' + (H - padB) + '" x2="' + (W - 8) + '" y2="' + (H - padB) + '" stroke="#26304d" stroke-width="1.2"/>');
    buckets.forEach(function (bk, i) {
      var x = padL + i * slot + 4;
      var lab = (i === NB - 1) ? (BW * (NB - 1)).toFixed(1) + "+" : (BW * i).toFixed(1) + "–" + (BW * (i + 1)).toFixed(1);
      svg.push('<text x="' + (x + bw / 2).toFixed(1) + '" y="' + (H - padB + 14) + '" fill="#93a0bd" font-size="9.5" text-anchor="middle">' + lab + "</text>");
      if (!bk.n) return;
      var ax = bk.xg / bk.n, ag = bk.g / bk.n, d = ag - ax;
      var col = d > 0.15 ? "#3ddc97" : d < -0.15 ? "#ff6b81" : "#4ea1ff";
      var y = by(bk.n);
      var info = lab + " xG · " + bk.n + " team-games · avg xG " + ax.toFixed(2) + " → avg goals " + ag.toFixed(2) +
                 " (" + (d >= 0 ? "+" : "") + d.toFixed(2) + ")";
      svg.push('<rect class="xd" x="' + x.toFixed(1) + '" y="' + y.toFixed(1) + '" width="' + bw.toFixed(1) +
        '" height="' + (H - padB - y).toFixed(1) + '" rx="4" fill="' + col + '" fill-opacity="0.55" stroke="' + col +
        '" stroke-width="1" data-info="' + esc(info) + '"/>');
      svg.push('<text x="' + (x + bw / 2).toFixed(1) + '" y="' + (y - 6).toFixed(1) + '" fill="#e8edf7" font-size="11" font-weight="700" text-anchor="middle">' + bk.n + "</text>");
      svg.push('<text x="' + (x + bw / 2).toFixed(1) + '" y="' + (H - padB + 27) + '" fill="' + col + '" font-size="9.5" text-anchor="middle">' + ax.toFixed(1) + "→" + ag.toFixed(1) + "</text>");
    });
    svg.push('<text x="' + (W / 2) + '" y="' + (H - 3) + '" fill="#93a0bd" font-size="11" text-anchor="middle">xG created in the game · below each bar: avg xG → avg goals actually scored</text>');
    svg.push("</svg>");
    host.innerHTML = svg.join("");
    host.querySelectorAll("rect.xd").forEach(function (c) {
      c.addEventListener("mousemove", function (e) {
        tooltip.innerHTML = '<div class="t-line">' + c.getAttribute("data-info") + "</div>";
        tooltip.style.opacity = "1";
        tooltip.style.left = (e.clientX + 14) + "px";
        tooltip.style.top = (e.clientY + 14) + "px";
      });
      c.addEventListener("mouseleave", function () { tooltip.style.opacity = "0"; });
    });
  }

  function renderCorr() {
    var box = document.getElementById("corrBox"), ins = document.getElementById("corrInsight");
    if (!R.length) { box.innerHTML = '<p class="hint">No xG data yet.</p>'; ins.innerHTML = ""; return; }
    var rr = rPearson, r2 = rr * rr;
    var strength = rr > 0.75 ? "strong" : rr > 0.5 ? "moderate" : rr > 0.3 ? "modest" : "weak";
    var scalePct = Math.max(0, Math.min(100, rr * 100));
    box.innerHTML =
      '<div class="r-row"><span class="r-big">' + rr.toFixed(2) + '</span><span class="lab">link strength (0 = none, 1 = perfect) — <b>' + strength + '</b></span></div>' +
      '<div class="corr-scale"><div class="corr-scale-fill" style="width:' + scalePct.toFixed(0) + '%"></div></div>' +
      '<div class="r-row" style="margin-top:12px"><span style="font-size:22px;font-weight:800;color:var(--accent-2)">' +
        (r2 * 100).toFixed(0) + '%</span><span class="lab">of the difference in goals is explained by chance quality (xG). The rest is finishing skill, goalkeeping &amp; luck.</span></div>' +
      '<div class="r-row"><span style="font-size:18px;font-weight:700">' + fit.slope.toFixed(2) +
        '</span><span class="lab">goals scored, on average, for every 1.0 xG of chances created</span></div>';
    ins.innerHTML =
      "In plain terms: across the <b>" + R.length + "</b> team-performances this season, teams that created better " +
      "chances did tend to score more — a <b>" + strength + "</b> relationship. But it's far from one-to-one, which is " +
      "exactly why upsets happen: on any given day finishing and luck can override who created the better chances. " +
      "Each 1.0 xG has turned into about <b>" + fit.slope.toFixed(2) + " goals</b>, so teams are finishing slightly <b>" +
      (fit.slope < 1 ? "below" : "above") + "</b> their chances overall.";
  }

  function renderQuadrant() {
    var src = AGG.filter(function (a) { return a.n > 0; });
    if (!src.length) { teamScatter("quadrant", [], {}); return; }
    var avgF = src.reduce(function (s, a) { return s + a.xgf / a.n; }, 0) / src.length;
    var avgA = src.reduce(function (s, a) { return s + a.xga / a.n; }, 0) / src.length;
    var rows = src.map(function (a) {
      var fwd = a.xgf / a.n, def = a.xga / a.n;
      var attGood = fwd >= avgF, defGood = def <= avgA;
      var col = attGood && defGood ? COL.green : !attGood && defGood ? COL.blue : attGood && !defGood ? COL.orange : COL.red;
      return { team: a.team, x: fwd, y: def, col: col, _f: fwd, _d: def };
    });
    teamScatter("quadrant", rows, {
      h: 580, flipY: true, avgX: avgF, avgY: avgA,
      xLabel: "xG created per game  →  (more dangerous attack)",
      yLabel: "xG conceded per game  (higher up = meaner defence)",
      corners: [
        { h: "r", v: "t", text: "Strong both ends ↗", color: COL.green },
        { h: "l", v: "t", text: "↖ Defence-first", color: COL.blue },
        { h: "r", v: "b", text: "All-out attack ↘", color: COL.orange },
        { h: "l", v: "b", text: "↙ Struggling", color: COL.red }
      ],
      tip: function (r) { return "create " + r._f.toFixed(2) + " / concede " + r._d.toFixed(2) + " xG per game"; },
      legend: chartLegend([
        [COL.green, "Strong both ends — good attack &amp; mean defence"],
        [COL.blue, "Defence-first — solid at the back, blunt up front"],
        [COL.orange, "All-out attack — dangerous but leaky"],
        [COL.red, "Struggling — out-created at both ends"]
      ], "Dashed lines mark the league average for each axis.")
    });
  }

  function matchXpts(lh, la) {
    var pw = 0, pd = 0, pl = 0;
    for (var i = 0; i <= 8; i++) for (var j = 0; j <= 8; j++) {
      var p = poisP(i, lh) * poisP(j, la);
      if (i > j) pw += p; else if (i === j) pd += p; else pl += p;
    }
    return [3 * pw + pd, 3 * pl + pd];
  }
  function teamPoints() {
    var t = {};
    function g(n) { return t[n] || (t[n] = { team: n, pts: 0, xpts: 0, n: 0 }); }
    D.matches.forEach(function (m) {
      if (!m.played || m.xg_home == null) return;
      var H = g(m.home), A = g(m.away);
      H.n++; A.n++;
      H.pts += m.hs > m.as ? 3 : m.hs === m.as ? 1 : 0;
      A.pts += m.as > m.hs ? 3 : m.hs === m.as ? 1 : 0;
      var xp = matchXpts(m.xg_home, m.xg_away);
      H.xpts += xp[0]; A.xpts += xp[1];
    });
    return Object.keys(t).map(function (k) { return t[k]; });
  }
  function renderXpts() {
    var src = teamPoints();
    var ins = document.getElementById("xptsInsight");
    if (!src.length) { teamScatter("xpts", [], {}); ins.innerHTML = ""; return; }
    var rows = src.map(function (r) {
      var d = r.pts - r.xpts;
      return { team: r.team, x: r.xpts, y: r.pts, col: d > 0.5 ? COL.green : d < -0.5 ? COL.red : COL.blue, _d: d };
    });
    teamScatter("xpts", rows, {
      h: 560, diagonal: true,
      xLabel: "Expected points from xG  →  (what the chances were worth)",
      yLabel: "Actual points won",
      tip: function (r) { return r.y + " pts vs " + r.x.toFixed(1) + " deserved (" + (r._d >= 0 ? "+" : "") + r._d.toFixed(1) + ")"; },
      legend: chartLegend([
        [COL.green, "Over-performing — more points than the chances deserved (clinical or lucky)"],
        [COL.blue, "About right — points roughly match performances"],
        [COL.red, "Under-performing — fewer points than deserved (wasteful or unlucky)"]
      ], "Dashed line = got exactly the points the xG says they earned. Above it = lucky, below = unlucky.")
    });
    var rr = src.slice().sort(function (a, b) { return (b.pts - b.xpts) - (a.pts - a.xpts); });
    var lucky = rr[0], unlucky = rr[rr.length - 1];
    ins.innerHTML =
      "Biggest over-achiever so far: <b>" + esc(lucky.team) + "</b> (+" + (lucky.pts - lucky.xpts).toFixed(1) +
      " pts vs expected). Most unlucky: <b>" + esc(unlucky.team) + "</b> (" + (unlucky.pts - unlucky.xpts).toFixed(1) +
      "). Expected points come from a Poisson model on each match's xG.";
  }

  function teamShotAgg() {
    var t = {};
    function g(n) { return t[n] || (t[n] = { team: n, shots: 0, xg: 0, n: 0 }); }
    D.matches.forEach(function (m) {
      if (!m.played || !m.has_stats) return;
      var s = m.stats;
      if (!s || s.shots[0] == null) return;
      var H = g(m.home), A = g(m.away);
      H.shots += s.shots[0] || 0; H.xg += s.xg[0] || 0; H.n++;
      A.shots += s.shots[1] || 0; A.xg += s.xg[1] || 0; A.n++;
    });
    return Object.keys(t).map(function (k) { return t[k]; }).filter(function (r) { return r.shots > 0; });
  }
  function renderShotQuality() {
    var src = teamShotAgg();
    if (!src.length) { teamScatter("shotquality", [], {}); return; }
    src.forEach(function (r) { r.spg = r.shots / r.n; r.xgPerShot = r.xg / r.shots; });
    var avgX = src.reduce(function (s, r) { return s + r.spg; }, 0) / src.length;
    var avgY = src.reduce(function (s, r) { return s + r.xgPerShot; }, 0) / src.length;
    var rows = src.map(function (r) {
      var hiVol = r.spg >= avgX, hiQual = r.xgPerShot >= avgY;
      var col = hiVol && hiQual ? COL.green : !hiVol && hiQual ? COL.blue : hiVol && !hiQual ? COL.orange : COL.red;
      return { team: r.team, x: r.spg, y: r.xgPerShot, col: col, _s: r.spg, _q: r.xgPerShot };
    });
    teamScatter("shotquality", rows, {
      h: 560, avgX: avgX, avgY: avgY, centerAvg: true,
      xLabel: "Shots per game  →  (volume)",
      yLabel: "xG per shot  (chance quality)",
      corners: [
        { h: "r", v: "t", text: "Lots of great chances ↗", color: COL.green },
        { h: "l", v: "t", text: "↖ Few but excellent", color: COL.blue },
        { h: "r", v: "b", text: "High volume, long-range ↘", color: COL.orange },
        { h: "l", v: "b", text: "↙ Creating little", color: COL.red }
      ],
      tip: function (r) { return r._s.toFixed(1) + " shots/game · " + r._q.toFixed(2) + " xG per shot"; },
      legend: chartLegend([
        [COL.green, "Lots of high-quality chances — volume + quality"],
        [COL.blue, "Fewer but excellent looks — picky, high quality"],
        [COL.orange, "High volume, lower quality — lots of long-range shots"],
        [COL.red, "Creating little — low volume and low quality"]
      ], "Dashed lines mark the league average for each axis.")
    });
  }

  function renderHomeAway() {
    var host = document.getElementById("homeAway");
    var h = R.filter(function (r) { return r.home; }), a = R.filter(function (r) { return !r.home; });
    if (!h.length || !a.length) { host.innerHTML = '<p class="hint">No home/away xG data yet.</p>'; return; }
    function avg(arr, k) { return arr.reduce(function (s, r) { return s + r[k]; }, 0) / arr.length; }
    var hx = avg(h, "xgf"), ax = avg(a, "xgf"), hg = avg(h, "gf"), ag = avg(a, "gf");
    var mx = Math.max(hx, ax) * 1.15;
    function bar(label, hv, av) {
      return '<div style="margin-bottom:14px"><div style="font-size:12px;color:var(--muted);margin-bottom:5px">' + label + "</div>" +
        '<div class="ha-row"><span class="ha-lab">Home</span><div class="ha-track"><div class="ha-fill" style="width:' + (100 * hv / mx).toFixed(1) + '%;background:var(--accent)"></div></div><b>' + hv.toFixed(2) + "</b></div>" +
        '<div class="ha-row"><span class="ha-lab">Away</span><div class="ha-track"><div class="ha-fill" style="width:' + (100 * av / mx).toFixed(1) + '%;background:var(--accent-2)"></div></div><b>' + av.toFixed(2) + "</b></div></div>";
    }
    var diff = ((hx - ax) >= 0 ? "+" : "") + (hx - ax).toFixed(2);
    host.innerHTML =
      bar("Average xG created per game", hx, ax) +
      bar("Average goals scored per game", hg, ag) +
      '<div class="insight">Home teams create <b>' + diff + ' xG</b> more per game than away teams across ' +
      h.length + " home and " + a.length + " away team-matches.</div>";
  }

  function renderUnlucky() {
    var host = document.getElementById("unlucky");
    if (!host) return;
    var rows = D.matches.filter(function (m) { return m.played && m.xg_home != null; }).map(function (m) {
      var hWon = m.hs > m.as, aWon = m.as > m.hs, adv = 0, who = "";
      if (m.xg_home > m.xg_away && !hWon) { adv = m.xg_home - m.xg_away; who = m.home; }
      else if (m.xg_away > m.xg_home && !aWon) { adv = m.xg_away - m.xg_home; who = m.away; }
      return { m: m, adv: adv, who: who };
    }).filter(function (x) { return x.adv > 0; })
      .sort(function (a, b) { return b.adv - a.adv; }).slice(0, 10);
    if (!rows.length) { host.innerHTML = '<p class="hint">No unlucky results yet.</p>'; return; }
    host.innerHTML = '<table class="rank"><thead><tr><th class="team">Match</th><th>Result</th><th>xG</th>' +
      '<th class="team">Deserved more</th><th>xG edge</th></tr></thead><tbody>' +
      rows.map(function (x) {
        var m = x.m;
        return "<tr><td class='team'>" + esc(m.home) + " v " + esc(m.away) + "</td>" +
          "<td>" + m.hs + "–" + m.as + "</td><td>" + m.xg_home.toFixed(2) + "–" + m.xg_away.toFixed(2) + "</td>" +
          "<td class='team'>" + esc(x.who) + "</td>" +
          "<td><span class='delta pos'>+" + x.adv.toFixed(2) + "</span></td></tr>";
      }).join("") + "</tbody></table>";
  }

  function renderFinishingBars() {
    var host = document.getElementById("finishingBars");
    var rows = AGG.slice().sort(function (a, b) { return b.attDelta - a.attDelta; });
    if (!rows.length) { host.innerHTML = '<p class="hint">No xG data yet.</p>'; return; }
    var maxAbs = Math.max.apply(null, rows.map(function (r) { return Math.abs(r.attDelta); }).concat([1]));
    host.innerHTML = rows.map(function (r) {
      var d = r.attDelta, pct = (Math.abs(d) / maxAbs) * 50;
      var fill = '<div class="bar-fill ' + (d >= 0 ? "pos" : "neg") + '" style="width:' + pct.toFixed(1) + '%"></div>';
      return '<div class="bar-row"><div class="nm">' + logoImg(r.team) + "<span>" + esc(r.team) +
        '</span></div><div class="bar-track"><div class="bar-mid"></div>' + fill + "</div>" +
        '<div class="bar-val ' + (d >= 0 ? "pos" : "neg") + '">' + (d >= 0 ? "+" : "") + d.toFixed(2) + "</div></div>";
    }).join("");
  }

  function renderLedger() {
    var host = document.getElementById("ledger");
    var cols = [["team", "Team"], ["n", "MP"], ["gf", "G"], ["xgf", "xG"], ["attDelta", "G−xG"],
      ["ga", "GA"], ["xga", "xGA"], ["defDelta", "xGA−GA"], ["xgd", "xGD"]];
    var rows = AGG.slice().sort(function (a, b) {
      var k = ledgerSort.key;
      if (k === "team") return ledgerSort.dir * a.team.localeCompare(b.team);
      return ledgerSort.dir * (a[k] - b[k]);
    });
    if (!rows.length) { host.innerHTML = '<p class="hint">No xG data yet.</p>'; return; }
    var head = cols.map(function (c) {
      var arr = ledgerSort.key === c[0] ? (ledgerSort.dir < 0 ? " ▼" : " ▲") : "";
      return '<th class="' + (c[0] === "team" ? "team" : "") + '" data-k="' + c[0] + '">' + c[1] + '<span class="arr">' + arr + "</span></th>";
    }).join("");
    var body = rows.map(function (r) {
      function cell(k) {
        if (k === "team") return '<td class="team"><div class="team-cell">' + logoImg(r.team) + '<span class="nm">' + esc(r.team) + "</span></div></td>";
        if (k === "n" || k === "gf" || k === "ga") return "<td>" + r[k] + "</td>";
        if (k === "xgf" || k === "xga") return "<td>" + r[k].toFixed(2) + "</td>";
        var v = r[k], cls = v > 0.05 ? "pos" : v < -0.05 ? "neg" : "";
        return '<td><span class="delta ' + cls + '">' + (v >= 0 ? "+" : "") + v.toFixed(2) + "</span></td>";
      }
      return "<tr>" + cols.map(function (c) { return cell(c[0]); }).join("") + "</tr>";
    }).join("");
    host.innerHTML = '<table class="rank"><thead><tr>' + head + "</tr></thead><tbody>" + body + "</tbody></table>";
    host.querySelectorAll("th").forEach(function (th) {
      th.addEventListener("click", function () {
        var k = th.dataset.k;
        if (ledgerSort.key === k) ledgerSort.dir *= -1;
        else { ledgerSort.key = k; ledgerSort.dir = k === "team" ? 1 : -1; }
        renderLedger();
      });
    });
  }

  function renderAgreement() {
    var host = document.getElementById("agreement");
    var matches = D.matches.filter(function (m) { return m.played && m.xg_home != null; });
    if (!matches.length) { host.innerHTML = '<p class="hint">No xG data yet.</p>'; return; }
    var agree = 0, total = matches.length, draws = 0;
    var rows = matches.map(function (m) {
      var xgWin = m.xg_home > m.xg_away ? "H" : m.xg_home < m.xg_away ? "A" : "D";
      var actWin = m.hs > m.as ? "H" : m.hs < m.as ? "A" : "D";
      var ok = xgWin === actWin;
      if (ok) agree++;
      if (actWin === "D") draws++;
      return { m: m, ok: ok, xgName: xgWin === "H" ? m.home : xgWin === "A" ? m.away : "Even" };
    }).sort(function (a, b) { return (a.ok === b.ok) ? 0 : a.ok ? 1 : -1; });
    var pct = total ? Math.round((agree / total) * 100) : 0;
    host.innerHTML = '<div class="stats-strip" style="margin-bottom:16px">' +
      '<div class="stat"><div class="v accent">' + pct + '%</div><div class="k">xG winner = actual result</div></div>' +
      '<div class="stat"><div class="v">' + agree + " / " + total + '</div><div class="k">matches in agreement</div></div>' +
      '<div class="stat"><div class="v blue">' + draws + '</div><div class="k">actual draws (hard for xG)</div></div></div>' +
      '<table class="rank"><thead><tr><th class="team">Match</th><th class="team">xG favoured</th><th>xG</th><th>Result</th><th>Match</th></tr></thead><tbody>' +
      rows.map(function (x) {
        var m = x.m;
        var mark = x.ok ? '<span style="color:var(--good)">✔ matched</span>' : '<span style="color:var(--bad)">✘ upset</span>';
        return "<tr><td class='team'>" + esc(m.home) + " v " + esc(m.away) + "</td>" +
          "<td class='team'>" + esc(x.xgName) + "</td>" +
          "<td>" + m.xg_home.toFixed(2) + "–" + m.xg_away.toFixed(2) + "</td>" +
          "<td>" + m.hs + "–" + m.as + "</td><td>" + mark + "</td></tr>";
      }).join("") + "</tbody></table>";
  }

  function renderXgLab() {
    computeXgDerived();
    renderXgStats();
    renderScatter();
    renderCorr();
    renderXgDist();
    renderFinishingBars();
    renderQuadrant();
    renderXpts();
    renderShotQuality();
    renderHomeAway();
    renderLedger();
    renderAgreement();
    renderUnlucky();
  }

  /* ================= STANDOUTS (player leaderboards) ================= */
  function renderPlayerLeaders() {
    var wrap = document.getElementById("playerLeaders");
    if (!PLAYERS.length) { wrap.innerHTML = '<p class="hint">Player data populates as matches are deep-scraped.</p>'; return; }
    function top(key, label, fmt) {
      var arr = PLAYERS.filter(function (p) { return p[key] != null; }).sort(function (a, b) { return b[key] - a[key]; });
      var p = arr[0];
      if (!p) return "";
      var v = fmt ? fmt(p[key]) : p[key];
      return '<div class="stat"><div class="v accent">' + v + '</div><div class="k">' + label +
        '<br><span style="color:var(--text)">' + esc(p.name) + "</span> · " + esc(p.team) + "</div></div>";
    }
    wrap.innerHTML = top("g", "Top scorer") + top("a", "Most assists") +
      top("xg", "Highest xG", function (v) { return v.toFixed(2); }) +
      top("rating", "Best avg rating", function (v) { return v.toFixed(2); });
  }
  function renderPlayerBoards() {
    var host = document.getElementById("playerBoards");
    if (!host) return;
    if (!PLAYERS.length) { host.innerHTML = '<p class="hint">Player data populates as matches are deep-scraped.</p>'; return; }
    function rows(list, valFn, subFn, cls) {
      var html = list.slice(0, 8).map(function (p) {
        return '<div class="fin-row"><div class="nm">' + logoImg(p.team) + "<span>" + esc(p.name) +
          '</span></div><div class="fin-stat">' + (subFn ? '<span class="sub">' + subFn(p) + "</span>" : "") +
          '<span class="lb-val ' + (cls || "") + '">' + valFn(p) + "</span></div></div>";
      }).join("");
      return html || '<p class="hint">Not enough data yet.</p>';
    }
    function card(title, hint, body) {
      return '<div class="card lboard"><h3>' + title + '</h3><p class="hint">' + hint + "</p>" + body + "</div>";
    }
    function desc(key) {
      return PLAYERS.slice().filter(function (p) { return p[key] != null; }).sort(function (a, b) { return b[key] - a[key]; });
    }
    function per90(filterFn, rateFn) {
      return PLAYERS.filter(filterFn).map(function (p) { return Object.assign({}, p, { _r: rateFn(p) }); })
        .sort(function (a, b) { return b._r - a._r; });
    }
    var fin = PLAYERS.filter(function (p) { return p.xg >= 1.0; });
    var rated = PLAYERS.filter(function (p) { return p.mp >= 2 && p.rating != null; }).sort(function (a, b) { return b.rating - a.rating; });
    var xgSub = function (p) { return p.g + "G vs " + p.xg.toFixed(2) + " xG"; };
    var shooters = PLAYERS.filter(function (p) { return p.shots >= 5; })
      .map(function (p) { return Object.assign({}, p, { conv: Math.round(p.g / p.shots * 100) }); })
      .sort(function (a, b) { return b.conv - a.conv; });
    var goalsPer90 = per90(function (p) { return p.mins >= 450 && p.g > 0; }, function (p) { return p.g / p.mins * 90; });
    var dribblers = per90(function (p) { return p.mins >= 450 && p.dribbles > 0; }, function (p) { return p.dribbles / p.mins * 90; });
    var chancesPer90 = per90(function (p) { return p.mins >= 450 && p.keyPasses > 0; }, function (p) { return p.keyPasses / p.mins * 90; });
    var passersPer90 = per90(function (p) { return p.mins >= 450 && p.passes > 0; }, function (p) { return p.passes / p.mins * 90; });
    var tacklersPer90 = per90(function (p) { return p.mins >= 450 && p.tackles > 0; }, function (p) { return p.tackles / p.mins * 90; });
    var boards = [
      card("Top scorers", "Goals scored.", rows(desc("g"), function (p) { return p.g; }, function (p) { return p.team; })),
      card("Goals per 90'", "Goals per 90 minutes, min. 450 mins.", rows(goalsPer90, function (p) { return p._r.toFixed(2); }, function (p) { return p.g + "G"; })),
      card("Dribbles per 90'", "Successful dribbles per 90, min. 450 mins.", rows(dribblers, function (p) { return p._r.toFixed(1); }, function (p) { return p.dribbles + " total"; })),
      card("Best shot conversion", "Goals per shot %, min. 5 attempts.", rows(shooters, function (p) { return p.conv + "%"; }, function (p) { return p.g + "G / " + p.shots + " shots"; })),
      card("Most assists", "Assists provided.", rows(desc("a"), function (p) { return p.a; }, function (p) { return p.team; })),
      card("Goal involvements", "Goals + assists combined.", rows(desc("ga"), function (p) { return p.g + p.a; }, function (p) { return p.g + "G " + p.a + "A"; })),
      card("Highest average rating", "Match rating, min. 2 games.", rows(rated, function (p) { return p.rating.toFixed(2); }, function (p) { return p.mp + " gms"; })),
      card("Most clinical finishers", "Goals above shot xG (min. 1.0 xG faced).",
        rows(fin.slice().sort(function (a, b) { return b.xg_diff - a.xg_diff; }), function (p) { return (p.xg_diff > 0 ? "+" : "") + p.xg_diff.toFixed(2); }, xgSub, "pos")),
      card("Wasteful in front of goal", "Goals below shot xG (min. 1.0 xG faced).",
        rows(fin.slice().sort(function (a, b) { return a.xg_diff - b.xg_diff; }), function (p) { return p.xg_diff.toFixed(2); }, xgSub, "neg")),
      card("Chances created per 90'", "Key passes per 90, min. 450 mins.", rows(chancesPer90, function (p) { return p._r.toFixed(2); }, function (p) { return p.keyPasses + " total"; })),
      card("Most shots on target", "Shots that hit the target.", rows(desc("sot"), function (p) { return p.sot; }, function (p) { return p.shots + " shots"; })),
      card("Most shots taken", "Total attempts.", rows(desc("shots"), function (p) { return p.shots; }, function (p) { return p.team; })),
      card("Passes per 90'", "Passes per 90, min. 450 mins.", rows(passersPer90, function (p) { return Math.round(p._r); }, function (p) { return p.pass_pct + "%"; })),
      card("Tackles per 90'", "Tackles per 90, min. 450 mins.", rows(tacklersPer90, function (p) { return p._r.toFixed(1); }, function (p) { return p.tackles + " total"; })),
    ];
    host.innerHTML = boards.join("");
  }
  /* ---- Standouts distribution view (KDE density + anomalies + 2-stat scatter + radar).
     Ported from the WC2026 dashboard; stat / preset / radar lists trimmed to the
     metrics the Premier League players.js carries. All client-side from window.LL_PLAYERS. ---- */
  var SO_STATS = [
    ["ga", "Goals + assists", 0], ["g", "Goals", 0], ["a", "Assists", 0],
    ["xg", "Expected goals (xG)", 2], ["xg_diff", "Finishing (goals − xG)", 2],
    ["xa", "Expected assists (xA)", 2], ["xgi", "xG involvement (xG + xA)", 2],
    ["shots", "Shots", 0], ["sot", "Shots on target", 0], ["keyPasses", "Key passes", 0],
    ["dribbles", "Dribbles completed", 0], ["passes", "Passes", 0], ["pass_pct", "Pass accuracy %", 0],
    ["tackles", "Tackles", 0], ["interceptions", "Interceptions", 0], ["clearances", "Clearances", 0],
    ["aerials", "Aerials won", 0], ["fouls", "Fouls", 0], ["dispossessed", "Dispossessed", 0],
    ["saves", "Saves", 0], ["touches", "Touches", 0], ["rating", "Average match rating", 2]
  ];
  var SO_POS_LABEL = { FWD: "attackers", MID: "midfielders", DEF: "defenders", GK: "goalkeepers" };
  var soState = { stat: "ga", pos: "all", mins: 450, player: "" };

  function soPosGroup(pos) {
    var s = (pos || "").toUpperCase();
    if (s === "GK") return "GK";
    if (s[0] === "F" || s === "ST" || s === "CF" || s[0] === "A") return "FWD";
    if (s[0] === "M" || s.indexOf("DM") === 0) return "MID";
    if (s[0] === "D" || s[0] === "W" || s === "B") return "DEF";
    return "OTH";
  }
  function soFmt(v, dp) { return dp ? (+v).toFixed(dp) : Math.round(v); }
  function normPdf(z) { return Math.exp(-0.5 * z * z) / 2.5066282746310002; }
  function soStatMeta() {
    for (var i = 0; i < SO_STATS.length; i++) if (SO_STATS[i][0] === soState.stat) return SO_STATS[i];
    return SO_STATS[0];
  }
  function soQualify() {
    return PLAYERS.filter(function (p) {
      if ((p.mins || 0) < soState.mins) return false;
      if (soState.pos !== "all" && soPosGroup(p.pos) !== soState.pos) return false;
      if (soState.stat === "rating" && !(p.rating > 0)) return false;
      return true;
    });
  }

  function soDistChart(rows, statKey, dp, spotPid, mean, sd) {
    var W = 880, H = 380, padL = 22, padR = 22, padT = 20, padB = 50;
    var plotW = W - padL - padR, plotH = H - padT - padB;
    var vals = rows.map(function (p) { return +p[statKey] || 0; });
    var n = vals.length;
    var lo = Math.min.apply(null, vals), hi = Math.max.apply(null, vals);
    var span = (hi - lo) || 1;
    var xMin = statKey === "rating" ? lo - span * 0.06 : Math.min(lo, 0) - span * 0.03;
    var xMax = hi + span * 0.10;
    function sx(v) { return padL + plotW * (v - xMin) / (xMax - xMin); }
    var baseY = padT + plotH;
    var h = 1.06 * (sd || span * 0.1) * Math.pow(n, -0.2);
    if (!(h > 0)) h = span * 0.08;
    var GRID = 140, dens = [], maxD = 0;
    for (var i = 0; i <= GRID; i++) {
      var x = xMin + (xMax - xMin) * i / GRID, d = 0;
      for (var j = 0; j < n; j++) d += normPdf((x - vals[j]) / h);
      d /= (n * h);
      dens.push(d);
      if (d > maxD) maxD = d;
    }
    function densInterp(v) {
      var t = (v - xMin) / (xMax - xMin) * GRID;
      var i = Math.max(0, Math.min(GRID - 1, Math.floor(t))), frac = t - i;
      return dens[i] * (1 - frac) + dens[i + 1] * frac;
    }
    function sy(d) { return baseY - (maxD ? d / maxD : 0) * plotH; }
    var svg = ['<svg viewBox="0 0 ' + W + ' ' + H + '" class="so-chart" preserveAspectRatio="xMidYMid meet" role="img">'];
    var area = "M " + sx(xMin).toFixed(1) + " " + baseY.toFixed(1);
    for (var k = 0; k <= GRID; k++) area += " L " + sx(xMin + (xMax - xMin) * k / GRID).toFixed(1) + " " + sy(dens[k]).toFixed(1);
    area += " L " + sx(xMax).toFixed(1) + " " + baseY.toFixed(1) + " Z";
    svg.push('<path d="' + area + '" fill="rgba(78,161,255,0.10)" stroke="none"/>');
    var line = "";
    for (var k2 = 0; k2 <= GRID; k2++) line += (k2 ? " L " : "M ") + sx(xMin + (xMax - xMin) * k2 / GRID).toFixed(1) + " " + sy(dens[k2]).toFixed(1);
    svg.push('<path d="' + line + '" fill="none" stroke="#8aa0d8" stroke-width="1.4" stroke-opacity="0.85"/>');
    svg.push('<line x1="' + padL + '" y1="' + baseY.toFixed(1) + '" x2="' + (W - padR) + '" y2="' + baseY.toFixed(1) + '" stroke="#26304d" stroke-width="1"/>');
    niceTicks(xMax, 6).forEach(function (t) {
      if (t < xMin - 1e-9 || t > xMax + 1e-9) return;
      svg.push('<line x1="' + sx(t).toFixed(1) + '" y1="' + baseY.toFixed(1) + '" x2="' + sx(t).toFixed(1) + '" y2="' + (baseY + 4).toFixed(1) + '" stroke="#46527a" stroke-width="1"/>');
      svg.push('<text x="' + sx(t).toFixed(1) + '" y="' + (baseY + 17) + '" fill="#7c89a8" font-size="10.5" text-anchor="middle">' + fmtTick(t) + "</text>");
    });
    var ax = sx(mean);
    svg.push('<line x1="' + ax.toFixed(1) + '" y1="' + padT + '" x2="' + ax.toFixed(1) + '" y2="' + baseY.toFixed(1) + '" stroke="#cfd8ee" stroke-width="1.2" stroke-dasharray="5 4" stroke-opacity="0.7"/>');
    svg.push('<text x="' + ax.toFixed(1) + '" y="' + (padT - 6) + '" fill="#cfd8ee" font-size="11" text-anchor="middle">average ' + soFmt(mean, dp || 1) + "</text>");
    function jit(pid) { var s = Math.sin((pid + 1) * 12.9898) * 43758.5453; return s - Math.floor(s); }
    rows.forEach(function (p) {
      var v = +p[statKey] || 0, z = sd ? (v - mean) / sd : 0;
      var dx = sx(v), band = (maxD ? densInterp(v) / maxD : 0) * plotH;
      var dy = baseY - 4 - jit(p.pid) * Math.max(6, band - 6);
      var isSpot = spotPid && p.pid === spotPid, anom = z >= 2;
      var r = isSpot ? 5.5 : anom ? 3.4 : 2.3;
      var fill = isSpot ? "#ffd24d" : anom ? "#ff3d8b" : "#4ea1ff";
      var op = isSpot ? 1 : anom ? 0.92 : 0.5;
      var stroke = (isSpot || anom) ? ' stroke="#0b0f1a" stroke-width="0.8"' : "";
      var info = p.name + " · " + p.team + " — " + soFmt(v, dp) + " (" + (z >= 0 ? "+" : "") + z.toFixed(1) + "σ)";
      svg.push('<circle cx="' + dx.toFixed(1) + '" cy="' + dy.toFixed(1) + '" r="' + r + '" fill="' + fill + '" fill-opacity="' + op + '"' + stroke + ' data-info="' + esc(info) + '"></circle>');
    });
    var labels = [];
    rows.slice().sort(function (a, b) { return (+b[statKey] || 0) - (+a[statKey] || 0); })
      .slice(0, 5).forEach(function (p) {
        var v = +p[statKey] || 0, z = sd ? (v - mean) / sd : 0;
        if (z < 1.2) return;
        labels.push({ x: sx(v), y: baseY - 6 - (maxD ? densInterp(v) / maxD : 0) * plotH, txt: p.name, gold: false });
      });
    if (spotPid) {
      var sp = rows.filter(function (p) { return p.pid === spotPid; })[0];
      if (sp) {
        var v = +sp[statKey] || 0;
        labels.push({ x: sx(v), y: baseY - 6 - (maxD ? densInterp(v) / maxD : 0) * plotH, txt: sp.name, gold: true });
      }
    }
    labels.sort(function (a, b) { return a.x - b.x; });
    var lastX = -999, tier = 0;
    labels.forEach(function (L) {
      tier = (L.x - lastX < 86) ? tier + 1 : 0; lastX = L.x;
      var ly = Math.max(padT + 6, L.y - 8 - tier * 13);
      var lx = Math.max(padL + 18, Math.min(W - padR - 18, L.x));
      svg.push('<line x1="' + L.x.toFixed(1) + '" y1="' + L.y.toFixed(1) + '" x2="' + lx.toFixed(1) + '" y2="' + ly.toFixed(1) + '" stroke="' + (L.gold ? "#ffd24d" : "#ff3d8b") + '" stroke-width="0.7" stroke-opacity="0.6"/>');
      svg.push('<text x="' + lx.toFixed(1) + '" y="' + (ly - 3).toFixed(1) + '" fill="' + (L.gold ? "#ffe08a" : "#ffaecb") + '" font-size="10.5" text-anchor="middle">' + esc(L.txt) + "</text>");
    });
    svg.push("</svg>");
    return svg.join("");
  }

  function renderStandouts() {
    if (!document.getElementById("view-standouts")) return;
    var setHTML = function (id, h) { var e = document.getElementById(id); if (e) e.innerHTML = h; };
    var meta = soStatMeta(), statKey = meta[0], label = meta[1], dp = meta[2];
    var rows = soQualify();
    setHTML("soChartTitle", label + " — distribution across " + rows.length + " players");
    setHTML("soChartHint", "Each dot is one player with " + (soState.mins ? soState.mins + "+ minutes" : "any minutes") +
      (soState.pos === "all" ? "" : " · " + SO_POS_LABEL[soState.pos]) + ". Pink = 2σ or more above average.");
    if (!rows.length) {
      setHTML("soChart", '<p class="hint">No players match these filters — try lowering the minimum minutes.</p>');
      ["soStats", "soStandouts", "soSpotlight"].forEach(function (id) { setHTML(id, ""); });
      return;
    }
    if (!(statKey in rows[0])) {
      setHTML("soChart", '<p class="hint">"' + esc(label) + '" isn\'t in the current data.</p>');
      ["soStats", "soStandouts", "soSpotlight"].forEach(function (id) { setHTML(id, ""); });
      return;
    }
    var vals = rows.map(function (p) { return +p[statKey] || 0; }), n = vals.length;
    var mean = vals.reduce(function (s, v) { return s + v; }, 0) / n;
    var sd = Math.sqrt(vals.reduce(function (s, v) { return s + (v - mean) * (v - mean); }, 0) / n);
    var sorted = rows.slice().sort(function (a, b) { return (+b[statKey] || 0) - (+a[statKey] || 0); });
    var leader = sorted[0], leadZ = sd ? ((+leader[statKey] || 0) - mean) / sd : 0;
    var spot = null;
    if (soState.player) {
      var q = soState.player.toLowerCase();
      spot = rows.filter(function (p) { return p.name.toLowerCase() === q; })[0] ||
        rows.filter(function (p) { return p.name.toLowerCase().indexOf(q) >= 0; })[0] || null;
    }
    var spotPid = spot ? spot.pid : null;
    var anomCount = rows.filter(function (p) { return sd && ((+p[statKey] || 0) - mean) / sd >= 2; }).length;
    var items = [
      ["v accent", soFmt(mean, dp || 1), "Average"],
      ["v blue", soFmt(sd, dp || 1), "Std dev (σ)"],
      ["v", n, "Players"],
      ["v", anomCount, "Anomalies (2σ+)"],
      ["v accent", soFmt(+leader[statKey] || 0, dp) + " <span style='font-size:13px;color:var(--muted)'>" + esc(leader.name) + "</span>", "Highest value"],
      ["v", "+" + leadZ.toFixed(1) + "σ", "Leader vs average"],
    ];
    setHTML("soStats", items.map(function (it) {
      return '<div class="stat"><div class="' + it[0] + '">' + it[1] + '</div><div class="k">' + it[2] + "</div></div>";
    }).join(""));
    setHTML("soChart", soDistChart(rows, statKey, dp, spotPid, mean, sd));
    if (spot) {
      var sv = +spot[statKey] || 0, sz = sd ? (sv - mean) / sd : 0;
      var better = Math.min(99, Math.round(100 * rows.filter(function (p) { return (+p[statKey] || 0) < sv; }).length / n));
      setHTML("soSpotlight",
        '<div class="so-spot"><span class="so-spot-tag">spotlight</span> <b>' + esc(spot.name) + "</b> (" + esc(spot.team) +
        (spot.pos ? ", " + esc(spot.pos) : "") + ") — <b>" + soFmt(sv, dp) + "</b> " + esc(label.toLowerCase()) +
        ', <b style="color:' + (sz >= 0 ? "var(--good)" : "var(--bad)") + '">' + (sz >= 0 ? "+" : "") + sz.toFixed(1) +
        "σ</b> " + (sz >= 0 ? "over" : "below") + " average — better than <b>" + better + "%</b> of " +
        (soState.pos === "all" ? "players" : "players in this position") + ".</div>");
    } else if (soState.player) {
      setHTML("soSpotlight", '<span class="hint">No qualifying player matches "' + esc(soState.player) + '". Check the spelling or relax the filters.</span>');
    } else {
      setHTML("soSpotlight", '<span class="hint">Tip: type a name in <b>Spotlight player</b> to highlight one player (gold) and see their percentile.</span>');
    }
    var top = sorted.slice(0, 12).map(function (p) {
      var v = +p[statKey] || 0; return { p: p, v: v, z: sd ? (v - mean) / sd : 0 };
    });
    var maxZ = Math.max.apply(null, top.map(function (t) { return t.z; }).concat([0.001]));
    setHTML("soStandouts", '<div class="so-bars">' + top.map(function (t) {
      var pct = Math.max(2, 100 * t.z / maxZ), hot = t.z >= 2;
      return '<div class="so-bar-row"><div class="nm">' + logoImg(t.p.team) + "<span>" + esc(t.p.name) + "</span></div>" +
        '<div class="so-bar-track"><div class="so-bar-fill" style="width:' + pct.toFixed(1) + "%;background:" + (hot ? "#ff3d8b" : "var(--accent-2)") + '"></div></div>' +
        '<div class="so-bar-val">' + soFmt(t.v, dp) + ' <span class="so-z">' + (t.z >= 0 ? "+" : "") + t.z.toFixed(1) + "σ</span></div></div>";
    }).join("") + "</div>");
  }

  /* ---- Two-stat scatter ---- */
  var soSc = { x: "xg", y: "g", size: "shots", pos: "all", mins: 900 };
  var SO_PRESETS = [
    { label: "🎯 Finishers", x: "xg", y: "g", size: "shots", pos: "all", mins: 450 },
    { label: "🎨 Creators", x: "xa", y: "a", size: "keyPasses", pos: "all", mins: 900 },
    { label: "⚡ Dribble & create", x: "dribbles", y: "keyPasses", size: "touches", pos: "all", mins: 900 },
    { label: "🛡 Ball winners", x: "tackles", y: "interceptions", size: "clearances", pos: "DEF", mins: 900 },
    { label: "🧱 Defensive rock", x: "clearances", y: "aerials", size: "tackles", pos: "DEF", mins: 900 },
    { label: "🎽 Sharpshooters", x: "shots", y: "sot", size: "xg", pos: "all", mins: 450 },
    { label: "⭐ Complete player", x: "xgi", y: "rating", size: "ga", pos: "all", mins: 900 }
  ];
  function soStatLabel(k) { var m = SO_STATS.filter(function (s) { return s[0] === k; })[0]; return m ? m[1] : k; }
  function soStatDp(k) { var m = SO_STATS.filter(function (s) { return s[0] === k; })[0]; return m ? m[2] : 0; }
  function soNiceStep(raw) {
    raw = raw || 1; var pow = Math.pow(10, Math.floor(Math.log10(raw))), n = raw / pow;
    return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 2.5 ? 2.5 : n <= 5 ? 5 : 10) * pow;
  }
  function soLTicks(lo, hi) {
    var step = soNiceStep((hi - lo) / 5), start = Math.ceil(lo / step - 1e-9) * step, out = [];
    for (var v = start; v <= hi + 1e-9; v += step) out.push(+v.toFixed(4));
    return out;
  }
  function soQualifyFor(pos, mins) {
    return PLAYERS.filter(function (p) {
      if ((p.mins || 0) < mins) return false;
      if (pos !== "all" && soPosGroup(p.pos) !== pos) return false;
      return true;
    });
  }

  function soScatterSVG(rows, xKey, yKey, sizeKey, spotPid) {
    var W = 880, H = 480, padL = 56, padR = 22, padT = 22, padB = 54;
    var plotW = W - padL - padR, plotH = H - padT - padB;
    var xs = rows.map(function (p) { return +p[xKey] || 0; });
    var ys = rows.map(function (p) { return +p[yKey] || 0; });
    var mean = function (a) { return a.reduce(function (s, v) { return s + v; }, 0) / a.length; };
    var stdev = function (a, m) { return Math.sqrt(a.reduce(function (s, v) { return s + (v - m) * (v - m); }, 0) / a.length); };
    var mx = mean(xs), my = mean(ys), sdx = stdev(xs, mx) || 1, sdy = stdev(ys, my) || 1;
    function dom(vals) {
      var lo = Math.min.apply(null, vals), hi = Math.max.apply(null, vals);
      lo = Math.min(lo, 0); var pad = (hi - lo) * 0.08 || 1;
      return [lo - (lo < 0 ? pad * 0.4 : 0), hi + pad];
    }
    var dx = dom(xs), dy = dom(ys);
    function sx(v) { return padL + plotW * (v - dx[0]) / (dx[1] - dx[0]); }
    function sy(v) { return padT + plotH * (1 - (v - dy[0]) / (dy[1] - dy[0])); }
    var sizeMax = sizeKey ? Math.max.apply(null, rows.map(function (p) { return +p[sizeKey] || 0; }).concat([0.0001])) : 1;
    function radius(p) { if (!sizeKey) return 4.2; return 3 + 9 * Math.sqrt(Math.max(0, +p[sizeKey] || 0) / sizeMax); }
    var dpx = soStatDp(xKey), dpy = soStatDp(yKey), dps = soStatDp(sizeKey);
    var svg = ['<svg viewBox="0 0 ' + W + ' ' + H + '" class="so-chart" preserveAspectRatio="xMidYMid meet" role="img">'];
    soLTicks(dx[0], dx[1]).forEach(function (t) {
      var x = sx(t);
      svg.push('<line x1="' + x.toFixed(1) + '" y1="' + padT + '" x2="' + x.toFixed(1) + '" y2="' + (padT + plotH) + '" stroke="#161d31" stroke-width="1"/>');
      svg.push('<text x="' + x.toFixed(1) + '" y="' + (padT + plotH + 16) + '" fill="#7c89a8" font-size="10.5" text-anchor="middle">' + soFmt(t, dpx) + "</text>");
    });
    soLTicks(dy[0], dy[1]).forEach(function (t) {
      var y = sy(t);
      svg.push('<line x1="' + padL + '" y1="' + y.toFixed(1) + '" x2="' + (W - padR) + '" y2="' + y.toFixed(1) + '" stroke="#161d31" stroke-width="1"/>');
      svg.push('<text x="' + (padL - 7) + '" y="' + (y + 3.5).toFixed(1) + '" fill="#7c89a8" font-size="10.5" text-anchor="end">' + soFmt(t, dpy) + "</text>");
    });
    svg.push('<line x1="' + sx(mx).toFixed(1) + '" y1="' + padT + '" x2="' + sx(mx).toFixed(1) + '" y2="' + (padT + plotH) + '" stroke="#cfd8ee" stroke-width="1" stroke-dasharray="5 4" stroke-opacity="0.5"/>');
    svg.push('<line x1="' + padL + '" y1="' + sy(my).toFixed(1) + '" x2="' + (W - padR) + '" y2="' + sy(my).toFixed(1) + '" stroke="#cfd8ee" stroke-width="1" stroke-dasharray="5 4" stroke-opacity="0.5"/>');
    svg.push('<text x="' + (padL + plotW / 2).toFixed(1) + '" y="' + (H - 6) + '" fill="#e8edf7" font-size="12.5" text-anchor="middle">' + esc(soStatLabel(xKey)) + " →</text>");
    svg.push('<text x="16" y="' + (padT + plotH / 2).toFixed(1) + '" fill="#e8edf7" font-size="12.5" text-anchor="middle" transform="rotate(-90 16 ' + (padT + plotH / 2).toFixed(1) + ')">' + esc(soStatLabel(yKey)) + " →</text>");
    var pts = [];
    rows.forEach(function (p) {
      var vx = +p[xKey] || 0, vy = +p[yKey] || 0;
      var cx = sx(vx), cy = sy(vy), r = radius(p);
      var elite = vx > mx && vy > my;
      var isSpot = spotPid && p.pid === spotPid;
      var fill = isSpot ? "#ffd24d" : elite ? "#ff3d8b" : "#4ea1ff";
      var op = isSpot ? 1 : elite ? 0.85 : 0.5;
      var stroke = (isSpot || elite) ? ' stroke="#0b0f1a" stroke-width="0.9"' : "";
      var info = p.name + " · " + p.team + " — " + soStatLabel(xKey) + " " + soFmt(vx, dpx) +
        ", " + soStatLabel(yKey) + " " + soFmt(vy, dpy) + (sizeKey ? " · " + soStatLabel(sizeKey) + " " + soFmt(+p[sizeKey] || 0, dps) : "");
      svg.push('<circle cx="' + cx.toFixed(1) + '" cy="' + cy.toFixed(1) + '" r="' + r.toFixed(1) + '" fill="' + fill + '" fill-opacity="' + op + '"' + stroke + ' data-info="' + esc(info) + '"></circle>');
      var zx = (vx - mx) / sdx, zy = (vy - my) / sdy;
      pts.push({ p: p, cx: cx, cy: cy, score: zx + zy, team: p.name, spot: isSpot });
    });
    var labelSet = pts.slice().sort(function (a, b) { return b.score - a.score; }).filter(function (q) { return q.score > 1.4; }).slice(0, 9);
    pts.forEach(function (q) { if (q.spot && labelSet.indexOf(q) < 0) labelSet.push(q); });
    declutter(labelSet, 8.7);
    labelSet.forEach(function (q) {
      if (q.led) svg.push('<line x1="' + q.cx.toFixed(1) + '" y1="' + q.cy.toFixed(1) + '" x2="' + (q.lx - 1).toFixed(1) + '" y2="' + (q.ly - 3).toFixed(1) + '" stroke="#46527a" stroke-width="0.6"/>');
      svg.push('<text x="' + q.lx.toFixed(1) + '" y="' + q.ly.toFixed(1) + '" fill="' + (q.spot ? "#ffe08a" : "#c2cce0") + '" font-size="8.9">' + esc(q.team) + "</text>");
    });
    svg.push("</svg>");
    return svg.join("");
  }

  function renderScatter2() {
    var host = document.getElementById("soScatter");
    if (!host) return;
    var rows = soQualifyFor(soSc.pos, soSc.mins);
    var setHTML = function (id, h) { var e = document.getElementById(id); if (e) e.innerHTML = h; };
    if (rows.length < 3) { host.innerHTML = '<p class="hint">Not enough players match these filters.</p>'; setHTML("soScInsight", ""); return; }
    var missing = [soSc.x, soSc.y, soSc.size].filter(function (k) { return k && !(k in rows[0]); });
    if (missing.length) {
      host.innerHTML = '<p class="hint">Some selected metrics (' + missing.map(soStatLabel).join(", ") + ') aren\'t in the current data.</p>';
      setHTML("soScInsight", ""); return;
    }
    var spot = null;
    if (soState.player) {
      var q = soState.player.toLowerCase();
      spot = rows.filter(function (p) { return p.name.toLowerCase() === q; })[0] ||
        rows.filter(function (p) { return p.name.toLowerCase().indexOf(q) >= 0; })[0] || null;
    }
    host.innerHTML = soScatterSVG(rows, soSc.x, soSc.y, soSc.size, spot ? spot.pid : null);
    var xs = rows.map(function (p) { return +p[soSc.x] || 0; }), ys = rows.map(function (p) { return +p[soSc.y] || 0; });
    var mx = xs.reduce(function (s, v) { return s + v; }, 0) / xs.length;
    var my = ys.reduce(function (s, v) { return s + v; }, 0) / ys.length;
    var elite = rows.filter(function (p) { return (+p[soSc.x] || 0) > mx && (+p[soSc.y] || 0) > my; });
    var best = elite.slice().sort(function (a, b) {
      return ((+b[soSc.x] || 0) / (mx || 1) + (+b[soSc.y] || 0) / (my || 1)) - ((+a[soSc.x] || 0) / (mx || 1) + (+a[soSc.y] || 0) / (my || 1));
    }).slice(0, 5).map(function (p) { return esc(p.name); });
    setHTML("soScInsight", "<b>" + elite.length + "</b> player" + (elite.length === 1 ? "" : "s") +
      " are above average in <b>both</b> " + esc(soStatLabel(soSc.x).toLowerCase()) + " and " + esc(soStatLabel(soSc.y).toLowerCase()) +
      " (top-right quadrant)" + (best.length ? " — led by " + best.join(", ") : "") + "." +
      (soSc.size ? ' Dot size = ' + esc(soStatLabel(soSc.size).toLowerCase()) + "." : ""));
  }

  /* ---- Player percentile radar ---- */
  var RADAR_OUT = [["g", "Goals"], ["a", "Assists"], ["keyPasses", "Key passes"], ["dribbles", "Dribbles"],
    ["tackles", "Tackles"], ["interceptions", "Intercept"], ["shots", "Shots"], ["xg", "xG"]];
  var RADAR_GK = [["saves", "Saves"], ["passes", "Passes"], ["pass_pct", "Pass %"], ["clearances", "Clearances"], ["rating", "Rating"]];

  function radarSVG(player) {
    var grp = soPosGroup(player.pos);
    var axes = grp === "GK" ? RADAR_GK : RADAR_OUT;
    function inPool(p) {
      if ((p.mins || 0) < 450) return false;
      var g = soPosGroup(p.pos);
      if (grp === "GK") return g === "GK";
      if (grp === "OTH") return g !== "GK";
      return g === grp;
    }
    var pool = PLAYERS.filter(inPool);
    if (pool.indexOf(player) < 0) pool.push(player);
    var N = axes.length, W = 580, H = 470, cx = W / 2, cy = H / 2 + 4, R = 148;
    var svg = ['<svg viewBox="0 0 ' + W + ' ' + H + '" class="so-radar" preserveAspectRatio="xMidYMid meet" role="img">'];
    [0.25, 0.5, 0.75, 1].forEach(function (f) {
      var pts = [];
      for (var i = 0; i < N; i++) { var a = -Math.PI / 2 + i * 2 * Math.PI / N; pts.push((cx + R * f * Math.cos(a)).toFixed(1) + "," + (cy + R * f * Math.sin(a)).toFixed(1)); }
      svg.push('<polygon points="' + pts.join(" ") + '" fill="none" stroke="#1e2740" stroke-width="1"/>');
    });
    var poly = [];
    axes.forEach(function (ax, i) {
      var a = -Math.PI / 2 + i * 2 * Math.PI / N;
      svg.push('<line x1="' + cx + '" y1="' + cy + '" x2="' + (cx + R * Math.cos(a)).toFixed(1) + '" y2="' + (cy + R * Math.sin(a)).toFixed(1) + '" stroke="#1e2740" stroke-width="1"/>');
      var pv = +player[ax[0]] || 0;
      var below = pool.filter(function (p) { return (+p[ax[0]] || 0) < pv; }).length;
      var pct = pool.length ? below / pool.length : 0;
      poly.push((cx + R * pct * Math.cos(a)).toFixed(1) + "," + (cy + R * pct * Math.sin(a)).toFixed(1));
      var lx = cx + (R + 16) * Math.cos(a), ly = cy + (R + 16) * Math.sin(a);
      var anchor = Math.abs(Math.cos(a)) < 0.3 ? "middle" : (Math.cos(a) > 0 ? "start" : "end");
      svg.push('<text x="' + lx.toFixed(1) + '" y="' + (ly - 2).toFixed(1) + '" fill="#aab4cc" font-size="10.5" text-anchor="' + anchor + '">' + esc(ax[1]) + "</text>");
      svg.push('<text x="' + lx.toFixed(1) + '" y="' + (ly + 10).toFixed(1) + '" fill="#e8edf7" font-size="11" font-weight="700" text-anchor="' + anchor + '">' + soFmt(pv, soStatDp(ax[0])) + " (" + Math.round(pct * 100) + "%)</text>");
    });
    svg.push('<polygon points="' + poly.join(" ") + '" fill="rgba(255,210,77,0.18)" stroke="#ffd24d" stroke-width="2"/>');
    poly.forEach(function (pt) { var c = pt.split(","); svg.push('<circle cx="' + c[0] + '" cy="' + c[1] + '" r="3" fill="#ffd24d"/>'); });
    svg.push("</svg>");
    return svg.join("");
  }

  function renderRadar() {
    var host = document.getElementById("soRadar");
    if (!host) return;
    if (!soState.player) {
      host.innerHTML = '<p class="hint">Pick a <b>spotlight player</b> at the top of this page to see their percentile radar.</p>';
      return;
    }
    var q = soState.player.toLowerCase();
    var pl = PLAYERS.filter(function (p) { return p.name.toLowerCase() === q; })[0] ||
      PLAYERS.filter(function (p) { return p.name.toLowerCase().indexOf(q) >= 0; })[0];
    if (!pl) { host.innerHTML = '<p class="hint">No player matches "' + esc(soState.player) + '".</p>'; return; }
    var grp = soPosGroup(pl.pos);
    var grpLabel = { FWD: "attackers", MID: "midfielders", DEF: "defenders", GK: "goalkeepers", OTH: "outfield players" }[grp] || "peers";
    host.innerHTML = '<div class="so-radar-head"><b>' + esc(pl.name) + "</b> · " + esc(pl.team) +
      (pl.pos ? " · " + esc(pl.pos) : "") + " — percentiles vs other " + grpLabel + " (450+ min)</div>" + radarSVG(pl);
  }

  /* Tap-to-identify: dots carry data-info; hover shows the floating tooltip, tap shows
     a caption line below the chart. Delegated so it survives chart re-renders. */
  function tipHTML(info) {
    var i = info.indexOf(" — ");
    var a = i >= 0 ? info.slice(0, i) : info, b = i >= 0 ? info.slice(i + 3) : "";
    return '<div class="t-team">' + esc(a) + "</div>" + (b ? '<div class="t-line">' + esc(b) + "</div>" : "");
  }
  function wireChartTaps(hostId, tipId) {
    var host = document.getElementById(hostId), tip = document.getElementById(tipId);
    if (!host || host._tapWired) return;
    host._tapWired = true;
    var last = null;
    function isDot(el) { return el && (el.tagName || "").toLowerCase() === "circle" && el.hasAttribute("data-info"); }
    host.addEventListener("pointermove", function (e) {
      if (e.pointerType === "touch") return;
      if (isDot(e.target)) {
        tooltip.innerHTML = tipHTML(e.target.getAttribute("data-info"));
        tooltip.style.opacity = "1";
        tooltip.style.left = (e.clientX + 14) + "px";
        tooltip.style.top = (e.clientY + 14) + "px";
      } else { tooltip.style.opacity = "0"; }
    });
    host.addEventListener("pointerleave", function () { tooltip.style.opacity = "0"; });
    host.addEventListener("click", function (e) {
      if (!isDot(e.target)) return;
      var el = e.target;
      if (last && last.parentNode) { last.setAttribute("stroke", last._os || "none"); last.setAttribute("stroke-width", last._ow || "0"); }
      el._os = el.getAttribute("stroke") || "none"; el._ow = el.getAttribute("stroke-width") || "0";
      el.setAttribute("stroke", "#fff"); el.setAttribute("stroke-width", "2");
      last = el;
      if (tip) { tip.textContent = el.getAttribute("data-info"); tip.classList.add("show"); }
    });
  }

  // One-time wiring of the static Standouts controls + events.
  function wireStandouts() {
    var statSel = document.getElementById("soStat");
    if (!statSel || statSel._wired) return;
    statSel._wired = true;
    statSel.innerHTML = SO_STATS.map(function (s) { return '<option value="' + s[0] + '">' + esc(s[1]) + "</option>"; }).join("");
    statSel.value = soState.stat;
    statSel.addEventListener("change", function () { soState.stat = statSel.value; renderStandouts(); });
    document.getElementById("soPos").addEventListener("change", function (e) { soState.pos = e.target.value; renderStandouts(); });
    document.getElementById("soMins").addEventListener("change", function (e) { soState.mins = +e.target.value; renderStandouts(); });
    var pin = document.getElementById("soPlayer"), deb;
    pin.addEventListener("input", function () {
      clearTimeout(deb);
      deb = setTimeout(function () { soState.player = pin.value.trim(); renderStandouts(); renderScatter2(); renderRadar(); }, 200);
    });
    wireChartTaps("soChart", "soChartTip");
    wireChartTaps("soScatter", "soScatterTip");
    var axisOpts = SO_STATS.map(function (s) { return '<option value="' + s[0] + '">' + esc(s[1]) + "</option>"; }).join("");
    var xSel = document.getElementById("soScX"), ySel = document.getElementById("soScY"), sizeSel = document.getElementById("soScSize");
    xSel.innerHTML = axisOpts; ySel.innerHTML = axisOpts;
    sizeSel.innerHTML = '<option value="">— none —</option>' + axisOpts;
    function syncScatterControls() {
      xSel.value = soSc.x; ySel.value = soSc.y; sizeSel.value = soSc.size;
      document.getElementById("soScPos").value = soSc.pos;
      document.getElementById("soScMins").value = String(soSc.mins);
    }
    syncScatterControls();
    xSel.addEventListener("change", function () { soSc.x = xSel.value; renderScatter2(); });
    ySel.addEventListener("change", function () { soSc.y = ySel.value; renderScatter2(); });
    sizeSel.addEventListener("change", function () { soSc.size = sizeSel.value; renderScatter2(); });
    document.getElementById("soScPos").addEventListener("change", function (e) { soSc.pos = e.target.value; renderScatter2(); });
    document.getElementById("soScMins").addEventListener("change", function (e) { soSc.mins = +e.target.value; renderScatter2(); });
    var pHost = document.getElementById("soPresets");
    pHost.innerHTML = SO_PRESETS.map(function (pr, i) {
      var on = pr.x === soSc.x && pr.y === soSc.y && pr.pos === soSc.pos;
      return '<button class="so-preset' + (on ? " active" : "") + '" data-i="' + i + '">' + esc(pr.label) + "</button>";
    }).join("");
    pHost.querySelectorAll(".so-preset").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var pr = SO_PRESETS[+btn.dataset.i];
        soSc.x = pr.x; soSc.y = pr.y; soSc.size = pr.size; soSc.pos = pr.pos; soSc.mins = pr.mins;
        pHost.querySelectorAll(".so-preset").forEach(function (b) { b.classList.remove("active"); });
        btn.classList.add("active");
        syncScatterControls();
        renderScatter2();
      });
    });
  }

  // Per-season Standouts refresh: repopulate the player datalist + redraw everything.
  function refreshStandouts() {
    var dl = document.getElementById("soPlayerList");
    if (dl) dl.innerHTML = PLAYERS.map(function (p) { return p.name; }).sort()
      .map(function (nm) { return '<option value="' + esc(nm) + '">'; }).join("");
    renderStandouts();
    renderScatter2();
    renderRadar();
    renderPlayerLeaders();
    renderPlayerBoards();
  }

  /* ================= TEAM LAB (per-team season aggregates) ================= */
  function teamTotals() {
    var t = {};
    function get(name) {
      return t[name] || (t[name] = { team: name, mp: 0, w: 0, d: 0, l: 0, gf: 0, ga: 0,
        sg: 0, xgf: 0, xga: 0, shots: 0, sot: 0, poss: 0, pacc: 0, bch: 0 });
    }
    D.matches.forEach(function (m) {
      if (!m.played) return;
      var H = get(m.home), A = get(m.away);
      H.mp++; A.mp++;
      H.gf += m.hs; H.ga += m.as; A.gf += m.as; A.ga += m.hs;
      if (m.hs > m.as) { H.w++; A.l++; } else if (m.hs < m.as) { A.w++; H.l++; } else { H.d++; A.d++; }
      if (!m.has_stats) return;
      var s = m.stats;
      function add(side, sign) {
        var i = sign === "h" ? 0 : 1, j = sign === "h" ? 1 : 0;
        side.sg++;
        side.xgf += s.xg[i] || 0; side.xga += s.xg[j] || 0;
        side.shots += s.shots[i] || 0; side.sot += s.sot[i] || 0;
        side.poss += s.possession[i] || 0; side.pacc += s.pass_acc[i] || 0;
        side.bch += s.big_chances[i] || 0;
      }
      add(H, "h"); add(A, "a");
    });
    return Object.keys(t).map(function (k) {
      var r = t[k], n = r.sg || 1;
      r.shotsPg = r.shots / n; r.sotPg = r.sot / n; r.possAvg = r.poss / n;
      r.paccAvg = r.pacc / n; r.bchPg = r.bch / n;
      return r;
    });
  }
  function renderDbTeamTable() {
    var input = document.getElementById("tlSearch");
    var q = (input ? input.value : "").toLowerCase().trim();
    var cols = [
      ["team", "Team", "t"], ["mp", "MP", "i"], ["w", "W", "i"], ["d", "D", "i"], ["l", "L", "i"],
      ["gf", "GF", "i"], ["ga", "GA", "i"], ["xgf", "xG", "f"], ["xga", "xGA", "f"],
      ["shotsPg", "Sh/g", "f"], ["sotPg", "SoT/g", "f"], ["possAvg", "Poss%", "i"],
      ["paccAvg", "Pass%", "i"], ["bchPg", "BigCh/g", "f"],
    ];
    var rows = TOTALS.filter(function (r) { return !q || r.team.toLowerCase().indexOf(q) >= 0; })
      .sort(function (a, b) {
        var k = dbSort.key;
        if (k === "team") return dbSort.dir * a.team.localeCompare(b.team);
        return dbSort.dir * ((a[k] || 0) - (b[k] || 0));
      });
    function cell(r, c) {
      var k = c[0];
      if (k === "team") return '<td class="team"><div class="team-cell">' + logoImg(r.team) + '<span class="nm">' + esc(r.team) + "</span></div></td>";
      var v = r[k];
      if (c[2] === "f") v = (v || 0).toFixed(2);
      else if (c[2] === "i") v = Math.round(v || 0);
      return "<td>" + v + "</td>";
    }
    var head = cols.map(function (c) {
      var arr = dbSort.key === c[0] ? (dbSort.dir < 0 ? " ▼" : " ▲") : "";
      return '<th class="' + (c[2] === "t" ? "team" : "") + '" data-k="' + c[0] + '">' + c[1] + '<span class="arr">' + arr + "</span></th>";
    }).join("");
    var body = rows.map(function (r) {
      return "<tr>" + cols.map(function (c) { return cell(r, c); }).join("") + "</tr>";
    }).join("");
    document.getElementById("teamTable").innerHTML =
      '<table class="rank db-team"><thead><tr>' + head + "</tr></thead><tbody>" + body + "</tbody></table>";
    document.querySelectorAll("#teamTable th").forEach(function (th) {
      th.addEventListener("click", function () {
        var k = th.dataset.k;
        if (dbSort.key === k) dbSort.dir *= -1;
        else { dbSort.key = k; dbSort.dir = k === "team" ? 1 : -1; }
        renderDbTeamTable();
      });
    });
  }
  /* ---- Team Lab: shot map / xG heatmap + team style fingerprint. Shots come from
     window.LL_SHOTS[season] (build_shots.py, aggregated from matches_detail). WhoScored
     coords attack toward x=100; the pitch is drawn goal-at-top. ---- */
  var SHOTS = [];
  var tlState = { team: "all", teamB: "none", teamC: "none", filter: "all", sit: "all", mode: "dots" };
  var TL_COLORS = ["#4ea1ff", "#ff3d8b", "#ffd24d"];

  function tlMatchSit(s, sit) {
    if (sit === "all") return true;
    if (sit === "open") return s.s === "Open Play" || s.s === "Fast Break";
    if (sit === "set") return s.s === "Corner" || s.s === "Free Kick" || s.s === "Set Piece";
    if (sit === "pen") return s.s === "Penalty";
    return true;
  }
  function tlShotsFor(team) {
    return SHOTS.filter(function (s) {
      if (team !== "all" && s.t !== team) return false;
      if (tlState.filter === "ot" && !s.ot) return false;
      if (tlState.filter === "goal" && !s.g) return false;
      return tlMatchSit(s, tlState.sit);
    });
  }
  function tlPitch(W, H) {
    var padX = 12, padTop = 12, padBot = 12;
    var plotW = W - padX * 2, plotH = H - padTop - padBot;
    function px(yws) { return padX + plotW * (yws / 100); }
    function py(xws) { return padTop + plotH * (1 - (Math.max(50, Math.min(100, xws)) - 50) / 50); }
    var st = 'stroke="#3a456b" stroke-width="1.3" fill="none"';
    var svg = [];
    svg.push('<rect x="' + px(0).toFixed(1) + '" y="' + py(100).toFixed(1) + '" width="' + (px(100) - px(0)).toFixed(1) + '" height="' + (py(50) - py(100)).toFixed(1) + '" ' + st + ' rx="2"/>');
    svg.push('<rect x="' + px(21.1).toFixed(1) + '" y="' + py(100).toFixed(1) + '" width="' + (px(78.9) - px(21.1)).toFixed(1) + '" height="' + (py(83) - py(100)).toFixed(1) + '" ' + st + '/>');
    svg.push('<rect x="' + px(36.8).toFixed(1) + '" y="' + py(100).toFixed(1) + '" width="' + (px(63.2) - px(36.8)).toFixed(1) + '" height="' + (py(94.2) - py(100)).toFixed(1) + '" ' + st + '/>');
    svg.push('<rect x="' + px(44.2).toFixed(1) + '" y="' + (py(100) - 4).toFixed(1) + '" width="' + (px(55.8) - px(44.2)).toFixed(1) + '" height="4" stroke="#6f7fb0" fill="none"/>');
    svg.push('<circle cx="' + px(50).toFixed(1) + '" cy="' + py(88.5).toFixed(1) + '" r="1.8" fill="#3a456b"/>');
    var ay = py(83);
    svg.push('<path d="M ' + px(36).toFixed(1) + ' ' + ay.toFixed(1) + ' A ' + ((px(64) - px(36)) / 2).toFixed(1) + ' ' + (py(83) - py(73)).toFixed(1) + ' 0 0 1 ' + px(64).toFixed(1) + ' ' + ay.toFixed(1) + '" ' + st + '/>');
    svg.push('<line x1="' + px(0).toFixed(1) + '" y1="' + py(50).toFixed(1) + '" x2="' + px(100).toFixed(1) + '" y2="' + py(50).toFixed(1) + '" ' + st + '/>');
    svg.push('<path d="M ' + px(40).toFixed(1) + ' ' + py(50).toFixed(1) + ' A ' + ((px(60) - px(40)) / 2).toFixed(1) + ' ' + (py(50) - py(60)).toFixed(1) + ' 0 0 1 ' + px(60).toFixed(1) + ' ' + py(50).toFixed(1) + '" ' + st + '/>');
    return { svg: svg, px: px, py: py };
  }
  function tlShotMap(shots) {
    var W = 600, H = 470;
    var P = tlPitch(W, H);
    var svg = ['<svg viewBox="0 0 ' + W + ' ' + H + '" class="tl-pitch" preserveAspectRatio="xMidYMid meet" role="img">'];
    svg.push('<rect x="0" y="0" width="' + W + '" height="' + H + '" fill="#0d1322"/>');
    svg = svg.concat(P.svg);
    if (tlState.mode === "heat") {
      var CW = 12, CH = 10, cells = [];
      for (var i = 0; i < CW * CH; i++) cells.push(0);
      shots.forEach(function (s) {
        var cx = Math.min(CW - 1, Math.max(0, Math.floor(s.y / 100 * CW)));
        var cr = Math.min(CH - 1, Math.max(0, Math.floor((Math.max(50, Math.min(100, s.x)) - 50) / 50 * CH)));
        cells[cr * CW + cx] += s.xg;
      });
      var maxC = Math.max.apply(null, cells.concat([0.0001]));
      var x0 = P.px(0), x1 = P.px(100), y0 = P.py(50), y1 = P.py(100);
      var cw = (x1 - x0) / CW, ch = (y0 - y1) / CH;
      for (var r = 0; r < CH; r++) for (var c = 0; c < CW; c++) {
        var v = cells[r * CW + c]; if (v <= 0) continue;
        var op = 0.08 + 0.78 * (v / maxC);
        var rx = x0 + c * cw, ry = y1 + (CH - 1 - r) * ch;
        svg.push('<rect x="' + rx.toFixed(1) + '" y="' + ry.toFixed(1) + '" width="' + cw.toFixed(1) + '" height="' + ch.toFixed(1) + '" fill="#ff6a3d" fill-opacity="' + op.toFixed(3) + '"/>');
      }
      svg = svg.concat(P.svg);
    } else {
      shots.slice().sort(function (a, b) { return (a.g ? 1 : 0) - (b.g ? 1 : 0); }).forEach(function (s) {
        var r = 2.3 + 6 * Math.sqrt(Math.max(0, s.xg));
        var fill = s.g ? "#ff3d8b" : s.ot ? "#4ea1ff" : "#7c89a8";
        var op = s.g ? 0.95 : s.ot ? 0.6 : 0.35;
        var stroke = s.g ? ' stroke="#0b0f1a" stroke-width="0.8"' : "";
        var info = s.t + " vs " + s.o + " — xG " + s.xg.toFixed(2) + (s.g ? " (GOAL)" : s.ot ? " (on target)" : "") + " · " + s.s + " · " + s.m + "'";
        svg.push('<circle cx="' + P.px(s.y).toFixed(1) + '" cy="' + P.py(s.x).toFixed(1) + '" r="' + r.toFixed(1) + '" fill="' + fill + '" fill-opacity="' + op + '"' + stroke + ' data-info="' + esc(info) + '"></circle>');
      });
    }
    svg.push("</svg>");
    return svg.join("");
  }
  function tlTeamStyle() {
    var T = {};
    function get(t) { return T[t] || (T[t] = { gp: 0, poss: 0, possN: 0, shots: 0, paSum: 0, paN: 0, xgf: 0, xga: 0, shotN: 0, xgAll: 0, spXg: 0 }); }
    D.matches.forEach(function (m) {
      if (!m.played) return;
      [["home", m.home], ["away", m.away]].forEach(function (z) {
        var side = z[0], t = get(z[1]);
        var st = m.stats || {};
        var pi = side === "home" ? 0 : 1;
        function v(k) { var a = st[k]; return a && a[pi] != null ? a[pi] : null; }
        t.gp++;
        var po = v("possession"); if (po != null) { t.poss += po; t.possN++; }
        var sh = v("shots"); if (sh != null) t.shots += sh;
        var pa = v("pass_acc"); if (pa != null) { t.paSum += pa; t.paN++; }
        t.xgf += side === "home" ? (m.xg_home || 0) : (m.xg_away || 0);
        t.xga += side === "home" ? (m.xg_away || 0) : (m.xg_home || 0);
      });
    });
    SHOTS.forEach(function (s) {
      var t = T[s.t]; if (!t) return;
      t.shotN++; t.xgAll += s.xg;
      if (s.s !== "Open Play" && s.s !== "Fast Break") t.spXg += s.xg;
    });
    var out = {};
    Object.keys(T).forEach(function (k) {
      var t = T[k]; if (!t.gp) return;
      out[k] = {
        team: k, gp: t.gp,
        poss: t.possN ? t.poss / t.possN : 0,
        shotsPG: t.shots / t.gp,
        xgPG: t.xgf / t.gp,
        xgPerShot: t.shotN ? t.xgAll / t.shotN : 0,
        spShare: t.xgAll ? t.spXg / t.xgAll * 100 : 0,
        passAcc: t.paN ? t.paSum / t.paN : 0,
        xgaPG: t.xga / t.gp
      };
    });
    return out;
  }
  var TL_AXES = [["poss", "Possession", 0], ["shotsPG", "Shots /game", 1], ["xgPG", "xG /game", 2],
    ["xgPerShot", "xG /shot", 2], ["spShare", "Set-piece xG %", 0], ["passAcc", "Pass accuracy", 0], ["DEF", "Defensive", 2]];
  function tlRadar(teams, styleMap) {
    var pool = Object.keys(styleMap).map(function (k) { return styleMap[k]; });
    var present = teams.filter(function (t) { return styleMap[t]; });
    if (!present.length) return '<p class="hint">No style data for these teams yet.</p>';
    var single = present.length === 1;
    var N = TL_AXES.length, W = 580, H = 470, cx = W / 2, cy = H / 2 + 4, R = 146;
    function axVal(me, ax) { return ax[0] === "DEF" ? -me.xgaPG : me[ax[0]]; }
    function axGet(ax) { return ax[0] === "DEF" ? function (s) { return -s.xgaPG; } : function (s) { return s[ax[0]]; }; }
    function pctOf(me, ax) {
      var v = axVal(me, ax), get = axGet(ax);
      var below = pool.filter(function (s) { return get(s) < v; }).length;
      return pool.length ? below / pool.length : 0;
    }
    var svg = ['<svg viewBox="0 0 ' + W + ' ' + H + '" class="so-radar" preserveAspectRatio="xMidYMid meet" role="img">'];
    [0.25, 0.5, 0.75, 1].forEach(function (f) {
      var pts = [];
      for (var i = 0; i < N; i++) { var a = -Math.PI / 2 + i * 2 * Math.PI / N; pts.push((cx + R * f * Math.cos(a)).toFixed(1) + "," + (cy + R * f * Math.sin(a)).toFixed(1)); }
      svg.push('<polygon points="' + pts.join(" ") + '" fill="none" stroke="#1e2740" stroke-width="1"/>');
    });
    TL_AXES.forEach(function (ax, i) {
      var a = -Math.PI / 2 + i * 2 * Math.PI / N;
      svg.push('<line x1="' + cx + '" y1="' + cy + '" x2="' + (cx + R * Math.cos(a)).toFixed(1) + '" y2="' + (cy + R * Math.sin(a)).toFixed(1) + '" stroke="#1e2740" stroke-width="1"/>');
      var lx = cx + (R + 16) * Math.cos(a), ly = cy + (R + 16) * Math.sin(a);
      var anchor = Math.abs(Math.cos(a)) < 0.3 ? "middle" : (Math.cos(a) > 0 ? "start" : "end");
      svg.push('<text x="' + lx.toFixed(1) + '" y="' + ((single ? ly - 2 : ly + 3.5)).toFixed(1) + '" fill="#aab4cc" font-size="10.5" text-anchor="' + anchor + '">' + esc(ax[1]) + "</text>");
      if (single) {
        var me = styleMap[present[0]], dp = ax[2];
        var disp = ax[0] === "DEF" ? me.xgaPG.toFixed(2) + " xGA" : soFmt(me[ax[0]], dp) + (ax[0] === "poss" || ax[0] === "passAcc" || ax[1].indexOf("%") >= 0 ? "%" : "");
        svg.push('<text x="' + lx.toFixed(1) + '" y="' + (ly + 10).toFixed(1) + '" fill="#e8edf7" font-size="11" font-weight="700" text-anchor="' + anchor + '">' + disp + " (" + Math.round(pctOf(me, ax) * 100) + "%)</text>");
      }
    });
    present.forEach(function (t, ti) {
      var me = styleMap[t], col = TL_COLORS[ti % TL_COLORS.length], poly = [];
      TL_AXES.forEach(function (ax, i) {
        var a = -Math.PI / 2 + i * 2 * Math.PI / N, pct = pctOf(me, ax);
        poly.push((cx + R * pct * Math.cos(a)).toFixed(1) + "," + (cy + R * pct * Math.sin(a)).toFixed(1));
      });
      svg.push('<polygon points="' + poly.join(" ") + '" fill="' + col + '" fill-opacity="' + (single ? 0.18 : 0.12) + '" stroke="' + col + '" stroke-width="2"/>');
      poly.forEach(function (pt) { var c = pt.split(","); svg.push('<circle cx="' + c[0] + '" cy="' + c[1] + '" r="3" fill="' + col + '"/>'); });
    });
    svg.push("</svg>");
    return svg.join("");
  }
  function tlMapCard(label, shots) {
    var goals = shots.filter(function (s) { return s.g; }).length;
    var xg = shots.reduce(function (a, s) { return a + s.xg; }, 0);
    var head = '<div class="tl-map-head"><b>' + esc(label) + "</b> · " + shots.length + " shots · " + goals + " goals · " + xg.toFixed(1) + " xG</div>";
    var body = shots.length ? tlShotMap(shots) : '<p class="hint">No shots match these filters.</p>';
    return '<div class="tl-map-card">' + head + '<div class="tl-pitch-wrap">' + body + "</div></div>";
  }

  function renderTeamLab() {
    if (!document.getElementById("view-teamlab")) return;
    // season totals table (always shown, below the maps)
    TOTALS = teamTotals();
    renderDbTeamTable();
    var setHTML = function (id, h) { var e = document.getElementById(id); if (e) e.innerHTML = h; };
    var mapsHost = document.getElementById("tlMaps");
    if (!SHOTS.length) {
      if (mapsHost) mapsHost.innerHTML = '<p class="hint">No shot data available yet.</p>';
      setHTML("tlStats", "");
      var sc0 = document.getElementById("tlStyleCard"); if (sc0) sc0.style.display = "none";
      return;
    }
    var allMode = tlState.team === "all";
    var teams = allMode ? [] : [tlState.team, tlState.teamB, tlState.teamC].filter(function (t, i, arr) {
      return t && t !== "none" && t !== "all" && arr.indexOf(t) === i;
    });
    var mapList = allMode ? [["All teams", "all"]] : teams.map(function (t) { return [t, t]; });
    if (!mapList.length) mapList = [["All teams", "all"]];
    setHTML("tlMapTitle", allMode ? "All teams — shot map"
      : (teams.length > 1 ? "Shot maps — " + teams.join(" vs ") : teams[0] + " — shot map"));
    setHTML("tlMaps", mapList.map(function (m) { return tlMapCard(m[0], tlShotsFor(m[1])); }).join(""));
    mapsHost.classList.toggle("compare", mapList.length > 1);
    var statsEl = document.getElementById("tlStats");
    if (mapList.length === 1) {
      var shots = tlShotsFor(mapList[0][1]);
      var goals = shots.filter(function (s) { return s.g; }).length;
      var ot = shots.filter(function (s) { return s.ot; }).length;
      var xg = shots.reduce(function (a, s) { return a + s.xg; }, 0);
      var diff = goals - xg;                        // finishing over/under xG (goal difference vs expected)
      var diffCls = "v " + (diff > 0.05 ? "pos" : diff < -0.05 ? "neg" : "");
      // own goals count in match scores but aren't shots — annotate the card so the
      // lower number is self-explaining (only in the unfiltered view)
      var goalsLabel = "Goals";
      if (tlState.filter === "all" && tlState.sit === "all") {
        var selTeam = allMode ? null : mapList[0][1];
        var scoreGoals = 0;
        (D.matches || []).forEach(function (m) {
          if (!m.played) return;
          if (!selTeam || selTeam === "all") scoreGoals += (m.hs || 0) + (m.as || 0);
          else if (m.home === selTeam) scoreGoals += (m.hs || 0);
          else if (m.away === selTeam) scoreGoals += (m.as || 0);
        });
        var og = scoreGoals - goals;
        if (og > 0) goalsLabel = "Goals from shots<br>+" + og + " own goal" + (og > 1 ? "s" : "") + " in scores";
      }
      var items = [
        ["v accent", shots.length, "Shots"], ["v", goals, goalsLabel], ["v blue", xg.toFixed(1), "Total xG"],
        ["v", shots.length ? (xg / shots.length).toFixed(2) : "0", "xG per shot"],
        ["v", shots.length ? Math.round(100 * ot / shots.length) + "%" : "0%", "On target"],
        [diffCls, (diff > 0 ? "+" : "") + diff.toFixed(1), "G − xG"],
      ];
      statsEl.innerHTML = items.map(function (it) { return '<div class="stat"><div class="' + it[0] + '">' + it[1] + '</div><div class="k">' + it[2] + "</div></div>"; }).join("");
      statsEl.style.display = "";
    } else { statsEl.innerHTML = ""; statsEl.style.display = "none"; }
    var styleCard = document.getElementById("tlStyleCard");
    if (allMode || !teams.length) { if (styleCard) styleCard.style.display = "none"; return; }
    styleCard.style.display = "";
    setHTML("tlStyleTitle", teams.length > 1 ? "Style fingerprints — " + teams.join(" vs ") : teams[0] + " — style fingerprint");
    var sm = tlTeamStyle();
    setHTML("tlRadar", tlRadar(teams, sm));
    setHTML("tlLegend", teams.length > 1 ? teams.map(function (t, i) {
      return '<span class="tl-leg"><i style="background:' + TL_COLORS[i % TL_COLORS.length] + '"></i>' + esc(t) + "</span>";
    }).join("") : "");
  }

  // One-time wiring of Team Lab controls.
  function wireTeamLab() {
    var sel = document.getElementById("tlTeam");
    if (!sel || sel._wired) return;
    sel._wired = true;
    sel.addEventListener("change", function () { tlState.team = sel.value; renderTeamLab(); });
    document.getElementById("tlTeamB").addEventListener("change", function (e) { tlState.teamB = e.target.value; renderTeamLab(); });
    document.getElementById("tlTeamC").addEventListener("change", function (e) { tlState.teamC = e.target.value; renderTeamLab(); });
    document.getElementById("tlFilter").addEventListener("change", function (e) { tlState.filter = e.target.value; renderTeamLab(); });
    document.getElementById("tlSit").addEventListener("change", function (e) { tlState.sit = e.target.value; renderTeamLab(); });
    document.getElementById("tlMode").addEventListener("change", function (e) { tlState.mode = e.target.value; renderTeamLab(); });
    wireChartTaps("tlMaps", "tlMapTip");
  }

  // Per-season Team Lab refresh: reload shots, repopulate team pickers, redraw.
  function refreshTeamLab() {
    SHOTS = (window.LL_SHOTS && window.LL_SHOTS[season]) || [];
    var sel = document.getElementById("tlTeam");
    if (sel) {
      var teams = {}; SHOTS.forEach(function (s) { teams[s.t] = 1; });
      var teamOpts = Object.keys(teams).sort().map(function (t) { return '<option value="' + esc(t) + '">' + esc(t) + "</option>"; }).join("");
      if (tlState.team !== "all" && !teams[tlState.team]) tlState.team = "all";
      ["teamB", "teamC"].forEach(function (k) { if (tlState[k] !== "none" && !teams[tlState[k]]) tlState[k] = "none"; });
      sel.innerHTML = '<option value="all">All teams</option>' + teamOpts;
      var selB = document.getElementById("tlTeamB"), selC = document.getElementById("tlTeamC");
      selB.innerHTML = '<option value="none">— none —</option>' + teamOpts;
      selC.innerHTML = '<option value="none">— none —</option>' + teamOpts;
      sel.value = tlState.team; selB.value = tlState.teamB; selC.value = tlState.teamC;
    }
    renderTeamLab();
  }

  /* ================= DATA DOWNLOADS ================= */
  function renderDownloads() {
    var db = window.WC_DATABASE;
    var wrap = document.getElementById("dataDownloads");
    if (!wrap) return;
    if (!db) { wrap.innerHTML = '<p class="hint">Run build_database.py to generate the downloads.</p>'; return; }
    wrap.innerHTML = db.tables.filter(function (t) { return t.rows > 0; }).map(function (t) {
      return '<a class="data-card" href="database/' + esc(t.file) + '" download>' +
        '<div class="dc-name">' + esc(t.label) + "</div>" +
        '<div class="dc-meta">' + t.rows + " rows · " + esc(t.file) + " · CSV</div>" +
        '<div class="dc-dl">⬇ Download</div></a>';
    }).join("");
    var link = document.getElementById("sqliteLink");
    if (link && db.sqlite) link.setAttribute("href", "database/" + db.sqlite);
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
    // Monte-Carlo for title / top-5 (UCL) / top-7 (European) / relegation probabilities.
    var N = 3000, tally = {};
    teams.forEach(function (t) { tally[t] = { title: 0, top5: 0, europe: 0, rel: 0, ptsSum: 0 }; });
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
        if (rk <= 5) tally[t].top5++;
        if (rk <= 7) tally[t].europe++;
        if (rk > teams.length - 3) tally[t].rel++;
      });
    }
    var proj = teams.map(function (t) {
      return {
        team: t, curPts: base[t], expPts: exp[t], projPts: tally[t].ptsSum / N,
        title: tally[t].title / N, top5: tally[t].top5 / N, europe: tally[t].europe / N, rel: tally[t].rel / N
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
        "<td>" + bar(r.top5, "b-top4") + "</td>" +
        "<td>" + bar(r.europe, "b-eu") + "</td>" +
        "<td>" + bar(r.rel, "b-rel") + "</td></tr>";
    }).join("");
    host.innerHTML =
      "<table class='proj'><thead><tr><th>#</th><th class='team'>Team</th><th>Pts now</th><th>Proj pts</th>" +
      "<th>Title</th><th>Top 5</th><th>Top 7</th><th>Relegated</th></tr></thead><tbody>" + body + "</tbody></table>" +
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
                   xg: function (p) { return p.xg || 0; }, xa: function (p) { return p.xa || 0; },
                   rating: function (p) { return p.rating || 0; } }[key] || function (p) { return 0; };
    var rows = PLAYERS.slice().sort(function (a, b) { return metric(b) - metric(a); }).slice(0, 50);
    var body = rows.map(function (p, i) {
      return "<tr><td class='pos'>" + (i + 1) + "</td>" +
        "<td class='team'><div class='team-cell'>" + logoImg(p.team) + "<span class='nm'>" + esc(p.name) + "</span></div></td>" +
        "<td>" + esc(p.team) + "</td><td>" + (p.mp || 0) + "</td><td>" + (p.g || 0) + "</td><td>" + (p.a || 0) + "</td>" +
        "<td>" + (p.xg != null ? p.xg.toFixed(2) : "–") + "</td><td>" + (p.xa != null ? p.xa.toFixed(2) : "–") + "</td>" +
        "<td>" + (p.rating != null ? p.rating.toFixed(2) : "–") + "</td></tr>";
    }).join("");
    host.innerHTML = "<table><thead><tr><th>#</th><th class='team'>Player</th><th>Team</th><th>MP</th><th>G</th><th>A</th><th>xG</th><th>xA</th><th>Rating</th></tr></thead><tbody>" + body + "</tbody></table>";
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
  // Standouts + Team Lab are dot-heavy (the all-teams shot map alone is thousands of
  // circles), so we render them lazily the first time their tab is opened and re-mark
  // them dirty on a season switch rather than redrawing on every renderAll.
  var heavyDirty = { standouts: true, teamlab: true, playerlab: true };
  function renderHeavyIfNeeded(view) {
    if (view === "standouts" && heavyDirty.standouts) { refreshStandouts(); heavyDirty.standouts = false; }
    else if (view === "teamlab" && heavyDirty.teamlab) { refreshTeamLab(); heavyDirty.teamlab = false; }
    else if (view === "playerlab" && heavyDirty.playerlab) { refreshPlayerLab(); heavyDirty.playerlab = false; }
  }

  function renderAll() {
    D = ALL.seasons[season];
    PLAYERS = PLAYERS_ALL[season] || [];
    renderOverview();
    renderStandings();
    populateMatchdayFilter();
    renderMatches();
    renderPlayers();
    renderXgLab();
    renderProjection();
    renderData();
    renderDownloads();
    // defer the heavy views; redraw now only if one is already on screen
    heavyDirty.standouts = true; heavyDirty.teamlab = true; heavyDirty.playerlab = true;
    var active = document.querySelector("nav.tabs button.active");
    if (active) renderHeavyIfNeeded(active.dataset.view);
    var foot = document.getElementById("footNote");
    if (foot) foot.textContent = "Season " + season + " · generated " + ALL.generated + " · " +
      (D.counts.played || 0) + " matches played · " + (D.counts.with_xg || 0) + " with xG · Premier League analytics pipeline.";
  }

  /* ======================= PLAYER LAB ======================= */
  // Ported from the BCN dashboard, adapted to the whole league: pick a TEAM, then a
  // player (+ optional compare from any club). Stat cards / radar / head-to-head bars
  // read the season aggregates already in players.js; the action maps read a per-team
  // event file (player_lab/<slug>.js) fetched on demand — like match pages load their
  // matches_detail. No tackles map (league matches_detail carries no tackle events).
  var PL_ACC = "#3ddc97", PL_BLUE = "#4ea1ff", PL_MUTED = "#93a0bd", PL_RED = "#ff5e7a";
  var PL = { main: null, cmp: null, teams: {} };   // main/cmp store "Team @@ Player"
  var PL_MAPS = [["shots", "Shots"], ["dribbles", "Take-ons"], ["passes", "Passes"], ["prog", "Progressive passes"]];
  var PL_RADAR = [
    { k: "g", t: "Finishing" }, { k: "ga", t: "G+A" }, { k: "shots", t: "Shooting" },
    { k: "keyPasses", t: "Creativity" }, { k: "dribbles", t: "Dribbling" },
    { k: "def", t: "Defending" }, { k: "aerials", t: "Aerials" }, { k: "rating", t: "Rating", raw: true }
  ];
  function plN2(x) { return (Math.round((x || 0) * 100) / 100).toFixed(2); }
  function plSgn(x) { x = Math.round((x || 0) * 100) / 100; return (x > 0 ? "+" : "") + x.toFixed(2); }
  function plSlug(t) { return t.replace(/[^A-Za-z0-9]+/g, "_").replace(/^_+|_+$/g, ""); }
  function plFind(team, name) {
    for (var i = 0; i < PLAYERS.length; i++) if (PLAYERS[i].team === team && PLAYERS[i].name === name) return PLAYERS[i];
    return null;
  }
  function plPer90(p, k) {
    var m = p.mins || 0;
    if (k === "def") return m ? ((p.tackles || 0) + (p.interceptions || 0)) / m * 90 : 0;
    return m ? (p[k] || 0) / m * 90 : 0;
  }
  function plVal(p, mt) { return mt.raw ? (p[mt.k] || 0) : plPer90(p, mt.k); }
  function plPct(pool, val, getter) {
    var below = 0;
    for (var i = 0; i < pool.length; i++) if (getter(pool[i]) <= val) below++;
    return pool.length ? Math.round(100 * below / pool.length) : 0;
  }
  // hover tooltip on the SVG marks — reuses the shared floating #tooltip + tipHTML
  function plTipWire(host) {
    if (!host || host._plTip) return;
    host._plTip = 1;
    host.addEventListener("pointermove", function (e) {
      var t = e.target, inf = t && t.getAttribute && t.getAttribute("data-info");
      if (inf) {
        tooltip.innerHTML = tipHTML(inf); tooltip.style.opacity = "1";
        tooltip.style.left = (e.clientX + 14) + "px"; tooltip.style.top = (e.clientY + 14) + "px";
      } else tooltip.style.opacity = "0";
    });
    host.addEventListener("pointerleave", function () { tooltip.style.opacity = "0"; });
  }

  // stat card; shows a second (compare) player's value SIDE BY SIDE when picked
  function plCard(mv, cv, k, cls) {
    if (cv == null) return '<div class="stat"><div class="v ' + (cls || "") + '">' + mv + '</div><div class="k">' + k + "</div></div>";
    return '<div class="stat"><div class="cmp-vals"><div class="v accent">' + mv +
      '</div><div class="v2">' + cv + '</div></div><div class="k">' + k + "</div></div>";
  }

  function plRadar(host, players, pool) {
    var W = 360, H = 340, cx = W / 2, cy = H / 2 + 6, R = 118, N = PL_RADAR.length, i, g;
    var svg = ['<svg viewBox="0 0 ' + W + " " + H + '" width="100%" class="scatter-svg">'];
    for (g = 1; g <= 4; g++) {
      var ring = [];
      for (i = 0; i < N; i++) { var a = -Math.PI / 2 + i / N * 2 * Math.PI, rr = R * g / 4; ring.push((cx + rr * Math.cos(a)).toFixed(1) + "," + (cy + rr * Math.sin(a)).toFixed(1)); }
      svg.push('<polygon points="' + ring.join(" ") + '" fill="none" stroke="#26304d" stroke-width="0.8"/>');
    }
    for (i = 0; i < N; i++) {
      var a2 = -Math.PI / 2 + i / N * 2 * Math.PI;
      var lx = cx + (R + 16) * Math.cos(a2), ly = cy + (R + 16) * Math.sin(a2);
      var anc = Math.abs(Math.cos(a2)) < 0.3 ? "middle" : (Math.cos(a2) > 0 ? "start" : "end");
      svg.push('<line x1="' + cx + '" y1="' + cy + '" x2="' + (cx + R * Math.cos(a2)).toFixed(1) + '" y2="' + (cy + R * Math.sin(a2)).toFixed(1) + '" stroke="#26304d" stroke-width="0.8"/>');
      svg.push('<text x="' + lx.toFixed(1) + '" y="' + (ly + 3).toFixed(1) + '" fill="' + PL_MUTED + '" font-size="10.5" text-anchor="' + anc + '">' + PL_RADAR[i].t + "</text>");
    }
    var cols = [PL_ACC, PL_BLUE];
    players.forEach(function (p, pi) {
      var pts = [], dots = "";
      for (i = 0; i < N; i++) {
        var mt = PL_RADAR[i], val = plVal(p, mt);
        var pct = plPct(pool, val, (function (m) { return function (q) { return plVal(q, m); }; })(mt)) / 100;
        var a3 = -Math.PI / 2 + i / N * 2 * Math.PI, rr2 = R * Math.max(0.04, pct);
        var vx = cx + rr2 * Math.cos(a3), vy = cy + rr2 * Math.sin(a3);
        pts.push(vx.toFixed(1) + "," + vy.toFixed(1));
        var info = p.name + " — " + mt.t + ": " + Math.round(pct * 100) + " pctl (" + val.toFixed(2) + (mt.raw ? "" : "/90") + ")";
        dots += '<circle cx="' + vx.toFixed(1) + '" cy="' + vy.toFixed(1) + '" r="3.4" fill="' + cols[pi] + '" stroke="#0b0f1a" stroke-width="1" data-info="' + esc(info) + '"/>';
      }
      svg.push('<polygon points="' + pts.join(" ") + '" fill="' + cols[pi] + '" fill-opacity="0.18" stroke="' + cols[pi] + '" stroke-width="2"/>');
      svg.push(dots);
    });
    svg.push("</svg>");
    host.innerHTML = svg.join("");
    plTipWire(host);
    var leg = document.getElementById("plRadarLegend");
    if (leg) leg.innerHTML = players.map(function (p, pi) { return '<span class="pl-leg"><i class="pl-sw" style="background:' + cols[pi] + '"></i>' + esc(p.name) + "</span>"; }).join("");
  }

  // --- action maps: shots on a vertical HALF pitch (goal on top); rest on the full pitch
  var _plGid = 0, PL_HPW = 68, PL_HPH = 52;
  function plMapX(wy) { return (100 - wy) / 100 * PL_HPW; }
  function plMapY(wx) { return Math.max(-1, Math.min(1.03, (100 - wx) / 50)) * PL_HPH; }
  function plPitchHalf(inner) {
    var midx = PL_HPW / 2, boxW = 40.3, boxD = 16.5, sixW = 18.32, sixD = 5.5, goalW = 7.32;
    var s = '<svg viewBox="-1 -3 ' + (PL_HPW + 2) + " " + (PL_HPH + 5) + '" width="100%" style="display:block;background:#101a2e;border-radius:6px">';
    s += '<rect x="0.3" y="0.3" width="' + (PL_HPW - 0.6) + '" height="' + (PL_HPH - 0.6) + '" fill="none" stroke="#26304d" stroke-width="0.4"/>';
    s += '<rect x="' + (midx - boxW / 2).toFixed(1) + '" y="0.3" width="' + boxW + '" height="' + boxD + '" fill="none" stroke="#26304d" stroke-width="0.4"/>';
    s += '<rect x="' + (midx - sixW / 2).toFixed(1) + '" y="0.3" width="' + sixW + '" height="' + sixD + '" fill="none" stroke="#26304d" stroke-width="0.4"/>';
    s += '<rect x="' + (midx - goalW / 2).toFixed(1) + '" y="-1.6" width="' + goalW + '" height="1.6" fill="none" stroke="#43e8a0" stroke-width="0.5"/>';
    s += '<path d="M ' + (midx - 7.3) + " " + boxD + " A 9.15 9.15 0 0 0 " + (midx + 7.3) + " " + boxD + '" fill="none" stroke="#26304d" stroke-width="0.4"/>';
    return s + inner + "</svg>";
  }
  function plPitchFull(inner) {
    return '<svg viewBox="0 0 100 64" width="100%" style="display:block;background:#101a2e;border-radius:6px">' +
      '<rect x="0.4" y="0.4" width="99.2" height="63.2" fill="none" stroke="#26304d" stroke-width="0.4"/>' +
      '<line x1="50" y1="0" x2="50" y2="64" stroke="#26304d" stroke-width="0.4"/>' +
      '<circle cx="50" cy="32" r="7" fill="none" stroke="#26304d" stroke-width="0.4"/>' +
      '<rect x="83" y="18" width="17" height="28" fill="none" stroke="#26304d" stroke-width="0.4"/>' +
      '<rect x="0" y="18" width="17" height="28" fill="none" stroke="#26304d" stroke-width="0.4"/>' + inner + "</svg>";
  }
  function plGraph(host, events, kind, color) {
    if (!host) return;
    events = events || [];
    if (events.length > 400) { var st = Math.ceil(events.length / 400); events = events.filter(function (_, ix) { return ix % st === 0; }); }
    var gid = "plg" + (_plGid++), GREEN = "#43e8a0", RED = PL_RED, half = kind === "shots";
    function di(t) { return ' data-info="' + esc(t) + '"'; }
    function opp(e) { var o = e[e.length - 1]; return o ? " — vs " + o : ""; }
    function pt(wx, wy) { return half ? [plMapX(wy), plMapY(wx)] : [wx, 64 - wy * 0.64]; }
    var s = '<defs><marker id="' + gid + 'g" markerWidth="4" markerHeight="4" refX="3" refY="2" orient="auto"><path d="M0,0 L4,2 L0,4 Z" fill="' + GREEN + '"/></marker>' +
      '<marker id="' + gid + 'r" markerWidth="4" markerHeight="4" refX="3" refY="2" orient="auto"><path d="M0,0 L4,2 L0,4 Z" fill="' + RED + '"/></marker></defs>';
    if (kind === "shots") {
      events.forEach(function (e) { // [x,y,gy,xg,goal,ot,min,opp]
        var a = pt(e[0], e[1]), b = pt(100, e[2]), xg = e[3], goal = e[4], ot = e[5];
        var r = 0.25 + Math.sqrt(xg) * 0.7, col = goal ? GREEN : RED, solid = goal || ot;
        var out = goal ? "GOAL" : ot ? "On target" : "Off target / blocked";
        var info = e[6] + "' — xG " + xg.toFixed(2) + " · " + out + opp(e);
        s += '<line x1="' + a[0].toFixed(1) + '" y1="' + a[1].toFixed(1) + '" x2="' + b[0].toFixed(1) + '" y2="' + b[1].toFixed(1) +
          '" stroke="' + col + '" stroke-width="' + (goal ? 0.3 : 0.2) + '" stroke-opacity="' + (goal ? 0.8 : 0.28) + '"/>';
        s += '<circle cx="' + a[0].toFixed(1) + '" cy="' + a[1].toFixed(1) + '" r="' + r.toFixed(1) +
          '" fill="' + (solid ? col : "none") + '" fill-opacity="0.6" stroke="' + col + '" stroke-width="' + (solid ? 0 : 0.32) + '"' + di(info) + "/>";
      });
    } else { // dribbles / passes / prog on the full pitch (attacking right)
      events.forEach(function (e) {
        var a = pt(e[0], e[1]), ok, ex, ey, prog = 0, mn, info, b;
        if (kind === "dribbles") { // [x,y,-1,-1,ok,min,opp]
          ok = e[4]; mn = e[5]; info = mn + "' — Take-on " + (ok ? "won" : "lost") + opp(e);
          var c0 = ok ? GREEN : RED;
          s += '<circle cx="' + a[0].toFixed(1) + '" cy="' + a[1].toFixed(1) + '" r="0.9" fill="' + (ok ? c0 : "none") +
            '" stroke="' + c0 + '" stroke-width="0.4"' + di(info) + "/>"; return;
        }
        b = pt(e[2], e[3]); ex = b[0]; ey = b[1]; ok = e[4]; prog = kind === "prog" ? 1 : e[5]; mn = e[6];
        info = mn + "' — " + (ok ? "Complete" : "Incomplete") + (prog ? " · progressive" : "") + opp(e);
        var col = ok ? (prog ? GREEN : "#1f9d5e") : RED, mk = "url(#" + gid + (ok ? "g" : "r") + ")";
        s += '<line x1="' + a[0].toFixed(1) + '" y1="' + a[1].toFixed(1) + '" x2="' + ex.toFixed(1) + '" y2="' + ey.toFixed(1) +
          '" stroke="' + col + '" stroke-width="' + (prog ? 0.45 : 0.28) + '" stroke-opacity="0.72"' + (ok ? "" : ' stroke-dasharray="0.9 0.9"') + ' marker-end="' + mk + '"' + di(info) + "/>";
      });
    }
    host.innerHTML = half ? plPitchHalf(s) : plPitchFull(s);
    plTipWire(host);
  }
  function plMapSummary(arr, kind, passes) {
    arr = arr || []; var n = arr.length, i;
    if (kind === "shots") {
      var g = 0, ot = 0;
      for (i = 0; i < n; i++) { if (arr[i][4]) g++; if (arr[i][5]) ot++; }
      return n + " shots · " + ot + " on target · " + g + " goals · " + (n ? Math.round(100 * g / n) : 0) + "% conv";
    }
    if (kind === "prog") { var tp = passes ? passes.length : 0; return n + " progressive · " + (tp ? Math.round(100 * n / tp) : 0) + "% of passes"; }
    var oi = 4, ok = 0;
    for (i = 0; i < n; i++) if (arr[i][oi]) ok++;
    var w = { dribbles: ["take-ons", "won", "lost"], passes: ["passes", "complete", "incomplete"] }[kind] || ["", "ok", "fail"];
    return n + " " + w[0] + " · " + ok + " " + w[1] + " · " + (n - ok) + " " + w[2] + " · " + (n ? Math.round(100 * ok / n) : 0) + "%";
  }

  function plEvents(team, name) { var t = (window.LL_PLAYERLAB || {})[team] || {}; return t[name] || { shots: [], dribbles: [], passes: [] }; }
  function plDataFor(ev, kind) { return kind === "prog" ? (ev.passes || []).filter(function (q) { return q[5]; }) : (ev[kind] || []); }
  function plLoadTeam(team, cb) {
    if ((window.LL_PLAYERLAB || {})[team]) { cb(); return; }
    var sc = document.createElement("script");
    sc.src = "player_lab/" + plSlug(team) + ".js";
    sc.onload = cb; sc.onerror = function () { cb(); };
    document.head.appendChild(sc);
  }
  function plDrawMaps(main, pc, cmpTeam) {
    var ea = plEvents(main.team, main.name), eb = pc ? plEvents(cmpTeam, pc.name) : null;
    var cols = pc ? "1fr 1fr" : "1fr", host = document.getElementById("plHeatGrid");
    host.innerHTML = PL_MAPS.map(function (mt, i) {
      var sumA = '<div class="pl-map-sum" style="color:' + PL_ACC + '">' + (pc ? "<b>" + esc(main.name) + "</b> · " : "") + plMapSummary(plDataFor(ea, mt[0]), mt[0], ea.passes) + "</div>";
      var sumB = pc ? '<div class="pl-map-sum" style="color:' + PL_BLUE + '"><b>' + esc(pc.name) + "</b> · " + plMapSummary(plDataFor(eb, mt[0]), mt[0], eb.passes) + "</div>" : "";
      return '<div class="pl-map"><div class="pl-map-title">' + mt[1] + "</div>" + sumA + sumB +
        '<div class="pl-map-cols" style="grid-template-columns:' + cols + '"><div id="plg_a_' + i + '"></div>' + (pc ? '<div id="plg_b_' + i + '"></div>' : "") + "</div></div>";
    }).join("");
    PL_MAPS.forEach(function (mt, i) {
      plGraph(document.getElementById("plg_a_" + i), plDataFor(ea, mt[0]), mt[0], PL_ACC);
      if (pc) plGraph(document.getElementById("plg_b_" + i), plDataFor(eb, mt[0]), mt[0], PL_BLUE);
    });
  }
  function plRender() {
    if (!PLAYERS.length || !PL.main) { var h = document.getElementById("plStats"); if (h) h.innerHTML = ""; return; }
    var mparts = PL.main.split(" @@ ");
    var main = plFind(mparts[0], mparts[1]); if (!main) return;
    var cmpTeam = null, cmpName = null;
    if (PL.cmp) { var parts = PL.cmp.split(" @@ "); cmpTeam = parts[0]; cmpName = parts[1]; }
    var pc = cmpName ? plFind(cmpTeam, cmpName) : null;
    function rtg(q) { return q.rating ? q.rating.toFixed(2) : "&ndash;"; }
    var s = "";
    s += plCard(main.mp, pc ? pc.mp : null, "Apps");
    s += plCard(main.mins, pc ? pc.mins : null, "Minutes");
    s += plCard(main.g, pc ? pc.g : null, "Goals", "accent");
    s += plCard(main.a, pc ? pc.a : null, "Assists", "blue");
    s += plCard(plN2(main.xg), pc ? plN2(pc.xg) : null, "xG");
    s += plCard(plSgn(main.xg_diff), pc ? plSgn(pc.xg_diff) : null, "xG&plusmn;", main.xg_diff >= 0 ? "pos" : "neg");
    s += plCard(plN2(main.xa), pc ? plN2(pc.xa) : null, "xA", "blue");
    s += plCard(plN2(main.xgi), pc ? plN2(pc.xgi) : null, "xGI", "accent");
    s += plCard(main.shots, pc ? pc.shots : null, "Shots");
    s += plCard(main.keyPasses, pc ? pc.keyPasses : null, "Key Passes");
    s += plCard(rtg(main), pc ? rtg(pc) : null, "Avg Rating", "accent");
    document.getElementById("plStats").innerHTML = s;

    var pool = PLAYERS.filter(function (q) { return (q.mins || 0) >= 450; });
    var players = [main]; if (pc) players.push(pc);
    plRadar(document.getElementById("plRadar"), players, pool.length ? pool : PLAYERS);

    var barsCard = document.getElementById("plBarsCard");
    if (pc) {
      document.getElementById("plCompareTitle").innerHTML = esc(main.name) + " vs " + esc(pc.name);
      var mets = [["g", "Goals"], ["a", "Assists"], ["shots", "Shots"], ["keyPasses", "Key passes"],
                  ["dribbles", "Take-ons"], ["tackles", "Tackles"], ["interceptions", "Interceptions"], ["passes", "Passes"]];
      document.getElementById("plCompareBody").innerHTML = mets.map(function (mt) {
        var av = main[mt[0]] || 0, bv = pc[mt[0]] || 0, t = (av + bv) || 1, ap = Math.round(100 * av / t);
        return '<div class="stat-cmp"><div class="sc-val' + (av >= bv ? " win" : "") + '">' + av + "</div>" +
          '<div><div class="sc-label">' + mt[1] + '</div><div class="sc-bar">' +
          '<div class="sc-fill h" style="width:' + ap + '%"></div><div class="sc-fill a" style="width:' + (100 - ap) + '%"></div></div></div>' +
          '<div class="sc-val' + (bv > av ? " win" : "") + '">' + bv + "</div></div>";
      }).join("");
      barsCard.style.display = "";
    } else barsCard.style.display = "none";

    document.getElementById("plHeatNameA").textContent = main.name;
    document.getElementById("plHeatNameB").textContent = pc ? pc.name : "";
    // stamp the render so a slower earlier load can't overdraw a newer selection
    var seq = ++_plRenderSeq;
    plLoadTeam(main.team, function () {
      if (pc) plLoadTeam(cmpTeam, function () { if (seq === _plRenderSeq) plDrawMaps(main, pc, cmpTeam); });
      else if (seq === _plRenderSeq) plDrawMaps(main, null, null);
    });
  }
  var _plRenderSeq = 0;
  function plTeamList() {
    var seen = {}, out = [];
    PLAYERS.forEach(function (p) { if (p.team && !seen[p.team]) { seen[p.team] = 1; out.push(p.team); } });
    return out.sort();
  }
  function plTeamsActive() {
    return Object.keys(PL.teams || {}).filter(function (k) { return PL.teams[k]; });
  }
  function plPool() {
    // players from the badge-selected teams (none selected = the whole league)
    var filtered = plTeamsActive().length > 0;
    return PLAYERS.filter(function (p) { return (p.mp || 0) > 0 && (!filtered || PL.teams[p.team]); });
  }
  function plGroupedOptions(pool, withNone) {
    var byTeam = {};
    pool.forEach(function (p) { (byTeam[p.team] = byTeam[p.team] || []).push(p); });
    var opts = withNone ? '<option value="">&mdash; none &mdash;</option>' : "";
    Object.keys(byTeam).sort().forEach(function (t) {
      opts += '<optgroup label="' + esc(t) + '">';
      byTeam[t].sort(function (a, b) { return (b.ga || 0) - (a.ga || 0); }).forEach(function (p) {
        opts += '<option value="' + esc(t + " @@ " + p.name) + '">' + esc(p.name) + "</option>";
      });
      opts += "</optgroup>";
    });
    return opts;
  }
  function plBuildPlayers() {
    var mainSel = document.getElementById("plMain"), pool = plPool();
    mainSel.innerHTML = plGroupedOptions(pool, false);
    var ok = PL.main && pool.some(function (p) { return (p.team + " @@ " + p.name) === PL.main; });
    if (!ok) {
      var top = pool.slice().sort(function (a, b) { return (b.ga || 0) - (a.ga || 0); })[0];
      PL.main = top ? (top.team + " @@ " + top.name) : null;
    }
    mainSel.value = PL.main || "";
  }
  function plBuildCompare() {
    var cmpSel = document.getElementById("plCompare");
    cmpSel.innerHTML = plGroupedOptions(plPool(), true);
    cmpSel.value = PL.cmp || "";
    if (cmpSel.value !== (PL.cmp || "")) PL.cmp = null;
  }
  // Clickable team badges — THE team filter for the whole lab (replaces the old Team
  // dropdown). Toggle one or more clubs to narrow BOTH player lists; "All" clears.
  function plBuildBadges() {
    var host = document.getElementById("plBadges");
    if (!host) return;
    var any = plTeamsActive().length > 0;
    host.innerHTML = '<button type="button" class="pl-badge pl-badge-all' + (any ? "" : " on") + '" data-team="">All</button>' +
      plTeamList().map(function (t) {
        return '<button type="button" class="pl-badge' + (PL.teams[t] ? " on" : "") +
          '" data-team="' + esc(t) + '" title="' + esc(t) + '">' + logoImg(t) + "</button>";
      }).join("");
    if (!host._wired) {
      host._wired = 1;
      host.addEventListener("click", function (e) {
        var btn = e.target && e.target.closest ? e.target.closest(".pl-badge") : null;
        if (!btn) return;
        var t = btn.getAttribute("data-team");
        if (!t) PL.teams = {};                      // "All" resets the filter
        else PL.teams[t] = !PL.teams[t];
        // drop the compare pick if its club fell out; the main pick re-defaults in build
        if (PL.cmp && plTeamsActive().length && !PL.teams[PL.cmp.split(" @@ ")[0]]) PL.cmp = null;
        plBuildBadges();
        plBuildPlayers();
        plBuildCompare();
        plRender();
      });
    }
  }
  function plBuild() {
    if (!document.getElementById("plMain") || !PLAYERS.length) return;
    plBuildBadges();
    plBuildPlayers();
    plBuildCompare();
  }
  function wirePlayerLab() {
    var mainSel = document.getElementById("plMain"), cmpSel = document.getElementById("plCompare");
    if (!mainSel || mainSel._wired) return;
    mainSel._wired = 1;
    mainSel.addEventListener("change", function () { PL.main = mainSel.value; plRender(); });
    cmpSel.addEventListener("change", function () { PL.cmp = cmpSel.value || null; plRender(); });
  }
  function refreshPlayerLab() { plBuild(); plRender(); }

  function initControls() {
    // tabs
    var tabs = document.querySelectorAll("nav.tabs button");
    tabs.forEach(function (b) {
      b.addEventListener("click", function () {
        tabs.forEach(function (x) { x.classList.remove("active"); });
        b.classList.add("active");
        document.querySelectorAll(".view").forEach(function (v) { v.classList.remove("active"); });
        document.getElementById("view-" + b.dataset.view).classList.add("active");
        renderHeavyIfNeeded(b.dataset.view);
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
    // Team Lab search
    var tl = document.getElementById("tlSearch");
    if (tl) tl.addEventListener("input", renderDbTeamTable);
    // Standouts + Team Lab one-time control wiring (renders happen per season in renderAll)
    wireStandouts();
    wireTeamLab();
    wirePlayerLab();
  }

  initControls();
  renderAll();
})();
