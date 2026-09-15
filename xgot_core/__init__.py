"""xgot_core — placement-based expected-goals-on-target (xGOT) model.

Every real (non-penalty) shot that reached the goal frame (Goal or SavedShot —
the same "on target" definition the dashboards already use) gets a probability
that it beats the keeper, conditioned on WHERE it was placed (WhoScored's
GoalMouthY/GoalMouthZ) plus the same pre-shot context xg_core_v3 uses (distance,
angle, body part, big-chance, assist context — placement alone underfits: a
keeper's positioning interacts with shot type).

See PLAN_new_models.md (XLALIGA repo root) item 1 for the design rationale, and
this package's README.md for trained-date/corpus/metrics once trained.
"""
