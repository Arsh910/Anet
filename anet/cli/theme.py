"""
theme.py — appearance presets for the ANet TUI.

A theme is a few tokens that drive the whole look:
  • accent     — the highlight color used for headers, the working animation, command
                 names, prompts, borders (everywhere the UI says "this is ANet").
  • assistant  — the border color of the assistant's reply panels.
  • banner     — the vertical gradient stops for the startup ANET block-art.

The CLI installs the active theme on its rich Console as named styles ("accent",
"assistant"), so markup like [accent]…[/accent] resolves per theme — switching is a
single console.push_theme(). Semantic colors (success=green, warning=yellow,
error=red) are intentionally NOT themed: they carry meaning, not decoration.

The chosen theme is stored PER PACK — a top-level `theme:` key in the active pack's
anet.config.yaml. So a pack carries its own colors (you can tell packs apart at a
glance), and the theme travels when the pack is shared.
"""
from __future__ import annotations

try:
    from rich.theme import Theme
except Exception:  # rich always present in practice; keep import-safe
    Theme = None  # type: ignore

# name → {accent, assistant, banner: [gradient stops]}
PRESETS: dict[str, dict] = {
    "cyan": {  # the default house look
        "accent": "cyan", "assistant": "green",
        "banner": ["#22c55e", "#10b981", "#06b6d4", "#0891b2"],
    },
    "emerald": {
        "accent": "#10b981", "assistant": "#10b981",
        "banner": ["#34d399", "#10b981", "#059669", "#047857"],
    },
    "amber": {
        "accent": "#f59e0b", "assistant": "#f59e0b",
        "banner": ["#fbbf24", "#f59e0b", "#d97706", "#b45309"],
    },
    "violet": {
        "accent": "#a855f7", "assistant": "#a855f7",
        "banner": ["#c084fc", "#a855f7", "#9333ea", "#7e22ce"],
    },
    "crimson": {
        "accent": "#ef4444", "assistant": "#ef4444",
        "banner": ["#f87171", "#ef4444", "#dc2626", "#b91c1c"],
    },
    "matrix": {
        "accent": "bright_green", "assistant": "green",
        "banner": ["#39ff14", "#22c55e", "#16a34a", "#15803d"],
    },
    "mono": {  # minimal / light-terminal friendly — no accent color
        "accent": "default", "assistant": "default",
        "banner": ["grey85", "grey66", "grey50", "grey39"],
    },
}

DEFAULT = "cyan"
NAMES = list(PRESETS.keys())

# PT-safe accent (hex) for the prompt_toolkit selection menus — so the menus pick up
# the theme color. "" = no accent (mono → plain bold highlight).
_PT_ACCENT = {
    "cyan": "#06b6d4", "emerald": "#10b981", "amber": "#f59e0b", "violet": "#a855f7",
    "crimson": "#ef4444", "matrix": "#22c55e", "mono": "",
}


def pt_accent(name: str | None = None) -> str:
    return _PT_ACCENT.get(name or active_name(), "#06b6d4")


def _config_path():
    """The active pack's anet.config.yaml (best-effort; None if unresolvable)."""
    try:
        from anet.core import paths
        return paths.config_path()
    except Exception:
        return None


def active_name() -> str:
    """Theme for the CURRENT pack — the `theme:` key in its anet.config.yaml."""
    p = _config_path()
    if p is None:
        return DEFAULT
    try:
        import yaml
        cfg = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        name = cfg.get("theme")
        return name if name in PRESETS else DEFAULT
    except Exception:
        return DEFAULT


def set_active(name: str) -> bool:
    """Persist the theme into the active pack's anet.config.yaml (comments preserved
    via ruamel). Returns False for an unknown name or if the config can't be written."""
    if name not in PRESETS:
        return False
    p = _config_path()
    if p is None or not p.exists():
        return False
    try:
        import io
        from ruamel.yaml import YAML
        y = YAML()
        y.preserve_quotes = True
        data = y.load(p.read_text(encoding="utf-8")) or {}
        data["theme"] = name
        buf = io.StringIO()
        y.dump(data, buf)
        p.write_text(buf.getvalue(), encoding="utf-8")
        return True
    except Exception:
        return False


def preset(name: str | None = None) -> dict:
    return PRESETS.get(name or active_name(), PRESETS[DEFAULT])


def rich_theme(name: str | None = None) -> "Theme | None":
    """Build the rich Theme (named styles) for a preset."""
    if Theme is None:
        return None
    p = preset(name)
    return Theme({
        "accent":     p["accent"],
        "assistant":  p["assistant"],
    }, inherit=True)


def banner_stops(name: str | None = None) -> list[str]:
    return list(preset(name)["banner"])


def swatch(name: str) -> str:
    """A rich-markup color swatch + label for menus/listings."""
    p = PRESETS.get(name, PRESETS[DEFAULT])
    return f"[{p['accent']}]●[/] {name}"
