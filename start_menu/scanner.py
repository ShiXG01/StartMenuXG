import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional


SKIP_NAMES = {"desktop.ini", "thumbs.db"}


@dataclass
class MenuEntry:
    path: Path
    display_name: str
    is_dir: bool
    entry_key: str


def _display_name(path):
    if path.suffix.lower() in {".lnk", ".url"}:
        return path.stem
    return path.name


def scan_menu_directory(menu_dir, ordered_keys: Optional[Iterable[str]] = None):
    base_path = Path(menu_dir)
    if not base_path.exists():
        return []

    order_lookup = {
        str(key).strip().casefold(): index
        for index, key in enumerate(ordered_keys or [])
        if str(key).strip()
    }

    entries = []
    for child in base_path.iterdir():
        if child.name.startswith("."):
            continue
        if child.name.lower() in SKIP_NAMES:
            continue

        entries.append(
            MenuEntry(
                path=child,
                display_name=_display_name(child),
                is_dir=child.is_dir(),
                entry_key=child.name,
            )
        )

    def _sort_key(item):
        order_index = order_lookup.get(item.entry_key.casefold())
        if order_index is not None:
            return (0, order_index)
        return (1, not item.is_dir, item.display_name.casefold())

    entries.sort(key=_sort_key)
    return entries


def open_path(path):
    target = str(Path(path))
    if hasattr(os, "startfile"):
        os.startfile(target)
        return
    raise OSError("This launcher currently expects Windows os.startfile support.")
