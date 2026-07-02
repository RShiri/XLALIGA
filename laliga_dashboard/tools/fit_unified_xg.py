#!/usr/bin/env python3
"""Fit the unified, data-driven xG model used across XLALIGA, the World Cup
dashboard and BCN — one logistic regression on ALL available shots so summed xG
tracks real goals.

Sources (all WhoScored-derived; identical feature logic). Assumes the three repos
are sibling folders (…/XLALIGA, …/XWORLDCUPTWIT, …/BCNPROJECT-main):
  - XLALIGA : laliga_dashboard/matches_detail/*.js  (shots carry x,y[WS 0-100],body,sit,big,goal)
  - WC      : XWORLDCUPTWIT/wc2026/matches/*.json    (raw WhoScored events)
  - BCN     : BCNPROJECT-main/data + root match_*_cache.json (raw WhoScored events)

Primary fit = XLALIGA + WC (comprehensive, non-overlapping competitions); BCN is a
holdout (its La Liga shots are already in XLALIGA). Prints the coefficients to bake
into xg_model.estimate_xg / renderer._estimate_xg, a per-competition logit shift so
summed xG == goals for each competition, calibration deciles, and a sanity table.
Requires numpy + scikit-learn (dev only — the runtime model is pure-python).
"""
import glob, json, math, os, re
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss

# repo layout: tools -> laliga_dashboard -> XLALIGA -> <desktop with sibling repos>
DESK = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
XLA_DETAIL = os.path.join(DESK, "XLALIGA", "laliga_dashboard", "matches_detail")
WC_MATCHES = os.path.join(DESK, "XWORLDCUPTWIT", "wc2026", "matches")
BCN_DIRS   = [os.path.join(DESK, "BCNPROJECT-main"), os.path.join(DESK, "BCNPROJECT-main", "data")]

SCALE_Y = 0.80
SHOT_TYPES = {"MissedShots", "SavedShot", "ShotOnPost", "BlockedShot", "Goal"}
FEAT_ORDER = ["dist", "angle", "header", "big", "freekick", "corner", "setpiece", "fastbreak"]


def ws_to_sb_x(ws_x):
    if ws_x <= 50:   return ws_x * (60.0 / 50.0)
    elif ws_x <= 89: return 60.0 + (ws_x - 50) * (48.0 / 39.0)
    else:            return 108.0 + (ws_x - 89) * (12.0 / 11.0)


def quals_of(ev):
    return {q.get("type", {}).get("displayName", "") for q in ev.get("qualifiers", [])}


def body_of(q):
    return ("Header" if "Head" in q else "Right Foot" if "RightFoot" in q
            else "Left Foot" if "LeftFoot" in q else "Unknown")


def sit_of(q):
    return ("Penalty" if "Penalty" in q else "Free Kick" if "DirectFreekick" in q
            else "Fast Break" if "FastBreak" in q else "Set Piece" if "SetPiece" in q
            else "Corner" if "FromCorner" in q else "Open Play")


def is_shootout(ev):
    p = ev.get("period", {})
    if isinstance(p, dict):
        return p.get("value") == 5 or "Shoot" in (p.get("displayName") or "")
    return "Shoot" in str(p or "")


def features(x_ws, y_ws, body, sit, big):
    is_pen = (sit == "Penalty")
    if is_pen:
        x_sb, y_sb = 108.0, 40.0
    else:
        x_sb, y_sb = ws_to_sb_x(x_ws), 80.0 - y_ws * SCALE_Y
    dist = max(math.hypot(120.0 - x_sb, 40.0 - y_sb), 0.5)
    a = math.hypot(120.0 - x_sb, 36.0 - y_sb)
    b = math.hypot(120.0 - x_sb, 44.0 - y_sb)
    cos_c = max(-1.0, min(1.0, (a * a + b * b - 64.0) / (2 * a * b))) if a > 0 and b > 0 else 1.0
    angle = math.acos(cos_c)
    return {"dist": dist, "angle": angle,
            "header": 1.0 if body == "Header" else 0.0, "big": 1.0 if big else 0.0,
            "freekick": 1.0 if sit == "Free Kick" else 0.0, "corner": 1.0 if sit == "Corner" else 0.0,
            "setpiece": 1.0 if sit == "Set Piece" else 0.0, "fastbreak": 1.0 if sit == "Fast Break" else 0.0}, is_pen


def shots_from_xla():
    out = []
    for f in glob.glob(os.path.join(XLA_DETAIL, "*.js")):
        if os.path.basename(f).startswith("_"):
            continue
        m = re.search(r"=\s*(\{.*\})\s*;?\s*$", open(f, encoding="utf-8").read(), re.S)
        if not m:
            continue
        for s in json.loads(m.group(1)).get("shots", []):
            out.append((s.get("x", 0), s.get("y", 0), s.get("body", "Unknown"),
                        s.get("sit", "Open Play"), bool(s.get("big")), 1 if s.get("goal") else 0))
    return out


def shots_from_raw(paths):
    out = []
    for f in paths:
        try:
            d = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        for ev in d.get("events", []):
            t = ev.get("type", {})
            if not isinstance(t, dict) or t.get("displayName") not in SHOT_TYPES or is_shootout(ev):
                continue
            q = quals_of(ev)
            if "OwnGoal" in q:
                continue
            out.append((ev.get("x", 0), ev.get("y", 0), body_of(q), sit_of(q),
                        "BigChance" in q, 1 if t.get("displayName") == "Goal" else 0))
    return out


def build(rows):
    X, y, pens, pgoals = [], [], 0, 0
    for (x, yy, body, sit, big, goal) in rows:
        f, is_pen = features(x, yy, body, sit, big)
        if is_pen:
            pens += 1; pgoals += goal; continue
        X.append([f[k] for k in FEAT_ORDER]); y.append(goal)
    return np.array(X), np.array(y), pens, pgoals


def fit_shift(base_logit, y, iters=60):
    base_logit, y, target, d = np.asarray(base_logit), np.asarray(y), np.asarray(y).sum(), 0.0
    for _ in range(iters):
        p = 1.0 / (1.0 + np.exp(-(base_logit + d)))
        h = (p * (1 - p)).sum()
        if h < 1e-9:
            break
        step = (p.sum() - target) / h
        d -= step
        if abs(step) < 1e-8:
            break
    return d


def calib(name, probs, y):
    probs, y = np.asarray(probs), np.asarray(y)
    order = np.argsort(probs)
    print(f"\n[{name}] shots={len(y)} goals={int(y.sum())} SumxG={probs.sum():.1f} "
          f"ratio={probs.sum()/max(y.sum(),1):.3f} Brier={brier_score_loss(y,probs):.4f} "
          f"logloss={log_loss(y,probs,labels=[0,1]):.4f}")
    for i in range(10):
        idx = order[i*len(order)//10:(i+1)*len(order)//10]
        if len(idx):
            print(f"   d{i+1:<2d} n={len(idx):<5d} mean_xG={probs[idx].mean():.3f} actual={y[idx].mean():.3f}")


def main():
    xla = shots_from_xla()
    wc = shots_from_raw(glob.glob(os.path.join(WC_MATCHES, "*.json")))
    bcn = []
    for dpath in BCN_DIRS:
        bcn += shots_from_raw(glob.glob(os.path.join(dpath, "match_*_cache.json")))
    print(f"raw shots: XLA={len(xla)} WC={len(wc)} BCN={len(bcn)}")
    Xx, yx, px, pgx = build(xla)
    Xw, yw, pw, pgw = build(wc)
    tp, tpg = px + pw, pgx + pgw
    print(f"non-pen: XLA={len(yx)} WC={len(yw)}  penalties={tp} conv={tpg/max(tp,1):.3f}")

    clf = LogisticRegression(C=20.0, max_iter=5000, solver="lbfgs")
    clf.fit(np.vstack([Xx, Xw]), np.concatenate([yx, yw]))
    coef, intc = clf.coef_[0], clf.intercept_[0]
    print(f"\n_INTERCEPT = {intc:.6f}")
    for k, c in zip(FEAT_ORDER, coef):
        print(f"_COEF['{k}'] = {c:.6f}")
    lg_x, lg_w = intc + Xx.dot(coef), intc + Xw.dot(coef)
    print(f"\nXLALIGA shift = {fit_shift(lg_x, yx):.6f}")
    print(f"WC      shift = {fit_shift(lg_w, yw):.6f}")
    sig = lambda z: 1.0 / (1.0 + np.exp(-z))
    calib("XLA shifted", sig(lg_x + fit_shift(lg_x, yx)), yx)
    calib("WC shifted", sig(lg_w + fit_shift(lg_w, yw)), yw)


if __name__ == "__main__":
    main()
