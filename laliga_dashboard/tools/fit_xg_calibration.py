#!/usr/bin/env python3
"""Fit a Platt (logistic) recalibration of the raw geometric xG model against
actual La Liga shot outcomes, so summed xG tracks goals.

Reads the shipped matches_detail/*.js (each shot has the raw model xg + goal
outcome + situation), fits  p_cal = sigmoid(A + B * logit(p_raw))  on all
non-penalty shots by maximum likelihood (pure-python IRLS, no numpy), and
prints the coefficients + a before/after calibration report.

The fitted A,B get hardcoded into xg_model.estimate_xg / renderer._estimate_xg.
Penalties are left at the fixed 0.76 and excluded from the fit.
"""
import glob, json, math, os, re

HERE = os.path.dirname(os.path.abspath(__file__))
DETAIL = os.path.join(HERE, "..", "matches_detail")

def load_shots():
    shots = []
    for f in glob.glob(os.path.join(DETAIL, "*.js")):
        if os.path.basename(f).startswith("_"):
            continue
        txt = open(f, encoding="utf-8").read()
        m = re.match(r"\s*window\.MATCH_DETAIL\s*=\s*(\{.*\});?\s*$", txt, re.S)
        if not m:
            continue
        d = json.loads(m.group(1))
        for s in d.get("shots", []):
            shots.append((float(s.get("xg") or 0.0), 1 if s.get("goal") else 0, s.get("sit")))
    return shots

def logit(p):
    p = min(max(p, 1e-4), 1 - 1e-4)
    return math.log(p / (1 - p))

def sigmoid(z):
    if z < -35: return 1e-15
    if z > 35: return 1 - 1e-15
    return 1.0 / (1.0 + math.exp(-z))

def fit(zs, ys, iters=100):
    """2-param logistic regression (intercept A, slope B) via Newton-Raphson."""
    A, B = 0.0, 1.0
    n = len(zs)
    for _ in range(iters):
        g0 = g1 = 0.0
        h00 = h01 = h11 = 0.0
        for z, y in zip(zs, ys):
            p = sigmoid(A + B * z)
            w = p * (1 - p)
            r = p - y
            g0 += r;      g1 += r * z
            h00 += w;     h01 += w * z;   h11 += w * z * z
        # add tiny ridge for stability
        h00 += 1e-9; h11 += 1e-9
        det = h00 * h11 - h01 * h01
        if abs(det) < 1e-12:
            break
        dA = (h11 * g0 - h01 * g1) / det
        dB = (-h01 * g0 + h00 * g1) / det
        A -= dA; B -= dB
        if abs(dA) < 1e-10 and abs(dB) < 1e-10:
            break
    return A, B

def report(shots, A, B):
    # totals including penalties (penalty stays 0.76)
    tot_goals = sum(y for _, y, _ in shots)
    raw = sum(xg for xg, _, _ in shots)
    cal = 0.0
    for xg, y, sit in shots:
        if sit == "Penalty":
            cal += 0.76
        else:
            cal += sigmoid(A + B * logit(xg))
    print(f"shots={len(shots)}  goals={tot_goals}")
    print(f"RAW   total xG = {raw:8.1f}   (xG/goals = {raw/tot_goals:.3f})")
    print(f"CAL   total xG = {cal:8.1f}   (xG/goals = {cal/tot_goals:.3f})")
    # reliability by decile of raw xg (non-pen)
    nps = [(xg, y) for xg, y, sit in shots if sit != "Penalty"]
    nps.sort()
    print("\n decile |  n  | mean_raw | mean_cal | actual")
    B_ = 10
    for i in range(B_):
        lo = i * len(nps) // B_; hi = (i + 1) * len(nps) // B_
        chunk = nps[lo:hi]
        if not chunk: continue
        mr = sum(x for x, _ in chunk) / len(chunk)
        mc = sum(sigmoid(A + B * logit(x)) for x, _ in chunk) / len(chunk)
        ac = sum(y for _, y in chunk) / len(chunk)
        print(f"  {i+1:2d}    |{len(chunk):4d} |  {mr:.3f}  |  {mc:.3f}  | {ac:.3f}")

def main():
    shots = load_shots()
    npen = [(xg, y) for xg, y, sit in shots if sit != "Penalty"]
    zs = [logit(xg) for xg, _ in npen]
    ys = [y for _, y in npen]
    A, B = fit(zs, ys)
    print(f"=== Fitted Platt recalibration: p_cal = sigmoid(A + B*logit(p_raw)) ===")
    print(f"_CAL_A = {A:.6f}")
    print(f"_CAL_B = {B:.6f}\n")
    report(shots, A, B)

if __name__ == "__main__":
    main()
