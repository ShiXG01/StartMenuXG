import sys
from dataclasses import dataclass


IS_WINDOWS = sys.platform.startswith("win")

if IS_WINDOWS:
    import winreg


@dataclass(frozen=True)
class ThemeColors:
    name: str
    window_bg: str
    text: str
    accent_text: str
    helper_text: str
    panel_bg: str
    border: str
    selected_bg: str
    selected_text: str
    button_bg: str
    button_hover: str
    button_pressed: str
    input_bg: str
    input_text: str


LIGHT_THEME = ThemeColors(
    name="light",
    window_bg="#eef2f5",
    text="#13212e",
    accent_text="#0f766e",
    helper_text="#557084",
    panel_bg="#ffffff",
    border="#d3dce5",
    selected_bg="#d9f2ee",
    selected_text="#0f1720",
    button_bg="#ffffff",
    button_hover="#f7fafc",
    button_pressed="#ebf0f4",
    input_bg="#ffffff",
    input_text="#13212e",
)


DARK_THEME = ThemeColors(
    name="dark",
    window_bg="#171c22",
    text="#e6edf4",
    accent_text="#73d5c7",
    helper_text="#93a6ba",
    panel_bg="#1f2630",
    border="#313b48",
    selected_bg="#28433f",
    selected_text="#f2f7f6",
    button_bg="#29323c",
    button_hover="#33404d",
    button_pressed="#3b4a59",
    input_bg="#212932",
    input_text="#f4f8fc",
)


def is_system_dark_theme():
    if not IS_WINDOWS:
        return False

    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        )
        value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
        return int(value) == 0
    except OSError:
        return False


def get_system_theme():
    return DARK_THEME if is_system_dark_theme() else LIGHT_THEME


def _hex_to_rgba(color, alpha):
    color = color.lstrip("#")
    red = int(color[0:2], 16)
    green = int(color[2:4], 16)
    blue = int(color[4:6], 16)
    return "rgba({0}, {1}, {2}, {3})".format(red, green, blue, alpha)


def build_stylesheet(theme, surface_opacity):
    alpha = max(20, min(100, int(surface_opacity))) / 100.0
    launcher_panel_bg = _hex_to_rgba(theme.panel_bg, int(255 * 0.82 * alpha))
    launcher_input_bg = _hex_to_rgba(theme.input_bg, int(255 * 0.78 * alpha))
    launcher_button_bg = _hex_to_rgba(theme.button_bg, int(255 * 0.70 * alpha))
    launcher_button_hover = _hex_to_rgba(theme.button_hover, int(255 * 0.84 * alpha))
    launcher_button_pressed = _hex_to_rgba(theme.button_pressed, int(255 * 0.90 * alpha))
    launcher_selected_bg = _hex_to_rgba(theme.selected_bg, int(255 * 0.88 * alpha))
    launcher_hover_bg = _hex_to_rgba(theme.button_hover, int(255 * 0.58 * alpha))

    return """
        QMainWindow, QDialog {{
            background: {window_bg};
        }}
        QWidget {{
            color: {text};
            font-family: "Microsoft YaHei UI";
            background: transparent;
        }}
        QLabel#statusLabel {{
            color: {accent_text};
            font-size: 17px;
            font-weight: 700;
        }}
        QLabel#helperLabel {{
            color: {helper_text};
        }}
        QListWidget {{
            background: {panel_bg};
            color: {text};
            border: 1px solid {border};
            border-radius: 10px;
            padding: 6px;
            outline: none;
        }}
        QListWidget#launcherList {{
            background: {launcher_panel_bg};
            border-radius: 16px;
            padding: 8px;
        }}
        QListWidget#launcherList::item {{
            padding: 6px 4px;
            border-radius: 10px;
            border: 1px solid transparent;
            margin: 0;
            background: transparent;
        }}
        QListWidget#launcherList::item:hover {{
            background: {launcher_hover_bg};
        }}
        QListWidget::item {{
            padding: 8px 10px;
            border-radius: 6px;
        }}
        QListWidget::item:selected {{
            background: {selected_bg};
            color: {selected_text};
        }}
        QListWidget#launcherList::item:selected {{
            background: {launcher_selected_bg};
            border: 1px solid {border};
            color: {selected_text};
        }}
        QLineEdit, QSpinBox, QComboBox {{
            background: {input_bg};
            color: {input_text};
            border: 1px solid {border};
            border-radius: 10px;
            min-height: 34px;
            padding: 0 10px;
            selection-background-color: {selected_bg};
            selection-color: {selected_text};
        }}
        QLineEdit#launcherSearchInput {{
            background: {launcher_input_bg};
        }}
        QSpinBox::up-button, QSpinBox::down-button,
        QComboBox::drop-down {{
            width: 24px;
            border: none;
            background: transparent;
        }}
        QPushButton {{
            min-height: 34px;
            padding: 0 12px;
            border: 1px solid {border};
            border-radius: 8px;
            background: {button_bg};
            color: {text};
        }}
        QPushButton[surfaceRole="launcher"] {{
            background: {launcher_button_bg};
        }}
        QPushButton:hover {{
            background: {button_hover};
        }}
        QPushButton:pressed {{
            background: {button_pressed};
        }}
        QPushButton[surfaceRole="launcher"]:hover {{
            background: {launcher_button_hover};
        }}
        QPushButton[surfaceRole="launcher"]:pressed {{
            background: {launcher_button_pressed};
        }}
        QMessageBox QLabel {{
            color: {text};
        }}
    """.format(
        window_bg=theme.window_bg,
        text=theme.text,
        accent_text=theme.accent_text,
        helper_text=theme.helper_text,
        panel_bg=theme.panel_bg,
        border=theme.border,
        selected_bg=theme.selected_bg,
        selected_text=theme.selected_text,
        button_bg=theme.button_bg,
        button_hover=theme.button_hover,
        button_pressed=theme.button_pressed,
        input_bg=theme.input_bg,
        input_text=theme.input_text,
        launcher_panel_bg=launcher_panel_bg,
        launcher_input_bg=launcher_input_bg,
        launcher_button_bg=launcher_button_bg,
        launcher_button_hover=launcher_button_hover,
        launcher_button_pressed=launcher_button_pressed,
        launcher_selected_bg=launcher_selected_bg,
        launcher_hover_bg=launcher_hover_bg,
    )
