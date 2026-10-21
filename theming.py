"""Applies VisualAssault (https://github.com/gerp93/VisualAssault) color themes to
the Tkinter/ttk window. If the theme package isn't installed (running from
source without requirements.txt), THEMES is empty and the window keeps the
native look, so the script and window still work.
"""
import tkinter as tk
from tkinter import ttk

try:
    from visual_assault_tkinter import THEMES
except ImportError:  # optional when running from source
    THEMES = {}

THEME_NAMES = {theme_id: data["name"] for theme_id, data in THEMES.items()}
DEFAULT_LABEL = "System Default"
DEFAULT_THEME_ID = "blue_oval" if "blue_oval" in THEMES else None

_defaults = {"ttk_theme": None, "root_background": None, "widgets": []}
_current = {"theme": None}


def _walk(widget):
    yield widget
    for child in widget.winfo_children():
        yield from _walk(child)


def capture_defaults(root: tk.Misc) -> None:
    """Snapshot the native look before any theme is applied, so 'System Default'
    can restore it exactly. Call once, after the main UI has been built."""
    style = ttk.Style(root)
    _defaults["ttk_theme"] = style.theme_use()
    _defaults["root_background"] = root.cget("background")

    widgets = []
    for widget in _walk(root):
        if isinstance(widget, tk.Text):
            widgets.append((widget, {
                "background": widget.cget("background"),
                "foreground": widget.cget("foreground"),
                "insertbackground": widget.cget("insertbackground"),
            }))
    _defaults["widgets"] = widgets


def background() -> str:
    """The window background right now (for dialogs created later)."""
    if _current["theme"]:
        return THEMES[_current["theme"]]["background"]
    return _defaults["root_background"] or "SystemButtonFace"


def apply_theme(root: tk.Misc, theme_id) -> None:
    """Apply a VisualAssault theme by id, or pass None to restore the native look."""
    style = ttk.Style(root)

    if theme_id is None:
        _current["theme"] = None
        style.theme_use(_defaults["ttk_theme"])
        root.configure(background=_defaults["root_background"])
        for widget, values in _defaults["widgets"]:
            widget.configure(**values)
        return

    theme = THEMES[theme_id]
    _current["theme"] = theme_id
    style.theme_use("clam")

    style.configure("TFrame", background=theme["background"])
    style.configure("TLabelframe", background=theme["background"], bordercolor=theme["border"])
    style.configure("TLabelframe.Label", background=theme["background"], foreground=theme["foreground"])
    style.configure("TLabel", background=theme["background"], foreground=theme["foreground"])
    style.configure(
        "TButton",
        background=theme["buttonBackground"],
        foreground=theme["foreground"],
        bordercolor=theme["border"],
        focuscolor=theme["accentBlue"],
    )
    style.map(
        "TButton",
        background=[("disabled", theme["surface"]), ("active", theme["buttonHover"])],
        foreground=[("disabled", theme["textMuted"])],
    )
    style.configure(
        "TEntry",
        fieldbackground=theme["surface"],
        foreground=theme["foreground"],
        bordercolor=theme["border"],
        insertcolor=theme["foreground"],
    )
    style.configure(
        "TCombobox",
        fieldbackground=theme["surface"],
        background=theme["buttonBackground"],
        foreground=theme["foreground"],
        arrowcolor=theme["foreground"],
    )
    style.map(
        "TCombobox",
        fieldbackground=[("readonly", theme["surface"])],
        foreground=[("readonly", theme["foreground"])],
    )
    style.configure(
        "TCheckbutton",
        background=theme["background"],
        foreground=theme["foreground"],
        indicatorbackground=theme["surface"],
        indicatorforeground=theme["foreground"],
    )
    style.map(
        "TCheckbutton",
        background=[("active", theme["backgroundHover"])],
        foreground=[("disabled", theme["textMuted"])],
        indicatorbackground=[("disabled", theme["background"])],
    )
    style.configure(
        "TScrollbar",
        background=theme["buttonBackground"],
        troughcolor=theme["surface"],
        arrowcolor=theme["foreground"],
        bordercolor=theme["border"],
    )

    root.configure(background=theme["background"])

    for widget, _ in _defaults["widgets"]:
        if isinstance(widget, tk.Text):
            widget.configure(background=theme["surface"], foreground=theme["foreground"], insertbackground=theme["foreground"])
