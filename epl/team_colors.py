"""
Premier League 2025/26 — primary/secondary colours for all 20 clubs, for the PNG renderer.
Sourced from each club's official kit/crest palette. ``get_team_colors`` matches by
exact name, then case-insensitively, then falls back to a neutral grey.

``WC2026_TEAM_COLORS`` is kept as an alias so the ported ``renderer.py`` imports unchanged.
"""

EPL_TEAM_COLORS: dict[str, dict[str, str]] = {
    "Arsenal":                {"primary": "#EF0107", "secondary": "#FFFFFF"},
    "Aston Villa":            {"primary": "#95BFE5", "secondary": "#670E36"},
    "AFC Bournemouth":        {"primary": "#DA291C", "secondary": "#000000"},
    "Brentford":              {"primary": "#E30613", "secondary": "#FBB800"},
    "Brighton & Hove Albion": {"primary": "#0057B8", "secondary": "#FFCD00"},
    "Burnley":                {"primary": "#6C1D45", "secondary": "#99D6EA"},
    "Chelsea":                {"primary": "#034694", "secondary": "#FFFFFF"},
    "Crystal Palace":         {"primary": "#1B458F", "secondary": "#C4122E"},
    "Everton":                {"primary": "#003399", "secondary": "#FFFFFF"},
    "Fulham":                 {"primary": "#000000", "secondary": "#FFFFFF"},
    "Leeds United":           {"primary": "#FFCD00", "secondary": "#1D428A"},
    "Liverpool":              {"primary": "#C8102E", "secondary": "#00B2A9"},
    "Manchester City":        {"primary": "#6CABDD", "secondary": "#1C2C5B"},
    "Manchester United":      {"primary": "#DA291C", "secondary": "#FBE122"},
    "Newcastle United":       {"primary": "#241F20", "secondary": "#FFFFFF"},
    "Nottingham Forest":      {"primary": "#DD0000", "secondary": "#FFFFFF"},
    "Sunderland":             {"primary": "#EB172B", "secondary": "#FFFFFF"},
    "Tottenham Hotspur":      {"primary": "#132257", "secondary": "#FFFFFF"},
    "West Ham United":        {"primary": "#7A263A", "secondary": "#1BB1E7"},
    "Wolverhampton Wanderers": {"primary": "#FDB913", "secondary": "#231F20"},
    # common alias spellings from FotMob / WhoScored / Understat
    "Bournemouth":            {"primary": "#DA291C", "secondary": "#000000"},
    "Brighton":               {"primary": "#0057B8", "secondary": "#FFCD00"},
    "Man City":               {"primary": "#6CABDD", "secondary": "#1C2C5B"},
    "Man Utd":                {"primary": "#DA291C", "secondary": "#FBE122"},
    "Manchester Utd":         {"primary": "#DA291C", "secondary": "#FBE122"},
    "Newcastle":              {"primary": "#241F20", "secondary": "#FFFFFF"},
    "Nott'm Forest":          {"primary": "#DD0000", "secondary": "#FFFFFF"},
    "Spurs":                  {"primary": "#132257", "secondary": "#FFFFFF"},
    "Tottenham":              {"primary": "#132257", "secondary": "#FFFFFF"},
    "West Ham":               {"primary": "#7A263A", "secondary": "#1BB1E7"},
    "Wolves":                 {"primary": "#FDB913", "secondary": "#231F20"},
    "Leeds":                  {"primary": "#FFCD00", "secondary": "#1D428A"},
}

# Drop-in alias so the ported renderer.py (which imports WC2026_TEAM_COLORS) works unchanged.
WC2026_TEAM_COLORS = EPL_TEAM_COLORS


def get_team_colors(team_name: str, fallback_home: bool = True) -> dict[str, str]:
    """Return {'primary': hex, 'secondary': hex} for a team."""
    name_clean = (team_name or "").strip()
    if name_clean in EPL_TEAM_COLORS:
        return EPL_TEAM_COLORS[name_clean]
    lower = name_clean.lower()
    for k, v in EPL_TEAM_COLORS.items():
        if k.lower() == lower:
            return v
    return {"primary": "#6b7a99" if fallback_home else "#4a5870", "secondary": "#FFFFFF"}
