import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable

from start_menu.hotkey import DEFAULT_HOTKEY_TEXT, normalize_hotkey_text


def _project_root():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _clamp(value, minimum, maximum):
    return max(minimum, min(maximum, value))


def _normalize_unique_text_list(values):
    if not isinstance(values, list):
        return []
    normalized_values = []
    seen = set()
    for value in values:
        text = str(value or "").strip()
        folded = text.casefold()
        if not text or folded in seen:
            continue
        seen.add(folded)
        normalized_values.append(text)
    return normalized_values


@dataclass
class AppConfig:
    menu_dir: str
    background_image_path: str = ""
    global_hotkey: str = DEFAULT_HOTKEY_TEXT
    surface_opacity: int = 72
    font_size: int = 10
    window_width: int = 520
    window_height: int = 620
    icon_size: int = 40
    edge_margin: int = 14
    tile_order: list = field(default_factory=list)

    def normalized(self):
        self.menu_dir = str(self.menu_dir or "").strip()
        self.background_image_path = str(self.background_image_path or "").strip()
        self.global_hotkey = normalize_hotkey_text(self.global_hotkey)
        self.surface_opacity = _clamp(int(self.surface_opacity), 20, 100)
        self.font_size = _clamp(int(self.font_size), 8, 28)
        self.window_width = _clamp(int(self.window_width), 360, 1200)
        self.window_height = _clamp(int(self.window_height), 320, 1200)
        self.icon_size = _clamp(int(self.icon_size), 32, 96)
        self.edge_margin = _clamp(int(self.edge_margin), 0, 48)
        self.tile_order = _normalize_unique_text_list(self.tile_order)
        return self


@dataclass
class MenuLayoutGroup:
    id: str
    name: str
    collapsed: bool = False
    item_keys: list = field(default_factory=list)

    def normalized(self):
        self.id = str(self.id or "").strip()
        self.name = str(self.name or "").strip() or "新分类"
        self.collapsed = bool(self.collapsed)
        self.item_keys = _normalize_unique_text_list(self.item_keys)
        return self


@dataclass
class MenuLayout:
    root_item_keys: list = field(default_factory=list)
    groups: list = field(default_factory=list)

    def normalized(self):
        self.root_item_keys = _normalize_unique_text_list(self.root_item_keys)
        normalized_groups = []
        seen_group_ids = set()

        for value in self.groups:
            if isinstance(value, MenuLayoutGroup):
                group = value
            elif isinstance(value, dict):
                group = MenuLayoutGroup(
                    id=value.get("id", ""),
                    name=value.get("name", ""),
                    collapsed=value.get("collapsed", False),
                    item_keys=value.get("item_keys", []),
                )
            else:
                continue

            group = group.normalized()
            folded = group.id.casefold()
            if not group.id or folded in seen_group_ids:
                continue
            seen_group_ids.add(folded)
            normalized_groups.append(group)

        self.groups = normalized_groups
        return self

    def group_by_id(self, group_id):
        folded = str(group_id or "").strip().casefold()
        for group in self.groups:
            if group.id.casefold() == folded:
                return group
        return None

    def sync_with_entry_keys(self, entry_keys: Iterable[str], legacy_root_order=None):
        available_keys = {}
        ordered_entry_keys = []
        for key in entry_keys:
            text = str(key or "").strip()
            folded = text.casefold()
            if not text or folded in available_keys:
                continue
            available_keys[folded] = text
            ordered_entry_keys.append(text)

        changed = False
        seen_item_keys = set()

        for group in self.groups:
            normalized_keys = []
            for key in group.item_keys:
                actual_key = available_keys.get(str(key).strip().casefold())
                if not actual_key:
                    changed = True
                    continue
                folded = actual_key.casefold()
                if folded in seen_item_keys:
                    changed = True
                    continue
                seen_item_keys.add(folded)
                normalized_keys.append(actual_key)
            if normalized_keys != group.item_keys:
                group.item_keys = normalized_keys
                changed = True

        root_item_keys = []
        for key in self.root_item_keys:
            actual_key = available_keys.get(str(key).strip().casefold())
            if not actual_key:
                changed = True
                continue
            folded = actual_key.casefold()
            if folded in seen_item_keys:
                changed = True
                continue
            seen_item_keys.add(folded)
            root_item_keys.append(actual_key)

        if not root_item_keys and legacy_root_order:
            for key in _normalize_unique_text_list(list(legacy_root_order)):
                actual_key = available_keys.get(key.casefold())
                if not actual_key:
                    continue
                folded = actual_key.casefold()
                if folded in seen_item_keys:
                    continue
                seen_item_keys.add(folded)
                root_item_keys.append(actual_key)

        for key in ordered_entry_keys:
            folded = key.casefold()
            if folded in seen_item_keys:
                continue
            seen_item_keys.add(folded)
            root_item_keys.append(key)

        if root_item_keys != self.root_item_keys:
            self.root_item_keys = root_item_keys
            changed = True

        return changed


class ConfigStore:
    def __init__(self, project_root):
        self.project_root = Path(project_root)
        self.data_dir = self.project_root / "data"
        self.config_path = self.data_dir / "settings.json"
        self.layout_path = self.data_dir / "menu_layout.json"
        self.default_menu_dir = self.project_root / "menu_items"

    @classmethod
    def default(cls):
        return cls(_project_root())

    def _runtime_menu_dir(self):
        return self.project_root

    def _loaded_menu_dir(self, menu_dir):
        normalized = str(menu_dir or "").strip()
        if not normalized:
            return str(self.default_menu_dir)
        try:
            candidate = Path(normalized)
        except (OSError, TypeError, ValueError):
            return str(self._runtime_menu_dir())
        if not candidate.exists():
            return str(self._runtime_menu_dir())
        return str(candidate)

    def _loaded_background_image_path(self, image_path):
        normalized = str(image_path or "").strip()
        if not normalized:
            return ""
        try:
            return normalized if Path(normalized).is_file() else ""
        except (OSError, TypeError, ValueError):
            return ""

    def _save_menu_dir(self, menu_dir):
        normalized = str(menu_dir or "").strip()
        fallback = self._runtime_menu_dir()
        if not normalized:
            return fallback
        try:
            candidate = Path(normalized)
            candidate.mkdir(parents=True, exist_ok=True)
            return candidate
        except OSError:
            fallback.mkdir(parents=True, exist_ok=True)
            return fallback

    def load(self):
        default_config = AppConfig(menu_dir=str(self.default_menu_dir)).normalized()
        self.default_menu_dir.mkdir(parents=True, exist_ok=True)
        self.data_dir.mkdir(parents=True, exist_ok=True)

        if not self.config_path.exists():
            return default_config

        try:
            payload = json.loads(self.config_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return default_config

        config = AppConfig(
            menu_dir=str(payload.get("menu_dir", default_config.menu_dir)),
            background_image_path=str(
                payload.get("background_image_path", default_config.background_image_path)
            ),
            global_hotkey=str(payload.get("global_hotkey", default_config.global_hotkey)),
            surface_opacity=payload.get("surface_opacity", default_config.surface_opacity),
            font_size=payload.get("font_size", default_config.font_size),
            window_width=payload.get("window_width", default_config.window_width),
            window_height=payload.get("window_height", default_config.window_height),
            icon_size=payload.get("icon_size", default_config.icon_size),
            edge_margin=payload.get("edge_margin", default_config.edge_margin),
            tile_order=payload.get("tile_order", default_config.tile_order),
        ).normalized()

        needs_save = False
        loaded_menu_dir = self._loaded_menu_dir(config.menu_dir)
        if loaded_menu_dir != config.menu_dir:
            config.menu_dir = loaded_menu_dir
            needs_save = True

        loaded_background_image = self._loaded_background_image_path(config.background_image_path)
        if loaded_background_image != config.background_image_path:
            config.background_image_path = loaded_background_image
            needs_save = True

        if needs_save:
            self.save(config)
        return config

    def save(self, config):
        config = config.normalized()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        config.menu_dir = str(self._save_menu_dir(config.menu_dir))
        config.background_image_path = self._loaded_background_image_path(config.background_image_path)
        self.config_path.write_text(
            json.dumps(asdict(config), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def load_menu_layout(self):
        self.data_dir.mkdir(parents=True, exist_ok=True)
        if not self.layout_path.exists():
            return MenuLayout().normalized()

        try:
            payload = json.loads(self.layout_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return MenuLayout().normalized()

        return MenuLayout(
            root_item_keys=payload.get("root_item_keys", []),
            groups=payload.get("groups", []),
        ).normalized()

    def save_menu_layout(self, layout):
        self.data_dir.mkdir(parents=True, exist_ok=True)
        layout = layout.normalized()
        self.layout_path.write_text(
            json.dumps(asdict(layout), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
