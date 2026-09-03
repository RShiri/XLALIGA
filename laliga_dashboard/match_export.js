/* Match Centre → PNG, drawn in the browser.
   The static site cannot run the Python renderer, so this draws the match into a
   canvas in the "Broadcast Kinetic" skin (carbon ground, lime signal, hatched bars,
   condensed italic type) and hands the viewer a PNG on the spot: no server, no
   scheduled task, no image files in the repo.

   window.LL_EXPORT.render(M) -> Promise<HTMLCanvasElement>
   window.LL_EXPORT.download(canvas, filename)

   M = { home:{name,color,score}, away:{name,color,score}, meta, season, xg:[h,a],
         xgNote, goals:[{min,scorer,assist,team,pen,own}], stats:[{label,h,a,hBetter,aBetter,hpct}],
         shots:[{team,x,y,xg,goal,onTarget}], crests:{home,away}, source } */
(function () {
  "use strict";

  var W = 1200, SCALE = 2;
  var C = {
    bg: "#0c0d10", plate: "#15171c", plate2: "#1c1f26", well: "#090a0d", line: "rgba(255,255,255,0.12)",
    text: "#f5f7fa", muted: "#b9bfc9", muted2: "#737b88", lime: "#d7ff3a", limeDim: "#96b328",
    red: "#ff2a4d", info: "#9fd0ff", pitch: "#0f3d22", pitchLine: "rgba(255,255,255,0.45)"
  };
  var DISP = '"Barlow Condensed", "Arial Narrow", "Segoe UI", sans-serif';
  var BODY = '"Barlow", "Segoe UI", system-ui, sans-serif';

  function font(weight, size, italic, fam) { return (italic ? "italic " : "") + weight + " " + size + "px " + (fam || DISP); }
  function esc(s) { return String(s == null ? "" : s); }
  function loadImg(src) {
    return new Promise(function (res) {
      if (!src) { res(null); return; }
      var i = new Image(); i.onload = function () { res(i); }; i.onerror = function () { res(null); }; i.src = src;
    });
  }
  function hatch(ctx, bright, dark) {
    var p = document.createElement("canvas"); p.width = p.height = 10;
    var g = p.getContext("2d");
    g.fillStyle = dark; g.fillRect(0, 0, 10, 10);
    g.strokeStyle = bright; g.lineWidth = 5; g.lineCap = "butt";
    g.beginPath(); g.moveTo(-3, 13); g.lineTo(13, -3); g.moveTo(-3, 3); g.lineTo(3, -3); g.moveTo(7, 13); g.lineTo(13, 7); g.stroke();
    return ctx.createPattern(p, "repeat");
  }
  function slant(ctx, x, y, w, h, cut) {
    ctx.beginPath(); ctx.moveTo(x + cut, y); ctx.lineTo(x + w, y); ctx.lineTo(x + w - cut, y + h); ctx.lineTo(x, y + h); ctx.closePath();
  }
  function chamfer(ctx, x, y, w, h, c) {
    ctx.beginPath(); ctx.moveTo(x, y); ctx.lineTo(x + w - c, y); ctx.lineTo(x + w, y + c); ctx.lineTo(x + w, y + h);
    ctx.lineTo(x + c, y + h); ctx.lineTo(x, y + h - c); ctx.closePath();
  }
  function fitFont(ctx, text, weight, size, italic, maxW, fam) {
    for (var s = size; s > 12; s -= 2) {
      ctx.font = font(weight, s, italic, fam);
      if (ctx.measureText(text).width <= maxW) return s;
    }
    return 12;
  }
  function label(ctx, text, x, y, opts) {
    opts = opts || {};
    ctx.font = font(opts.weight || 700, opts.size || 14, false, DISP);
    ctx.fillStyle = opts.color || C.muted2;
    ctx.textAlign = opts.align || "left"; ctx.textBaseline = "alphabetic";
    var t = String(text).toUpperCase(), sp = opts.tracking == null ? 1.6 : opts.tracking;
    if (!sp) { ctx.fillText(t, x, y); return; }
    // letter-spaced uppercase, drawn glyph by glyph
    var total = 0, i;
    for (i = 0; i < t.length; i++) total += ctx.measureText(t[i]).width + sp;
    var cx = opts.align === "center" ? x - total / 2 : opts.align === "right" ? x - total : x;
    ctx.textAlign = "left";
    for (i = 0; i < t.length; i++) { ctx.fillText(t[i], cx, y); cx += ctx.measureText(t[i]).width + sp; }
  }
  function fmt(v, isXg, pct) { if (v == null) return "–"; return (isXg ? (+v).toFixed(2) : v) + (pct ? "%" : ""); }

  // WhoScored 0-100 → pitch units (100 × 64), home attacking right, away attacking left
  var PW = 100, PH = 64;
  function tx(side, x) { return side === "home" ? x : PW - x; }
  function ty(side, y) { return side === "home" ? PH - y * (PH / 100) : y * (PH / 100); }

  function drawPitch(ctx, x0, y0, w) {
    var k = w / PW, h = PH * k;
    ctx.save();
    ctx.translate(x0, y0); ctx.scale(k, k);
    ctx.fillStyle = C.pitch; ctx.fillRect(0, 0, PW, PH);
    for (var i = 0; i < 10; i += 2) { ctx.fillStyle = "rgba(255,255,255,0.03)"; ctx.fillRect(i * 10, 0, 10, PH); }
    ctx.strokeStyle = C.pitchLine; ctx.lineWidth = 0.35;
    ctx.strokeRect(0.6, 0.6, PW - 1.2, PH - 1.2);
    ctx.beginPath(); ctx.moveTo(50, 0.6); ctx.lineTo(50, PH - 0.6); ctx.stroke();
    ctx.beginPath(); ctx.arc(50, 32, 8.3, 0, Math.PI * 2); ctx.stroke();
    var by1 = 13, by2 = PH - 13, sby1 = 23.4, sby2 = PH - 23.4;
    [false, true].forEach(function (right) {
      var bx = right ? PW - 15.7 : 0.6, sbx = right ? PW - 5.6 : 0.6;
      ctx.strokeRect(bx, by1, 15.1, by2 - by1);
      ctx.strokeRect(sbx, sby1, 5, sby2 - sby1);
      var spot = right ? PW - 10.5 : 10.5;
      ctx.beginPath(); ctx.arc(spot, 32, 0.5, 0, Math.PI * 2); ctx.fillStyle = C.pitchLine; ctx.fill();
      var edge = right ? bx : bx + 15.1, ddx = Math.abs(edge - spot), r = 8.3;
      if (ddx < r) {
        var ddy = Math.sqrt(r * r - ddx * ddx), a = Math.atan2(ddy, edge - spot);
        ctx.beginPath();
        if (right) ctx.arc(spot, 32, r, -a, a); else ctx.arc(spot, 32, r, Math.PI - a, Math.PI + a);
        ctx.stroke();
      }
      var gx = right ? PW - 0.6 : 0.6 - 1.6;
      ctx.fillStyle = "rgba(255,255,255,0.10)"; ctx.fillRect(gx, 28.5, 1.6, 7); ctx.strokeRect(gx, 28.5, 1.6, 7);
    });
    ctx.restore();
    return h;
  }

  function render(M) {
    var fontsReady = document.fonts && document.fonts.load
      ? Promise.all([document.fonts.load('italic 800 96px "Barlow Condensed"'), document.fonts.load('700 20px "Barlow Condensed"'),
                     document.fonts.load('600 16px "Barlow"')]).catch(function () {})
      : Promise.resolve();
    return Promise.all([fontsReady, loadImg(M.crests && M.crests.home), loadImg(M.crests && M.crests.away)]).then(function (r) {
      var crestH = r[1], crestA = r[2];
      var big = document.createElement("canvas");
      big.width = W * SCALE; big.height = 2600 * SCALE;
      var ctx = big.getContext("2d");
      ctx.scale(SCALE, SCALE);

      var PAD = 36, IW = W - PAD * 2, y = PAD;
      var hatchLime = hatch(ctx, C.lime, C.limeDim), hatchGrey = hatch(ctx, "rgba(255,255,255,0.28)", "rgba(255,255,255,0.10)");

      /* ground: carbon + diagonal hatch */
      ctx.fillStyle = C.bg; ctx.fillRect(0, 0, W, 2600);
      ctx.save(); ctx.strokeStyle = "rgba(255,255,255,0.025)"; ctx.lineWidth = 2;
      for (var d = -2600; d < W + 2600; d += 14) { ctx.beginPath(); ctx.moveTo(d, 0); ctx.lineTo(d + 2600 * 0.7, 2600); ctx.stroke(); }
      ctx.restore();

      /* ---- header lower-third ---- */
      var HH = 236;
      ctx.fillStyle = C.plate; slant(ctx, PAD, y, IW, HH, 18); ctx.fill();
      ctx.fillStyle = C.lime; ctx.beginPath(); ctx.moveTo(PAD + 18, y); ctx.lineTo(PAD + 26, y); ctx.lineTo(PAD + 8, y + HH); ctx.lineTo(PAD, y + HH); ctx.closePath(); ctx.fill();
      // label tab
      var metaTxt = ("LaLiga" + (M.meta ? " · " + M.meta : "")).toUpperCase();
      ctx.font = font(700, 15, false, DISP);
      var tabW = 44;   // same per-glyph advance + tracking the label() drawer uses
      for (var ti = 0; ti < metaTxt.length; ti++) tabW += ctx.measureText(metaTxt[ti]).width + 1.8;
      ctx.fillStyle = C.lime; ctx.beginPath(); ctx.moveTo(PAD + 26, y); ctx.lineTo(PAD + 26 + tabW, y); ctx.lineTo(PAD + 26 + tabW - 12, y + 30); ctx.lineTo(PAD + 26, y + 30); ctx.closePath(); ctx.fill();
      label(ctx, metaTxt, PAD + 44, y + 21, { size: 15, color: C.bg, tracking: 1.8 });

      // teams + score
      var cy = y + 132, mid = W / 2;
      ctx.textBaseline = "middle";
      var scoreTxt = (M.home.score == null ? "–" : M.home.score) + " : " + (M.away.score == null ? "–" : M.away.score);
      ctx.font = font(800, 104, true, DISP); ctx.fillStyle = C.lime; ctx.textAlign = "center";
      ctx.fillText(scoreTxt, mid, cy);
      var scoreW = ctx.measureText(scoreTxt).width;
      var crestSize = 96, gap = 26;
      var leftEdge = mid - scoreW / 2 - gap, rightEdge = mid + scoreW / 2 + gap;
      if (crestH) ctx.drawImage(crestH, leftEdge - crestSize, cy - crestSize / 2, crestSize, crestSize);
      if (crestA) ctx.drawImage(crestA, rightEdge, cy - crestSize / 2, crestSize, crestSize);
      var nameMax = leftEdge - crestSize - gap - (PAD + 40);
      var hs = fitFont(ctx, M.home.name.toUpperCase(), 800, 46, true, nameMax);
      ctx.font = font(800, hs, true, DISP); ctx.fillStyle = C.text; ctx.textAlign = "right";
      ctx.fillText(M.home.name.toUpperCase(), leftEdge - crestSize - gap, cy);
      var as = fitFont(ctx, M.away.name.toUpperCase(), 800, 46, true, nameMax);
      ctx.font = font(800, as, true, DISP); ctx.textAlign = "left";
      ctx.fillText(M.away.name.toUpperCase(), rightEdge + crestSize + gap, cy);
      // team colour ticks under the names
      ctx.fillStyle = M.home.color || C.muted; ctx.fillRect(leftEdge - crestSize - gap - 120, cy + 34, 120, 4);
      ctx.fillStyle = M.away.color || C.muted; ctx.fillRect(rightEdge + crestSize + gap, cy + 34, 120, 4);
      // xG line
      if (M.xg) {
        ctx.font = font(600, 17, false, BODY); ctx.fillStyle = C.muted; ctx.textAlign = "center";
        var xgLine = "Expected goals (xG)  " + (+M.xg[0]).toFixed(2) + " – " + (+M.xg[1]).toFixed(2) + (M.xgNote ? "   ·   " + M.xgNote : "");
        ctx.fillText(xgLine, mid, y + HH - 28);
      }
      y += HH + 18;

      /* ---- goals: two columns ---- */
      var gh = (M.goals || []).filter(function (g) { return g.team === "home"; });
      var ga = (M.goals || []).filter(function (g) { return g.team === "away"; });
      var rows = Math.max(gh.length, ga.length);
      if (rows) {
        ctx.textBaseline = "middle";
        for (var i = 0; i < rows; i++) {
          var gy = y + 16 + i * 34;
          [gh[i], ga[i]].forEach(function (g, side) {
            if (!g) return;
            var text = (g.min + "'  " + esc(g.scorer) + (g.pen ? " (P)" : "") + (g.own ? " (OG)" : "")) + (g.assist ? "   " + esc(g.assist) : "");
            ctx.font = font(700, 19, false, DISP);
            var w = ctx.measureText(text).width + 36;
            var x = side === 0 ? mid - 24 - w : mid + 24;
            ctx.fillStyle = C.well; slant(ctx, x, gy - 15, w, 30, 5); ctx.fill();
            ctx.fillStyle = C.lime; ctx.textAlign = "left"; ctx.font = font(800, 19, false, DISP);
            var minTxt = g.min + "'";
            ctx.fillText(minTxt, x + 14, gy + 1);
            var mw = ctx.measureText(minTxt).width;
            ctx.fillStyle = C.text; ctx.font = font(700, 19, false, DISP);
            ctx.fillText(esc(g.scorer) + (g.pen ? " (P)" : "") + (g.own ? " (OG)" : ""), x + 14 + mw + 8, gy + 1);
            if (g.assist) {
              var sw = ctx.measureText(esc(g.scorer) + (g.pen ? " (P)" : "") + (g.own ? " (OG)" : "")).width;
              ctx.fillStyle = C.muted2; ctx.font = font(600, 17, false, DISP);
              ctx.fillText(esc(g.assist), x + 14 + mw + 8 + sw + 10, gy + 1);
            }
          });
        }
        y += rows * 34 + 12;
      }

      /* ---- match stats ---- */
      var stats = (M.stats || []).filter(function (s) { return s.h != null || s.a != null; });
      if (stats.length) {
        y += 8;
        ctx.fillStyle = C.lime; ctx.save(); ctx.transform(1, 0, -0.32, 1, 0, 0); ctx.fillRect(PAD + 8 + y * 0.32, y, 8, 26); ctx.restore();
        ctx.font = font(800, 28, true, DISP); ctx.fillStyle = C.text; ctx.textAlign = "left"; ctx.textBaseline = "alphabetic";
        ctx.fillText("MATCH STATS", PAD + 26, y + 24);
        y += 46;
        var colV = 92, barX = PAD + colV + 18, barW = IW - 2 * (colV + 18), rowH = 44;
        stats.forEach(function (s) {
          label(ctx, s.label, mid, y + 12, { size: 13, color: C.muted, align: "center", tracking: 1.6 });
          var trackY = y + 20, trackH = 12, hw = Math.round(barW * (s.hpct / 100)) - 2, aw = barW - hw - 4;
          ctx.fillStyle = s.hBetter ? hatchLime : hatchGrey; slant(ctx, barX, trackY, Math.max(hw, 0), trackH, 4); ctx.fill();
          ctx.fillStyle = s.aBetter ? hatchLime : hatchGrey; slant(ctx, barX + hw + 4, trackY, Math.max(aw, 0), trackH, 4); ctx.fill();
          ctx.textBaseline = "middle";
          ctx.font = font(700, 26, false, DISP);
          ctx.fillStyle = s.hBetter ? C.lime : C.muted2; ctx.textAlign = "right"; ctx.fillText(fmt(s.h, s.isXg, s.pct), PAD + colV, trackY + 6);
          ctx.fillStyle = s.aBetter ? C.lime : C.muted2; ctx.textAlign = "left"; ctx.fillText(fmt(s.a, s.isXg, s.pct), W - PAD - colV, trackY + 6);
          ctx.textBaseline = "alphabetic";
          y += rowH;
        });
        y += 6;
      }

      /* ---- shot map ---- */
      if (M.shots && M.shots.length) {
        y += 10;
        ctx.fillStyle = C.lime; ctx.save(); ctx.transform(1, 0, -0.32, 1, 0, 0); ctx.fillRect(PAD + 8 + y * 0.32, y, 8, 26); ctx.restore();
        ctx.font = font(800, 28, true, DISP); ctx.fillStyle = C.text; ctx.textAlign = "left";
        ctx.fillText("SHOT MAP", PAD + 26, y + 24);
        ctx.font = font(600, 15, false, BODY); ctx.fillStyle = C.muted2; ctx.textAlign = "right";
        ctx.fillText(M.home.name + " attack →   ·   ← " + M.away.name + " attack", W - PAD, y + 22);
        y += 42;
        var ph = drawPitch(ctx, PAD, y, IW), k = IW / PW;
        M.shots.forEach(function (s) {
          var px = PAD + tx(s.team, s.x) * k, py = y + ty(s.team, s.y) * k;
          var r = 5 + Math.sqrt(Math.max(s.xg || 0, 0.01)) * 22;
          var col = s.team === "home" ? (M.home.color || C.text) : (M.away.color || C.info);
          ctx.beginPath(); ctx.arc(px, py, r, 0, Math.PI * 2);
          if (s.goal) { ctx.fillStyle = C.lime; ctx.fill(); ctx.lineWidth = 3; ctx.strokeStyle = col; ctx.stroke(); }
          else if (s.onTarget) { ctx.fillStyle = col; ctx.globalAlpha = 0.9; ctx.fill(); ctx.globalAlpha = 1; ctx.lineWidth = 1.5; ctx.strokeStyle = "rgba(255,255,255,0.7)"; ctx.stroke(); }
          else { ctx.fillStyle = "rgba(0,0,0,0.25)"; ctx.fill(); ctx.lineWidth = 2; ctx.strokeStyle = col; ctx.globalAlpha = 0.8; ctx.stroke(); ctx.globalAlpha = 1; }
        });
        y += ph + 16;
        // legend
        var lx = PAD + 4, ly = y + 8;
        ctx.textBaseline = "middle";
        function key(kind, text) {
          ctx.beginPath(); ctx.arc(lx + 7, ly, 7, 0, Math.PI * 2);
          if (kind === "goal") { ctx.fillStyle = C.lime; ctx.fill(); }
          else if (kind === "on") { ctx.fillStyle = C.muted; ctx.fill(); }
          else { ctx.lineWidth = 2; ctx.strokeStyle = C.muted; ctx.stroke(); }
          ctx.font = font(600, 15, false, BODY); ctx.fillStyle = C.muted; ctx.textAlign = "left";
          ctx.fillText(text, lx + 22, ly + 1); lx += ctx.measureText(text).width + 48;
        }
        key("goal", "Goal"); key("on", "On target"); key("off", "Off target");
        ctx.fillStyle = C.muted2; ctx.fillText("Dot size = xG · colours = kit", lx, ly + 1);
        ctx.textBaseline = "alphabetic";
        y += 34;
      }

      /* ---- footer ---- */
      y += 10;
      ctx.fillStyle = C.line; ctx.fillRect(PAD, y, IW, 1);
      y += 26;
      label(ctx, "rshiri.github.io/XLALIGA", PAD, y, { size: 14, color: C.lime, tracking: 1.8 });
      label(ctx, (M.source || "Data: WhoScored · xG: our own shot model") + (M.season ? "   ·   La Liga " + M.season.replace("-", "/") : ""), W - PAD, y, { size: 13, color: C.muted2, align: "right", tracking: 1.2 });
      y += 22;

      /* crop to the used height */
      var out = document.createElement("canvas");
      out.width = W * SCALE; out.height = Math.round(y * SCALE);
      out.getContext("2d").drawImage(big, 0, 0, W * SCALE, y * SCALE, 0, 0, W * SCALE, y * SCALE);
      return out;
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
