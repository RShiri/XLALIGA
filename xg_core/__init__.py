"""xg_core — calibrated xG + xA models shared by XLALIGA, XWORLDCUP and BCNPROJECT.

Training side (needs numpy/pandas/sklearn, lightgbm optional):
    from xg_core import CalibratedXGModel, CalibratedXAModel
Runtime side (stdlib only — vendor features.py + score.py + xg_artifact.json,
plus xa_features.py + xa_score.py + xa_artifact.json for expected assists):
    from xg_core import XGScorer, XAScorer
"""
from .features import FEATURE_NAMES, feature_dict, iter_shots  # noqa: F401
from .score import XGScorer  # noqa: F401
from .xa_features import PASS_FEATURE_NAMES, pass_feature_dict, iter_passes  # noqa: F401
from .xa_score import XAScorer  # noqa: F401

try:  # training deps may be absent in the dashboards' build environments
    from .model import CalibratedXGModel  # noqa: F401
    from .xa_model import CalibratedXAModel  # noqa: F401
except ImportError:
    CalibratedXGModel = None
    CalibratedXAModel = None
