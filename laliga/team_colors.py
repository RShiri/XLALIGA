"""
La Liga 2025/26 — primary/secondary colours for all 20 clubs, for the PNG renderer.
Sourced from each club's official kit/crest palette. ``get_team_colors`` matches by
exact name, then case-insensitively, then falls back to a neutral grey.

``WC2026_TEAM_COLORS`` is kept as an alias so the ported ``renderer.py`` imports unchanged.
"""

LALIGA_TEAM_COLORS: dict[str, dict[str, str]] = {
    "Athletic Club":     {"primary": "#EE2523", "secondary": "#FFFFFF"},
    "Atletico Madrid":   {"primary": "#CB3524", "secondary": "#262E62"},
    "Barcelona":         {"primary": "#004D98", "secondary": "#A50044"},
    "Celta Vigo":        {"primary": "#8AC3EE", "secondary": "#E4022E"},
    "Deportivo Alaves":  {"primary": "#0761AF", "secondary": "#FFFFFF"},
    "Elche":             {"primary": "#046A38", "secondary": "#FFFFFF"},
    "Espanyol":          {"primary": "#0072CE", "secondary": "#FFFFFF"},
    "Getafe":            {"primary": "#005999", "secondary": "#FFFFFF"},
    "Girona":            {"primary": "#C40018", "secondary": "#FFFFFF"},
    "Levante":           {"primary": "#0055A5", "secondary": "#A50044"},
    "Mallorca":          {"primary": "#E20613", "secondary": "#000000"},
    "Osasuna":           {"primary": "#0A346F", "secondary": "#D91A21"},
    "Rayo Vallecano":    {"primary": "#E53027", "secondary": "#FFFFFF"},
    "Real Betis":        {"primary": "#00954C", "secondary": "#FFFFFF"},
    "Real Madrid":       {"primary": "#00529F", "secondary": "#FEBE10"},
    "Real Oviedo":       {"primary": "#005BAC", "secondary": "#FFFFFF"},
    "Real Sociedad":     {"primary": "#0067B1", "secondary": "#FFFFFF"},
    "Sevilla":           {"primary": "#D81E05", "secondary": "#FFFFFF"},
    "Valencia":          {"primary": "#F18E00", "secondary": "#000000"},
    "Villarreal":        {"primary": "#FDE607", "secondary": "#005187"},
    # common alias spellings from FotMob / WhoScored / Understat
    "Atlético Madrid":   {"primary": "#CB3524", "secondary": "#262E62"},
    "Alaves":            {"primary": "#0761AF", "secondary": "#FFFFFF"},
    "Elche CF":          {"primary": "#046A38", "secondary": "#FFFFFF"},
    "Real Betis Balompié": {"primary": "#00954C", "secondary": "#FFFFFF"},
}

# Drop-in alias so the ported renderer.py (which imports WC2026_TEAM_COLORS) works unchanged.
WC2026_TEAM_COLORS = LALIGA_TEAM_COLORS


def get_team_colors(team_name: str, fallback_home: bool = True) -> dict[str, str]:
    """Return {'primary': hex, 'secondary': hex} for a team."""
    name_clean = (team_name or "").strip()
    if name_clean in LALIGA_TEAM_COLORS:
        return LALIGA_TEAM_COLORS[name_clean]
    lower = name_clean.lower()
    for k, v in LALIGA_TEAM_COLORS.items():
        if k.lower() == lower:
            return v
    return {"primary": "#6b7a99" if fallback_home else "#4a5870", "secondary": "#FFFFFF"}
