/* Match Centre → PNG, drawn in the browser.
   The static site cannot run the Python renderer, so this draws the match board into a
   canvas in the "Broadcast Kinetic" skin and hands the viewer a PNG on the spot: no
   server, no scheduled task, no image files in the repo.

   Layout follows the pipeline infographic: a wide board with
     row 1  home lineup | home pass network | match statistics | away pass network | away lineup
     row 2  home shot map (half pitch)      | final-third passes (both teams) | away shot map
   window.LL_EXPORT.render(M) -> Promise<HTMLCanvasElement>;  window.LL_EXPORT.download(canvas, filename)

   M = { home:{name,color,score}, away:{name,color,score}, matchday, date, venue, season,
         goals:[...], stats:[{label,h,a,hBetter,aBetter,hpct,isXg,pct}],
         shots:[{team,x,y,xg,goal,onTarget}], passes:[{team,x,y,ex,ey,ok,player,recv}],
         lineups:{home:{starters:[...],subs:[...]}, away:{...}}, crests:{home,away}, source } */
(function () {
  "use strict";

  var W = 1800, H = 1262, SCALE = 2, PAD = 30;
  var C = {
    bg: "#0c0d10", plate: "#15171c", plate2: "#1c1f26", well: "#090a0d", line: "rgba(255,255,255,0.10)",
    text: "#f5f7fa", muted: "#b9bfc9", muted2: "#737b88", lime: "#d7ff3a", limeDim: "#96b328",
    red: "#ff2a4d", info: "#9fd0ff", warn: "#ffb020", pitch: "#0f3d22", pitchLine: "rgba(255,255,255,0.45)"
  };
  var DISP = '"Barlow Condensed", "Arial Narrow", "Segoe UI", sans-serif';
  var BODY = '"Barlow", "Segoe UI", system-ui, sans-serif';

  function font(weight, size, italic, fam) { return (italic ? "italic " : "") + weight + " " + size + "px " + (fam || DISP); }
  function s(v) { return String(v == null ? "" : v); }
  function loadImg(src) {
    return new Promise(function (res) {
      if (!src) { res(null); return; }
      var i = new Image(); i.onload = function () { res(i); }; i.onerror = function () { res(null); }; i.src = src;
    });
  }
  function slant(ctx, x, y, w, h, cut) {
    ctx.beginPath(); ctx.moveTo(x + cut, y); ctx.lineTo(x + w, y); ctx.lineTo(x + w - cut, y + h); ctx.lineTo(x, y + h); ctx.closePath();
  }
  function chamfer(ctx, x, y, w, h, c) {
    ctx.beginPath(); ctx.moveTo(x, y); ctx.lineTo(x + w - c, y); ctx.lineTo(x + w, y + c); ctx.lineTo(x + w, y + h);
    ctx.lineTo(x + c, y + h); ctx.lineTo(x, y + h - c); ctx.closePath();
  }
  function fitFont(ctx, text, weight, size, italic, maxW, fam) {
    for (var z = size; z > 10; z -= 1) { ctx.font = font(weight, z, italic, fam); if (ctx.measureText(text).width <= maxW) return z; }
    return 10;
  }
  function tracked(ctx, text, x, y, opts) {
    opts = opts || {};
    ctx.font = font(opts.weight || 700, opts.size || 13, false, DISP);
    ctx.fillStyle = opts.color || C.muted2; ctx.textBaseline = "alphabetic";
    var t = String(text).toUpperCase(), sp = opts.tracking == null ? 1.5 : opts.tracking, total = 0, i;
    for (i = 0; i < t.length; i++) total += ctx.measureText(t[i]).width + sp;
    var cx = opts.align === "center" ? x - total / 2 : opts.align === "right" ? x - total : x;
    ctx.textAlign = "left";
    for (i = 0; i < t.length; i++) { ctx.fillText(t[i], cx, y); cx += ctx.measureText(t[i]).width + sp; }
    return total;
  }
  function sectionTitle(ctx, text, x, y, align, color) {
    ctx.font = font(800, 20, true, DISP); ctx.fillStyle = color || C.text; ctx.textBaseline = "alphabetic";
    var w = ctx.measureText(text.toUpperCase()).width;
    var lx = align === "center" ? x - w / 2 - 14 : align === "right" ? x - w - 14 : x;
    ctx.fillStyle = C.lime; ctx.save(); ctx.transform(1, 0, -0.3, 1, 0, 0); ctx.fillRect(lx + 4 + (y - 16) * 0.3, y - 16, 5, 18); ctx.restore();
    ctx.fillStyle = color || C.text; ctx.textAlign = "left"; ctx.fillText(text.toUpperCase(), lx + 16, y);
  }
  function inkFor(hex) {
    var m = /^#?([0-9a-f]{6})$/i.exec(String(hex || "")); if (!m) return "#fff";
    var n = parseInt(m[1], 16), r = (n >> 16) & 255, g = (n >> 8) & 255, b = n & 255;
    return (0.2126 * r + 0.7152 * g + 0.0722 * b) > 150 ? "#0c0d10" : "#fff";
  }
  function ratingColor(r) { return r == null ? C.muted2 : r >= 7.5 ? C.lime : r >= 6.5 ? C.text : C.red; }
  function fmtStat(v, isXg, pct) { if (v == null) return "–"; return (isXg ? (+v).toFixed(2) : v) + (pct ? "%" : ""); }
  function hatch(ctx, bright, dark) {
    var p = document.createElement("canvas"); p.width = p.height = 10;
    var g = p.getContext("2d");
    g.fillStyle = dark; g.fillRect(0, 0, 10, 10);
    g.strokeStyle = bright; g.lineWidth = 5;
    g.beginPath(); g.moveTo(-3, 13); g.lineTo(13, -3); g.moveTo(-3, 3); g.lineTo(3, -3); g.moveTo(7, 13); g.lineTo(13, 7); g.stroke();
    return ctx.createPattern(p, "repeat");
  }

  /* ---------- pitches (WhoScored 0-100 in each team's own attacking frame) ---------- */
  // vertical pitch, attacking UP: draw x = width axis (64 units), draw y = length (100 units)
  function vPitch(ctx, x0, y0, w, fromX) {
    // fromX: draw only the part with raw x >= fromX (half pitch when 50)
    var k = w / 64, len = (100 - fromX), h = len * k;
    ctx.save(); ctx.translate(x0, y0); ctx.scale(k, k);
    ctx.fillStyle = C.pitch; ctx.fillRect(0, 0, 64, len);
    ctx.strokeStyle = C.pitchLine; ctx.lineWidth = 0.35;
    ctx.strokeRect(0.5, 0.5, 63, len - 1);
    // boxes at the top goal (raw x 100 → draw y 0)
    ctx.strokeRect(13, 0.5, 38, 15.1);       // penalty box: width 38 of 64, depth 15.1
    ctx.strokeRect(23.4, 0.5, 17.2, 5);      // six-yard
    ctx.beginPath(); ctx.arc(32, 10.5, 0.6, 0, Math.PI * 2); ctx.fillStyle = C.pitchLine; ctx.fill();
    ctx.beginPath(); ctx.arc(32, 10.5, 8.3, Math.acos(-(15.6 - 10.5) / 8.3) * 0 + 0.66, Math.PI - 0.66); ctx.stroke(); // the D
    ctx.fillStyle = "rgba(255,255,255,0.10)"; ctx.fillRect(28.5, -1.6, 7, 1.6); ctx.strokeRect(28.5, -1.6, 7, 1.6);
    if (fromX <= 50) { ctx.beginPath(); ctx.moveTo(0.5, 50); ctx.lineTo(63.5, 50); ctx.stroke(); ctx.beginPath(); ctx.arc(32, 50, 8.3, Math.PI, 2 * Math.PI); ctx.stroke(); }
    if (fromX <= 0) {
      ctx.beginPath(); ctx.arc(32, 50, 8.3, 0, Math.PI); ctx.stroke();
      ctx.strokeRect(13, len - 15.6, 38, 15.1); ctx.strokeRect(23.4, len - 5.5, 17.2, 5);
      ctx.beginPath(); ctx.arc(32, len - 10.5, 8.3, Math.PI + 0.66, 2 * Math.PI - 0.66); ctx.stroke();
    }
    ctx.restore();
    return { k: k, h: h, toX: function (rx, ry) { return x0 + (100 - ry) * 0.64 * k; }, toY: function (rx) { return y0 + (100 - rx) * k; } };
  }
  // horizontal full pitch, home attacking RIGHT, away attacking LEFT
  function hPitch(ctx, x0, y0, w) {
    var k = w / 100, h = 64 * k;
    ctx.save(); ctx.translate(x0, y0); ctx.scale(k, k);
    ctx.fillStyle = C.pitch; ctx.fillRect(0, 0, 100, 64);
    for (var i = 0; i < 10; i += 2) { ctx.fillStyle = "rgba(255,255,255,0.03)"; ctx.fillRect(i * 10, 0, 10, 64); }
    ctx.strokeStyle = C.pitchLine; ctx.lineWidth = 0.35;
    ctx.strokeRect(0.5, 0.5, 99, 63);
    ctx.beginPath(); ctx.moveTo(50, 0.5); ctx.lineTo(50, 63.5); ctx.stroke();
    ctx.beginPath(); ctx.arc(50, 32, 8.3, 0, Math.PI * 2); ctx.stroke();
    [false, true].forEach(function (right) {
      var bx = right ? 100 - 15.6 : 0.5, sbx = right ? 100 - 5.5 : 0.5;
      ctx.strokeRect(bx, 13, 15.1, 38); ctx.strokeRect(sbx, 23.4, 5, 17.2);
      var spot = right ? 100 - 10.5 : 10.5, edge = right ? bx : 15.6, a = Math.acos(Math.abs(edge - spot) / 8.3);
      ctx.beginPath(); if (right) ctx.arc(spot, 32, 8.3, Math.PI - a, Math.PI + a); else ctx.arc(spot, 32, 8.3, -a, a); ctx.stroke();
      var gx = right ? 99.5 : -1.1; ctx.fillStyle = "rgba(255,255,255,0.10)"; ctx.fillRect(gx, 28.5, 1.6, 7); ctx.strokeRect(gx, 28.5, 1.6, 7);
    });
    // final-third markers
    ctx.setLineDash([1, 1.5]); ctx.strokeStyle = "rgba(255,255,255,0.35)";
    ctx.beginPath(); ctx.moveTo(66.7, 0.5); ctx.lineTo(66.7, 63.5); ctx.moveTo(33.3, 0.5); ctx.lineTo(33.3, 63.5); ctx.stroke(); ctx.setLineDash([]);
    ctx.restore();
    return { k: k, h: h,
      toX: function (side, rx) { return x0 + (side === "home" ? rx : 100 - rx) * k; },
      toY: function (side, ry) { return y0 + (side === "home" ? (100 - ry) * 0.64 : ry * 0.64) * k; } };
  }
  function arrow(ctx, x1, y1, x2, y2, size) {
    var a = Math.atan2(y2 - y1, x2 - x1);
    ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); ctx.stroke();
    ctx.beginPath(); ctx.moveTo(x2, y2);
    ctx.lineTo(x2 - size * Math.cos(a - 0.45), y2 - size * Math.sin(a - 0.45));
    ctx.lineTo(x2 - size * Math.cos(a + 0.45), y2 - size * Math.sin(a + 0.45)); ctx.closePath(); ctx.fill();
  }

  /* ---------- pass network: starters' average pass positions + pass counts between them ---------- */
  function network(M, side) {
    var L = (M.lineups && M.lineups[side]) || {}, starters = L.starters || [];
    var byName = {}; starters.forEach(function (p) { byName[p.name] = { p: p, sx: 0, sy: 0, n: 0 }; });
    var pairs = {};
    (M.passes || []).forEach(function ps(pa) {
      if (pa.team !== side) return;
      var a = byName[pa.player];
      if (a) { a.sx += pa.x; a.sy += pa.y; a.n++; }
      var b = pa.recv ? byName[pa.recv] : null;
      if (b && pa.ok) { b.sx += pa.ex; b.sy += pa.ey; b.n++; }
      if (a && b && pa.ok && pa.player !== pa.recv) { var key = pa.player + "→" + pa.recv; pairs[key] = (pairs[key] || 0) + 1; }
    });
    var nodes = starters.filter(function (p) { return byName[p.name].n > 0; }).map(function (p) {
      var a = byName[p.name]; return { p: p, x: a.sx / a.n, y: a.sy / a.n };
    });
    var edges = Object.keys(pairs).map(function (k) { var ab = k.split("→"); return { a: ab[0], b: ab[1], n: pairs[k] }; });
    return { nodes: nodes, edges: edges };
  }

  function render(M) {
    var fontsReady = document.fonts && document.fonts.load
      ? Promise.all([document.fonts.load('italic 800 84px "Barlow Condensed"'), document.fonts.load('700 20px "Barlow Condensed"'),
                     document.fonts.load('600 14px "Barlow"'), document.fonts.load('500 14px "Barlow"')]).catch(function () {})
      : Promise.resolve();
    return Promise.all([fontsReady, loadImg(M.crests && M.crests.home), loadImg(M.crests && M.crests.away)]).then(function (r) {
      var crestH = r[1], crestA = r[2];
      var cv = document.createElement("canvas");
      cv.width = W * SCALE; cv.height = H * SCALE;
      var ctx = cv.getContext("2d"); ctx.scale(SCALE, SCALE);
      var hatchLime = hatch(ctx, C.lime, C.limeDim), hatchGrey = hatch(ctx, "rgba(255,255,255,0.28)", "rgba(255,255,255,0.10)");
      var homeCol = M.home.color || C.info, awayCol = M.away.color || C.red;

      /* ground */
      ctx.fillStyle = C.bg; ctx.fillRect(0, 0, W, H);
      ctx.save(); ctx.strokeStyle = "rgba(255,255,255,0.025)"; ctx.lineWidth = 2;
      for (var d = -H; d < W + H; d += 14) { ctx.beginPath(); ctx.moveTo(d, 0); ctx.lineTo(d + H * 0.7, H); ctx.stroke(); }
      ctx.restore();

      /* ---------- header ---------- */
      var mid = W / 2;
      var tab = "LaLiga" + (M.matchday ? " · Matchday " + M.matchday : "");
      ctx.font = font(700, 15, false, DISP);
      var tabW = 44; for (var ti = 0; ti < tab.length; ti++) tabW += ctx.measureText(tab[ti].toUpperCase()).width + 1.8;
      ctx.fillStyle = C.lime; ctx.beginPath(); ctx.moveTo(mid - tabW / 2, 0); ctx.lineTo(mid + tabW / 2, 0); ctx.lineTo(mid + tabW / 2 - 10, 28); ctx.lineTo(mid - tabW / 2 + 10, 28); ctx.closePath(); ctx.fill();
      tracked(ctx, tab, mid, 20, { size: 15, color: C.bg, align: "center", tracking: 1.8 });
      var sub = [M.date, M.venue].filter(Boolean).join("  ·  ");
      if (sub) { ctx.font = font(600, 14, false, BODY); ctx.fillStyle = C.muted2; ctx.textAlign = "center"; ctx.textBaseline = "alphabetic"; ctx.fillText(sub, mid, 48); }
      // scoreboard line
      var cy = 92, crest = 72;
      ctx.textBaseline = "middle";
      var scoreTxt = s(M.home.score == null ? "–" : M.home.score) + "  –  " + s(M.away.score == null ? "–" : M.away.score);
      ctx.font = font(800, 78, true, DISP); ctx.fillStyle = C.lime; ctx.textAlign = "center"; ctx.fillText(scoreTxt, mid, cy);
      var nameMax = mid - 220 - PAD - crest - 24;
      if (crestH) ctx.drawImage(crestH, PAD + 8, cy - crest / 2, crest, crest);
      var hz = fitFont(ctx, M.home.name.toUpperCase(), 800, 50, true, nameMax);
      ctx.font = font(800, hz, true, DISP); ctx.fillStyle = C.text; ctx.textAlign = "left"; ctx.fillText(M.home.name.toUpperCase(), PAD + 8 + crest + 22, cy);
      ctx.fillStyle = homeCol; ctx.fillRect(PAD + 8 + crest + 22, cy + 34, 120, 4);
      if (crestA) ctx.drawImage(crestA, W - PAD - 8 - crest, cy - crest / 2, crest, crest);
      var az = fitFont(ctx, M.away.name.toUpperCase(), 800, 50, true, nameMax);
      ctx.font = font(800, az, true, DISP); ctx.fillStyle = C.text; ctx.textAlign = "right"; ctx.fillText(M.away.name.toUpperCase(), W - PAD - 8 - crest - 22, cy);
      ctx.fillStyle = awayCol; ctx.fillRect(W - PAD - 8 - crest - 22 - 120, cy + 34, 120, 4);
      ctx.textBaseline = "alphabetic";

      /* ---------- row 1 geometry ---------- */
      var R1 = 158, R1H = 520;
      var colLine = 310, colNet = 320, colStat = 400, gap = 20;
      var xL1 = PAD, xN1 = xL1 + colLine + gap, xS = xN1 + colNet + gap, xN2 = xS + colStat + gap, xL2 = xN2 + colNet + gap;

      /* lineups */
      function lineup(side, x0, rightAligned) {
        var L = (M.lineups && M.lineups[side]) || { starters: [], subs: [] }, col = side === "home" ? homeCol : awayCol;
        var name = side === "home" ? M.home.name : M.away.name;
        sectionTitle(ctx, name + " lineup", rightAligned ? x0 + colLine : x0, R1 + 18, rightAligned ? "right" : "left");
        var y = R1 + 44, rowH = 25;
        var starters = L.starters || [], subs = L.subs || [];
        // who replaced whom: match a sub's "on" minute to a starter's "off" minute
        var replaced = {}; var pool = starters.slice();
        subs.forEach(function (sb) { for (var i = 0; i < pool.length; i++) { if (pool[i].off != null && pool[i].off === sb.on) { replaced[sb.name] = pool[i].name; pool.splice(i, 1); break; } } });
        function row(p, isSub) {
          var numX = rightAligned ? x0 + colLine - 4 : x0 + 4, nameX = rightAligned ? x0 + colLine - 36 : x0 + 36, rtX = rightAligned ? x0 + 4 : x0 + colLine - 4;
          ctx.textBaseline = "middle";
          ctx.font = font(700, 15, false, DISP); ctx.fillStyle = col; ctx.textAlign = rightAligned ? "right" : "left"; ctx.fillText(s(p.num), numX, y);
          var badges = "";
          for (var g = 0; g < (p.g || 0); g++) badges += "●";
          var nm = p.name.length > 22 ? p.name.slice(0, 21) + "…" : p.name;
          ctx.font = font(600, 15, false, BODY); ctx.fillStyle = C.text; ctx.textAlign = rightAligned ? "right" : "left"; ctx.fillText(nm, nameX, y);
          var nw = ctx.measureText(nm).width, bx = rightAligned ? nameX - nw - 8 : nameX + nw + 8;
          if (badges) { ctx.font = font(700, 11, false, BODY); ctx.fillStyle = C.lime; ctx.textAlign = rightAligned ? "right" : "left"; ctx.fillText(badges, bx, y); bx += (rightAligned ? -1 : 1) * (ctx.measureText(badges).width + 6); }
          if (p.a) { ctx.font = font(800, 12, false, DISP); ctx.fillStyle = C.info; ctx.fillText("A" + (p.a > 1 ? "×" + p.a : ""), bx, y); bx += (rightAligned ? -1 : 1) * 18; }
          if (p.yc) { ctx.fillStyle = "#f5c518"; ctx.fillRect(rightAligned ? bx - 7 : bx, y - 6, 7, 10); bx += (rightAligned ? -1 : 1) * 12; }
          if (p.rc) { ctx.fillStyle = C.red; ctx.fillRect(rightAligned ? bx - 7 : bx, y - 6, 7, 10); }
          var note = isSub ? (replaced[p.name] ? "for " + replaced[p.name].split(" ").slice(-1)[0] + " " + p.on + "'" : (p.on != null ? p.on + "'" : ""))
                           : (p.off != null ? "↓" + p.off + "'" : "");
          if (note) { ctx.font = font(600, 12, false, DISP); ctx.fillStyle = isSub ? C.muted2 : C.warn; ctx.textAlign = rightAligned ? "left" : "right"; ctx.fillText(note, rightAligned ? x0 + 50 : x0 + colLine - 50, y); }
          ctx.font = font(700, 15, false, DISP); ctx.fillStyle = ratingColor(p.rating); ctx.textAlign = rightAligned ? "left" : "right";
          ctx.fillText(p.rating != null ? p.rating.toFixed(1) : "–", rtX, y);
          y += rowH;
        }
        starters.forEach(function (p) { row(p, false); });
        if (subs.length) {
          y += 4; ctx.fillStyle = C.line; ctx.fillRect(x0, y - 8, colLine, 1);
          tracked(ctx, "Subs", rightAligned ? x0 + colLine - 4 : x0 + 4, y + 8, { size: 11, color: C.muted2, align: rightAligned ? "right" : "left" });
          y += 20;
          subs.slice(0, 6).forEach(function (p) { row(p, true); });
        }
        ctx.textBaseline = "alphabetic";
      }
      lineup("home", xL1, false);
      lineup("away", xL2, true);

      /* pass networks */
      function passNet(side, x0) {
        var col = side === "home" ? homeCol : awayCol, name = side === "home" ? M.home.name : M.away.name;
        sectionTitle(ctx, name + " · pass network", x0 + colNet / 2, R1 + 18, "center");
        var top = R1 + 30, pw = colNet, k = pw / 64, ph = 100 * k, scaleY = (R1H - 40) / ph; // squeeze to fit the row
        ctx.save(); ctx.translate(x0, top); ctx.scale(1, scaleY);
        var P = vPitch(ctx, 0, 0, pw, 0);
        var net = network(M, side), pos = {};
        net.nodes.forEach(function (n) { pos[n.p.name] = { x: (100 - n.y) * 0.64 * k, y: (100 - n.x) * k }; });
        var maxN = net.edges.reduce(function (m, e) { return Math.max(m, e.n); }, 1);
        net.edges.filter(function (e) { return e.n >= 2; }).forEach(function (e) {
          var a = pos[e.a], b = pos[e.b]; if (!a || !b) return;
          var t = e.n / maxN;
          ctx.strokeStyle = col; ctx.globalAlpha = 0.25 + 0.65 * t; ctx.lineWidth = (0.8 + 5 * t) / scaleY;
          ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke();
        });
        ctx.globalAlpha = 1;
        ctx.restore();
        // nodes drawn unsqueezed so circles stay round
        net.nodes.forEach(function (n) {
          var p = pos[n.p.name], px = x0 + p.x, py = top + p.y * scaleY;
          ctx.beginPath(); ctx.arc(px, py, 13, 0, Math.PI * 2); ctx.fillStyle = C.bg; ctx.fill();
          ctx.beginPath(); ctx.arc(px, py, 11, 0, Math.PI * 2); ctx.fillStyle = col; ctx.fill();
          ctx.font = font(800, 13, false, DISP); ctx.fillStyle = inkFor(col); ctx.textAlign = "center"; ctx.textBaseline = "middle"; ctx.fillText(s(n.p.num), px, py + 1);
        });
        ctx.textBaseline = "alphabetic";
        ctx.font = font(600, 12, false, BODY); ctx.fillStyle = C.muted2; ctx.textAlign = "center";
        ctx.fillText("Starters · average pass position · line width = passes between them", x0 + colNet / 2, R1 + R1H + 4);
      }
      passNet("home", xN1);
      passNet("away", xN2);

      /* match statistics */
      (function () {
        var y = R1 + 18;
        ctx.textBaseline = "alphabetic";
        ctx.font = font(800, 18, true, DISP); ctx.textAlign = "left"; ctx.fillStyle = homeCol; ctx.fillText(M.home.name.toUpperCase(), xS + 8, y);
        ctx.textAlign = "right"; ctx.fillStyle = awayCol; ctx.fillText(M.away.name.toUpperCase(), xS + colStat - 8, y);
        tracked(ctx, "Match statistics", xS + colStat / 2, y, { size: 12, color: C.muted2, align: "center", tracking: 2.4 });
        y += 14;
        var stats = M.stats || [], rowH = Math.min(44, Math.floor((R1H - 40) / Math.max(stats.length, 1)));
        stats.forEach(function (st, i) {
          if (i % 2 === 0) { ctx.fillStyle = C.plate; slant(ctx, xS, y, colStat, rowH, 6); ctx.fill(); }
          ctx.textBaseline = "middle";
          ctx.font = font(700, 24, false, DISP);
          ctx.fillStyle = st.hBetter ? C.lime : C.muted; ctx.textAlign = "left"; ctx.fillText(fmtStat(st.h, st.isXg, st.pct), xS + 26, y + rowH / 2);
          ctx.fillStyle = st.aBetter ? C.lime : C.muted; ctx.textAlign = "right"; ctx.fillText(fmtStat(st.a, st.isXg, st.pct), xS + colStat - 26, y + rowH / 2);
          tracked(ctx, st.label, xS + colStat / 2, y + rowH / 2 + 5, { size: 12, color: C.muted2, align: "center", tracking: 1.6 });
          // a slim hatched split under the label
          var bw = 120, bx = xS + colStat / 2 - bw / 2, hw = Math.round(bw * st.hpct / 100);
          ctx.fillStyle = st.hBetter ? hatchLime : hatchGrey; ctx.fillRect(bx, y + rowH - 7, Math.max(hw - 1, 0), 3);
          ctx.fillStyle = st.aBetter ? hatchLime : hatchGrey; ctx.fillRect(bx + hw + 1, y + rowH - 7, Math.max(bw - hw - 1, 0), 3);
          ctx.textBaseline = "alphabetic";
          y += rowH;
        });
      })();

      /* ---------- row 2 ---------- */
      var R2 = R1 + R1H + 40, shotW = 400, ftW = 640, xSh1 = PAD + 10, xFT = mid - ftW / 2, xSh2 = W - PAD - 10 - shotW;
      var teamShots = function (side) { return (M.shots || []).filter(function (x) { return x.team === side; }); };

      function shotMap(side, x0) {
        var col = side === "home" ? homeCol : awayCol, name = side === "home" ? M.home.name : M.away.name, sh = teamShots(side);
        var goals = sh.filter(function (x) { return x.goal; }).length, ot = sh.filter(function (x) { return x.onTarget; }).length;
        var xg = sh.reduce(function (t, x) { return t + (x.xg || 0); }, 0);
        sectionTitle(ctx, name + " · shot map", x0 + shotW / 2, R2 + 18, "center");
        ctx.font = font(600, 14, false, BODY); ctx.fillStyle = C.muted; ctx.textAlign = "center";
        ctx.fillText("Shots " + sh.length + "   |   On target " + ot + "   |   Goals " + goals + "   |   xG " + xg.toFixed(2), x0 + shotW / 2, R2 + 42);
        var P = vPitch(ctx, x0, R2 + 56, shotW, 48);
        sh.forEach(function (x) {
          if (x.x < 48) return;
          var px = P.toX(x.x, x.y), py = P.toY(x.x), r = 3.5 + Math.sqrt(Math.max(x.xg || 0, 0.005)) * 20;
          ctx.beginPath(); ctx.arc(px, py, r, 0, Math.PI * 2);
          if (x.goal) { ctx.fillStyle = C.lime; ctx.fill(); ctx.lineWidth = 2.5; ctx.strokeStyle = col; ctx.stroke(); }
          else if (x.onTarget) { ctx.fillStyle = col; ctx.globalAlpha = 0.85; ctx.fill(); ctx.globalAlpha = 1; }
          else { ctx.fillStyle = "rgba(0,0,0,0.25)"; ctx.fill(); ctx.lineWidth = 1.8; ctx.strokeStyle = col; ctx.stroke(); }
        });
        var ly = R2 + 56 + P.h + 16, lx = x0 + 4;
        ctx.textBaseline = "middle"; ctx.font = font(600, 13, false, BODY); ctx.textAlign = "left";
        ctx.beginPath(); ctx.arc(lx + 6, ly, 6, 0, Math.PI * 2); ctx.fillStyle = C.lime; ctx.fill(); ctx.fillStyle = C.muted; ctx.fillText("Goal", lx + 18, ly + 1); lx += 62;
        ctx.beginPath(); ctx.arc(lx + 6, ly, 6, 0, Math.PI * 2); ctx.fillStyle = col; ctx.fill(); ctx.fillStyle = C.muted; ctx.fillText("On target", lx + 18, ly + 1); lx += 96;
        ctx.beginPath(); ctx.arc(lx + 6, ly, 6, 0, Math.PI * 2); ctx.lineWidth = 1.8; ctx.strokeStyle = col; ctx.stroke(); ctx.fillStyle = C.muted; ctx.fillText("Off target", lx + 18, ly + 1); lx += 96;
        ctx.fillStyle = C.muted2; ctx.fillText("size = xG", lx, ly + 1);
        ctx.textBaseline = "alphabetic";
      }
      shotMap("home", xSh1);
      shotMap("away", xSh2);

      /* final-third passes */
      (function () {
        sectionTitle(ctx, "Final third passes", mid, R2 + 18, "center");
        ctx.font = font(600, 14, false, BODY); ctx.fillStyle = C.muted; ctx.textAlign = "center";
        ctx.fillText(M.home.name + " attack →   ·   ← " + M.away.name + " attack", mid, R2 + 42);
        var P = hPitch(ctx, xFT, R2 + 56, ftW);
        var tot = { home: [0, 0], away: [0, 0] }, lanes = { home: [0, 0, 0], away: [0, 0, 0] };
        (M.passes || []).forEach(function (pa) {
          if (pa.x >= 66.7 || pa.ex < 66.7) return;             // entries into the attacking third only
          var side = pa.team, col = side === "home" ? homeCol : awayCol;
          tot[side][1]++; if (pa.ok) tot[side][0]++;
          var lane = pa.ey >= 66.7 ? 0 : pa.ey >= 33.3 ? 1 : 2; lanes[side][lane]++;
          var x1 = P.toX(side, pa.x), y1 = P.toY(side, pa.y), x2 = P.toX(side, pa.ex), y2 = P.toY(side, pa.ey);
          ctx.strokeStyle = col; ctx.fillStyle = col; ctx.lineWidth = pa.ok ? 1.4 : 1;
          ctx.globalAlpha = pa.ok ? 0.85 : 0.35; ctx.setLineDash(pa.ok ? [] : [4, 4]);
          arrow(ctx, x1, y1, x2, y2, 6);
        });
        ctx.setLineDash([]); ctx.globalAlpha = 1;
        // lane counts at the entry line of each team (home: left wing at the top, away rotated)
        var laneY = [R2 + 56 + P.h * 0.18, R2 + 56 + P.h * 0.5, R2 + 56 + P.h * 0.82];
        ["LW", "CTR", "RW"].forEach(function (nm, i) {
          var ht = nm + ": " + lanes.home[i], at = nm + ": " + lanes.away[2 - i];
          ctx.font = font(700, 12, false, DISP);
          var hwid = ht.length * 8.6 + 14, awid = at.length * 8.6 + 14;
          ctx.fillStyle = "rgba(9,10,13,0.82)"; slant(ctx, xFT + 4, laneY[i] - 10, hwid, 20, 4); ctx.fill();
          slant(ctx, xFT + ftW - 4 - awid, laneY[i] - 10, awid, 20, 4); ctx.fill();
          tracked(ctx, ht, xFT + 12, laneY[i] + 4, { size: 12, color: homeCol, tracking: 1.2 });
          tracked(ctx, at, xFT + ftW - 12, laneY[i] + 4, { size: 12, color: awayCol, align: "right", tracking: 1.2 });
        });
        var fy = R2 + 56 + P.h + 22;
        ctx.textBaseline = "middle"; ctx.font = font(700, 16, false, DISP); ctx.textAlign = "center";
        var pct = function (t) { return t[1] ? Math.round(100 * t[0] / t[1]) : 0; };
        var hTxt = M.home.name + "  " + tot.home[0] + "/" + tot.home[1] + " made (" + pct(tot.home) + "%)", aTxt = M.away.name + "  " + tot.away[0] + "/" + tot.away[1] + " made (" + pct(tot.away) + "%)";
        var hw = ctx.measureText(hTxt).width, aw = ctx.measureText(aTxt).width, sep = 28, total = hw + sep + aw, sx = mid - total / 2;
        ctx.textAlign = "left"; ctx.fillStyle = homeCol; ctx.fillText(hTxt, sx, fy);
        ctx.fillStyle = C.muted2; ctx.fillText("|", sx + hw + sep / 2 - 3, fy);
        ctx.fillStyle = awayCol; ctx.fillText(aTxt, sx + hw + sep, fy);
        ctx.textBaseline = "alphabetic";
      })();

      /* footer */
      ctx.fillStyle = C.line; ctx.fillRect(PAD, H - 34, W - PAD * 2, 1);
      tracked(ctx, "rshiri.github.io/XLALIGA", PAD, H - 12, { size: 13, color: C.lime, tracking: 1.8 });
      tracked(ctx, (M.source || "Data: WhoScored · xG: our own shot model") + (M.season ? "   ·   La Liga " + M.season.replace("-", "/") : ""), W - PAD, H - 12, { size: 12, color: C.muted2, align: "right", tracking: 1.2 });
      return cv;
    });
  }

  function download(canvas, filename) {
    return new Promise(function (res, rej) {
      canvas.toBlob(function (blob) {
        if (!blob) { rej(new Error("Could not encode the image.")); return; }
        var a = document.createElement("a");
        a.href = URL.createObjectURL(blob); a.download = filename || "match.png";
        document.body.appendChild(a); a.click();
        setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); res(); }, 1200);
      }, "image/png");
    });
  }

  window.LL_EXPORT = { render: render, download: download };
})();
