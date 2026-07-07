# -*- coding: utf-8 -*-
"""xg_core_v3 — deployable 23-feature xG runtime (stdlib-only; lightgbm optional).

    from xg_core_v3 import XGScorer
    scorer = XGScorer()                       # loads xg_artifact.json (23-feature)
    for event_id, xg in scorer.iter_match_xg(match_data, league="EPL"):
        ...
"""
from .features import (FEATURE_NAMES, feature_dict, shot_feature_dict,  # noqa: F401
                       iter_shots)
from .score import XGScorer  # noqa: F401
