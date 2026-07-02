#!/usr/bin/env python3
"""Overfitting + benchmark validation of the unified xG model.

1. 5-fold CV (train vs held-out metrics) on pooled + per-competition shots
2. Temporal split: La Liga first 70% of season -> last 30% (out-of-time)
3. Cross-competition transfer: fit on La Liga only -> test on World Cup
4. Baselines: constant rate, distance-only LR, old geometric heuristic,
   old geometric + Platt, previous WC-only LR (on WC shots with full qualifiers)
5. External: our match xG vs FotMob official xG (WC) and vs Understat (BCN)
6. xA sanity: player-level correlation xa <-> actual assists
"""
import glob, json, math, os, re, sys
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score
from sklearn.model_selection import StratifiedKFold

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
def _find_desk(start):
    d = start
    for _ in range(8):
        if all(os.path.isdir(os.path.join(d, r)) for r in ("XLALIGA", "XWORLDCUPTWIT")):
            return d
        nd = os.path.dirname(d)
        if nd == d:
            break
        d = nd
    return start


DESK = _find_desk(os.path.dirname(os.path.abspath(__file__)))
XLA_DETAIL = os.path.join(DESK, "XLALIGA", "laliga_dashboard", "matches_detail")
WC_MATCHES = os.path.join(DESK, "XWORLDCUPTWIT", "wc2026", "matches")
WC_DETAIL  = os.path.join(DESK, "XWORLDCUPTWIT", "wc2026_dashboard", "matches_detail")
BCN_DIRS   = [os.path.join(DESK, "BCNPROJECT-main", "assets", "data")]

SCALE_Y = 0.80
SHOT_TYPES = {"MissedShots", "SavedShot", "ShotOnPost", "BlockedShot", "Goal"}
FEATS = ["dist", "angle", "header", "big", "freekick", "corner", "setpiece", "fastbreak"]


def ws_to_sb_x(x):
    if x <= 50:   return x * 1.2
    elif x <= 89: return 60.0 + (x - 50) * (48.0 / 39.0)
    else:         return 108.0 + (x - 89) * (12.0 / 11.0)


def geom(x_ws, y_ws):
    x_sb, y_sb = ws_to_sb_x(x_ws), 80.0 - y_ws * SCALE_Y
    dist = max(math.hypot(120.0 - x_sb, 40.0 - y_sb), 0.5)
    a = math.hypot(120.0 - x_sb, 36.0 - y_sb)
    b = math.hypot(120.0 - x_sb, 44.0 - y_sb)
    c = max(-1.0, min(1.0, (a * a + b * b - 64.0) / (2 * a * b))) if a > 0 and b > 0 else 1.0
    return dist, math.acos(c), x_sb, y_sb


def featrow(x_ws, y_ws, body, sit, big):
    dist, ang, _, _ = geom(x_ws, y_ws)
    return [dist, ang, 1.0 if body == "Header" else 0.0, 1.0 if big else 0.0,
            1.0 if sit == "Free Kick" else 0.0, 1.0 if sit == "Corner" else 0.0,
            1.0 if sit == "Set Piece" else 0.0, 1.0 if sit == "Fast Break" else 0.0]


def old_heuristic(x_ws, y_ws, body, big):
    """The original geometric xG (pre-calibration)."""
    dist, _, x_sb, y_sb = geom(x_ws, y_ws)
    angle = math.atan2(4.0, dist)
    xg = (angle / (math.pi / 2)) * (1 / (1 + dist / 30))
    if body == "Header":
        xg *= 0.4
    if big:
        xg = min(0.65, max(0.35, xg * 3.5))
    if dist > 18:
        xg *= (18 / dist) ** 2
    return min(max(xg, 0.01), 0.95)


def platt(p, A=-0.783772, B=0.755401):
    p = min(max(p, 1e-4), 1 - 1e-4)
    return 1.0 / (1.0 + math.exp(-(A + B * math.log(p / (1 - p)))))


def wc_old_lr(x_ws, y_ws, body, sit, big, one):
    """The previous WC-only logistic model (needs OneOnOne qualifier)."""
    dist, ang, _, _ = geom(x_ws, y_ws)
    z = (-2.9528 - 0.0179 * dist + 1.4460 * ang
         - 0.5954 * (body == "Header") + 1.7581 * big + 0.1043 * one
         + 0.9319 * (sit == "Free Kick") - 0.5847 * (sit in ("Corner", "Set Piece"))
         + 0.8012 * (sit == "Fast Break"))
    return 1.0 / (1.0 + math.exp(-max(-35, min(35, z))))


def quals_of(ev):
    return {q.get("type", {}).get("displayName", "") for q in ev.get("qualifiers", [])}


def sit_of(q):
    return ("Penalty" if "Penalty" in q else "Free Kick" if "DirectFreekick" in q
            else "Fast Break" if "FastBreak" in q else "Set Piece" if "SetPiece" in q
            else "Corner" if "FromCorner" in q else "Open Play")


def body_of(q):
    return ("Header" if "Head" in q else "Right Foot" if "RightFoot" in q
            else "Left Foot" if "LeftFoot" in q else "Unknown")


def is_shootout(ev):
    p = ev.get("period", {})
    return isinstance(p, dict) and (p.get("value") == 5 or "Shoot" in (p.get("displayName") or ""))


def load_xla():
    """(X, y, dates) non-penalty shots from XLALIGA matches_detail (with match date)."""
    X, y, dates = [], [], []
    for f in glob.glob(os.path.join(XLA_DETAIL, "*.js")):
        if os.path.basename(f).startswith("_"):
            continue
        m = re.search(r"=\s*(\{.*\})\s*;?\s*$", open(f, encoding="utf-8").read(), re.S)
        if not m:
            continue
        d = json.loads(m.group(1))
        for s in d.get("shots", []):
            if (s.get("sit") or "") == "Penalty":
                continue
            X.append(featrow(s.get("x", 0), s.get("y", 0), s.get("body", "Unknown"),
                             s.get("sit", "Open Play"), bool(s.get("big"))))
            y.append(1 if s.get("goal") else 0)
            dates.append(d.get("date", ""))
    return np.array(X), np.array(y), np.array(dates)


def load_wc():
    """(X, y, one_on_one, raws) non-pen shots from WC raw events (full qualifiers)."""
    X, y, ones, raws = [], [], [], []
    for f in glob.glob(os.path.join(WC_MATCHES, "*.json")):
        if "cache" in f:
            continue
        try:
            d = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        for ev in d.get("events", []):
            t = ev.get("type", {})
            if not isinstance(t, dict) or t.get("displayName") not in SHOT_TYPES or is_shootout(ev):
                continue
            q = quals_of(ev)
            if "OwnGoal" in q or "Penalty" in q:
                continue
            body, sit, big, one = body_of(q), sit_of(q), "BigChance" in q, "OneOnOne" in q
            X.append(featrow(ev.get("x", 0), ev.get("y", 0), body, sit, big))
            y.append(1 if t.get("displayName") == "Goal" else 0)
            ones.append(1.0 if one else 0.0)
            raws.append((ev.get("x", 0), ev.get("y", 0), body, sit, big, one))
    return np.array(X), np.array(y), np.array(ones), raws


def fit_lr(X, y):
    clf = LogisticRegression(C=20.0, max_iter=5000, solver="lbfgs")
    clf.fit(X, y)
    return clf


def shift_to(base_logit, y):
    """1-param intercept shift so sum p == sum y (the deployment calibration)."""
    d = 0.0
    for _ in range(60):
        p = 1 / (1 + np.exp(-(base_logit + d)))
        h = (p * (1 - p)).sum()
        if h < 1e-9:
            break
        step = (p.sum() - y.sum()) / h
        d -= step
        if abs(step) < 1e-8:
            break
    return d


def report(tag, p, y):
    p = np.clip(np.asarray(p, dtype=float), 1e-6, 1 - 1e-6)
    return "%-34s Brier %.4f  logloss %.4f  AUC %.3f  ΣxG/goals %.3f" % (
        tag, brier_score_loss(y, p), log_loss(y, p), roc_auc_score(y, p),
        p.sum() / max(y.sum(), 1))


def main():
    Xx, yx, dx = load_xla()
    Xw, yw, onew, raww = load_wc()
    Xall = np.vstack([Xx, Xw]); yall = np.concatenate([yx, yw])
    print(f"shots: XLA={len(yx)} (goals {yx.sum()})  WC={len(yw)} (goals {yw.sum()})\n")

    # ---------- 1. 5-fold CV: train vs held-out ----------
    print("== 1) 5-fold cross-validation (pooled XLA+WC) ==")
    skf = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)
    tr_b, te_b, tr_l, te_l, te_a = [], [], [], [], []
    for tr, te in skf.split(Xall, yall):
        clf = fit_lr(Xall[tr], yall[tr])
        ptr = clf.predict_proba(Xall[tr])[:, 1]
        pte = clf.predict_proba(Xall[te])[:, 1]
        tr_b.append(brier_score_loss(yall[tr], ptr)); te_b.append(brier_score_loss(yall[te], pte))
        tr_l.append(log_loss(yall[tr], ptr));         te_l.append(log_loss(yall[te], pte))
        te_a.append(roc_auc_score(yall[te], pte))
    print(f"  train Brier {np.mean(tr_b):.4f} ± {np.std(tr_b):.4f}   heldout Brier {np.mean(te_b):.4f} ± {np.std(te_b):.4f}")
    print(f"  train logloss {np.mean(tr_l):.4f} ± {np.std(tr_l):.4f}  heldout logloss {np.mean(te_l):.4f} ± {np.std(te_l):.4f}")
    print(f"  heldout AUC {np.mean(te_a):.3f} ± {np.std(te_a):.3f}")
    gap_b = np.mean(te_b) - np.mean(tr_b)
    print(f"  generalization gap (Brier): {gap_b:+.4f}  -> {'no overfit' if gap_b < 0.002 else 'CHECK'}\n")

    # ---------- 2. temporal split (La Liga) ----------
    print("== 2) Out-of-time: La Liga first 70% of season -> last 30% ==")
    order = np.argsort(dx)
    cut = int(len(order) * 0.7)
    tr, te = order[:cut], order[cut:]
    clf = fit_lr(Xx[tr], yx[tr])
    print("  " + report("train (Aug–Mar)", clf.predict_proba(Xx[tr])[:, 1], yx[tr]))
    print("  " + report("TEST  (Apr–May, unseen)", clf.predict_proba(Xx[te])[:, 1], yx[te]) + "\n")

    # ---------- 3. cross-competition transfer ----------
    print("== 3) Transfer: fit on La Liga ONLY -> test on World Cup ==")
    clf = fit_lr(Xx, yx)
    lg = clf.intercept_[0] + Xw.dot(clf.coef_[0])
    p_raw = 1 / (1 + np.exp(-lg))
    print("  " + report("WC, no adjustment", p_raw, yw))
    d = shift_to(lg, yw)
    print("  " + report(f"WC, +1-param shift ({d:+.3f})", 1 / (1 + np.exp(-(lg + d))), yw))
    print("  (the shift is the deployed per-competition calibration)\n")

    # ---------- 4. baselines on the SAME pooled data ----------
    print("== 4) Model comparison (pooled XLA+WC non-pen shots, in-sample for the")
    print("      fixed/heuristic models, 5-fold held-out for fitted ones) ==")
    base = np.full(len(yall), yall.mean())
    print("  " + report("constant (base rate)", base, yall))
    # distance-only LR (held-out via CV)
    p_cv = np.zeros(len(yall))
    Xd = Xall[:, [0]]
    for tr, te in skf.split(Xd, yall):
        c = fit_lr(Xd[tr], yall[tr]); p_cv[te] = c.predict_proba(Xd[te])[:, 1]
    print("  " + report("distance-only LR (heldout)", p_cv, yall))
    # old geometric heuristic + platt (fixed formulas -> no fitting)
    # rebuild WS coords for XLA shots: invert not possible from features; recompute directly
    p_geo, p_pla, ygeo = [], [], []
    for f in glob.glob(os.path.join(XLA_DETAIL, "*.js")):
        if os.path.basename(f).startswith("_"):
            continue
        m = re.search(r"=\s*(\{.*\})\s*;?\s*$", open(f, encoding="utf-8").read(), re.S)
        if not m:
            continue
        for s in json.loads(m.group(1)).get("shots", []):
            if (s.get("sit") or "") == "Penalty":
                continue
            g = old_heuristic(s.get("x", 0), s.get("y", 0), s.get("body", "Unknown"), bool(s.get("big")))
            p_geo.append(g); p_pla.append(platt(g)); ygeo.append(1 if s.get("goal") else 0)
    for (x, yy, body, sit, big, one) in raww:
        g = old_heuristic(x, yy, body, big)
        p_geo.append(g); p_pla.append(platt(g)); ygeo.append(0)  # y appended below correctly
    # fix: y for WC raws
    ygeo = np.concatenate([np.array(ygeo[:len(yx)]), yw])
    print("  " + report("old geometric heuristic", np.array(p_geo), ygeo))
    print("  " + report("old geometric + Platt", np.array(p_pla), ygeo))
    # previous WC-only LR on WC shots (fixed coefficients, needs OneOnOne)
    p_old = np.array([wc_old_lr(x, yy, b, s, bg, o) for (x, yy, b, s, bg, o) in raww])
    print("  " + report("previous WC-only LR (WC shots)", p_old, yw))
    # our unified model, held-out predictions (from CV)
    p_uni = np.zeros(len(yall))
    for tr, te in skf.split(Xall, yall):
        c = fit_lr(Xall[tr], yall[tr]); p_uni[te] = c.predict_proba(Xall[te])[:, 1]
    print("  " + report("UNIFIED model (heldout CV)", p_uni, yall) + "\n")

    # ---------- 5. external: FotMob + Understat ----------
    print("== 5) External benchmark: our match xG vs professional models ==")
    ours, fots = [], []
    for f in glob.glob(os.path.join(WC_DETAIL, "*.js")):
        if os.path.basename(f).startswith("_"):
            continue
        m = re.search(r"=\s*(\{.*\})\s*;?\s*$", open(f, encoding="utf-8").read(), re.S)
        if not m:
            continue
        d = json.loads(m.group(1))
        fx = d.get("fotXg")
        if isinstance(fx, dict):
            fh, fa = fx.get("home"), fx.get("away")
        elif isinstance(fx, (list, tuple)) and len(fx) == 2:
            fh, fa = fx[0], fx[1]
        else:
            continue
        if fh is None or fa is None:
            continue
        oh = sum(s["xg"] for s in d["shots"] if s["team"] == "home")
        oa = sum(s["xg"] for s in d["shots"] if s["team"] == "away")
        ours += [oh, oa]; fots += [float(fh), float(fa)]
    if ours:
        ours, fots = np.array(ours), np.array(fots)
        r = np.corrcoef(ours, fots)[0, 1]
        mae = np.abs(ours - fots).mean()
        print(f"  FotMob (WC, n={len(ours)} team-matches): corr r={r:.3f}  MAE={mae:.2f} xG  "
              f"mean ours {ours.mean():.2f} vs FotMob {fots.mean():.2f}")
    uo, uu = [], []
    for dpath in BCN_DIRS:
        for f in glob.glob(os.path.join(dpath, "match_*_cache.json")):
            try:
                d = json.load(open(f, encoding="utf-8"))
            except Exception:
                continue
            us = (d.get("understat") or {}).get("xG") or {}
            if not us.get("h") and not us.get("a"):
                continue
            for side_key, us_key in (("home", "h"), ("away", "a")):
                tid = d.get(side_key, {}).get("teamId")
                tot = 0.0
                for ev in d.get("events", []):
                    t = ev.get("type", {})
                    if not isinstance(t, dict) or t.get("displayName") not in SHOT_TYPES:
                        continue
                    if ev.get("teamId") != tid or is_shootout(ev):
                        continue
                    q = quals_of(ev)
                    if "OwnGoal" in q:
                        continue
                    if "Penalty" in q:
                        tot += 0.76
                        continue
                    fr = featrow(ev.get("x", 0), ev.get("y", 0), body_of(q), sit_of(q), "BigChance" in q)
                    z = -3.379503 - 0.044712 + np.dot([-0.004175, 1.421131, -0.580616, 1.891534,
                                                       0.278088, -0.303916, -0.345961, 0.455797], fr)
                    tot += 1 / (1 + math.exp(-z))
                uo.append(tot); uu.append(float(us.get(us_key, 0) or 0))
    if uo:
        uo, uu = np.array(uo), np.array(uu)
        r = np.corrcoef(uo, uu)[0, 1]
        print(f"  Understat (BCN, n={len(uo)} team-matches): corr r={r:.3f}  MAE={np.abs(uo-uu).mean():.2f} xG  "
              f"mean ours {uo.mean():.2f} vs Understat {uu.mean():.2f}")
    print()

    # ---------- 6. xA sanity ----------
    print("== 6) xA sanity: player-level xa vs actual assists (XLALIGA, 600 players) ==")
    m = re.search(r"=\s*(\{.*\})\s*;?\s*$",
                  open(os.path.join(DESK, "XLALIGA", "laliga_dashboard", "players.js"), encoding="utf-8").read(), re.S)
    pl = json.loads(m.group(1))["2025-26"]
    xa = np.array([p.get("xa", 0) for p in pl]); aa = np.array([p.get("a", 0) for p in pl])
    print(f"  corr(xa, assists) r={np.corrcoef(xa, aa)[0,1]:.3f}   Σxa={xa.sum():.0f} vs Σassists={aa.sum():.0f}")


if __name__ == "__main__":
    main()
