import ctypes
import json
import sys
import uuid
from pathlib import Path

from PySide6.QtCore import QFileInfo, QEvent, QMimeData, QTimer, Qt, QSize, Signal
from PySide6.QtGui import (
    QColor,
    QCursor,
    QDrag,
    QFont,
    QGuiApplication,
    QKeySequence,
    QPainter,
    QPainterPath,
    QPixmap,
    QShortcut,
)
from PySide6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QFileIconProvider,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from start_menu.config import MenuLayoutGroup
from start_menu.hotkey import GlobalHotkeyManager
from start_menu.scanner import open_path, scan_menu_directory
from start_menu.settings_dialog import SettingsDialog
from start_menu.theme import build_stylesheet, get_system_theme


IS_WINDOWS = sys.platform.startswith("win")
MIME_TILE = "application/x-startmenuxg-tile"
MIME_GROUP = "application/x-startmenuxg-group"
ROOT_GROUP_ID = "@root"

if IS_WINDOWS:
    from ctypes import wintypes

    class RECT(ctypes.Structure):
        _fields_ = [
            ("left", wintypes.LONG),
            ("top", wintypes.LONG),
            ("right", wintypes.LONG),
            ("bottom", wintypes.LONG),
        ]


def _encode_drag_payload(item_key, source_group_id):
    payload = {
        "item_key": str(item_key or "").strip(),
        "source_group_id": ROOT_GROUP_ID if source_group_id in (None, "") else str(source_group_id),
    }
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def _decode_drag_payload(mime_data):
    if not mime_data.hasFormat(MIME_TILE):
        return None

    try:
        payload = json.loads(bytes(mime_data.data(MIME_TILE)).decode("utf-8"))
    except (ValueError, TypeError, UnicodeDecodeError):
        return None

    item_key = str(payload.get("item_key", "")).strip()
    source_group_id = str(payload.get("source_group_id", ROOT_GROUP_ID)).strip() or ROOT_GROUP_ID
    if not item_key:
        return None
    return item_key, source_group_id


def _encode_group_drag_payload(group_id):
    payload = {
        "group_id": str(group_id or "").strip(),
    }
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def _decode_group_drag_payload(mime_data):
    if not mime_data.hasFormat(MIME_GROUP):
        return None

    try:
        payload = json.loads(bytes(mime_data.data(MIME_GROUP)).decode("utf-8"))
    except (ValueError, TypeError, UnicodeDecodeError):
        return None

    group_id = str(payload.get("group_id", "")).strip()
    if not group_id:
        return None
    return group_id


def _invisible_drag_pixmap():
    pixmap = QPixmap(1, 1)
    pixmap.fill(Qt.transparent)
    return pixmap


class BackgroundSurface(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._theme = None
        self._background_pixmap = QPixmap()
        self._corner_radius = 24

    def set_theme(self, theme):
        self._theme = theme
        self.update()

    def set_background_image(self, image_path):
        normalized_path = str(image_path or "").strip()
        if normalized_path and Path(normalized_path).is_file():
            pixmap = QPixmap(normalized_path)
            self._background_pixmap = pixmap if not pixmap.isNull() else QPixmap()
        else:
            self._background_pixmap = QPixmap()
        self.update()

    def rounded_path(self):
        path = QPainterPath()
        rect = self.rect().adjusted(0, 0, -1, -1)
        path.addRoundedRect(rect, self._corner_radius, self._corner_radius)
        return path

    def paintEvent(self, event):
        del event
        if self._theme is None:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        path = self.rounded_path()
        painter.setClipPath(path)
        painter.fillPath(path, QColor(self._theme.window_bg))

        if not self._background_pixmap.isNull():
            scaled = self._background_pixmap.scaled(
                self.size(),
                Qt.KeepAspectRatioByExpanding,
                Qt.SmoothTransformation,
            )
            offset_x = int((self.width() - scaled.width()) / 2)
            offset_y = int((self.height() - scaled.height()) / 2)
            painter.drawPixmap(offset_x, offset_y, scaled)

            overlay = QColor(self._theme.window_bg)
            overlay.setAlpha(112 if self._theme.name == "dark" else 88)
            painter.fillRect(self.rect(), overlay)

        border_color = QColor(self._theme.border)
        border_color.setAlpha(160)
        painter.setClipping(False)
        painter.setPen(border_color)
        painter.drawPath(path)


class DeferredTextInputDialog(QDialog):
    def __init__(self, title, label, initial_text="", parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(360)
        self._typing_target = QLineEdit(initial_text)

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(16, 16, 16, 16)
        root_layout.setSpacing(12)

        helper = QLabel(label)
        root_layout.addWidget(helper)
        root_layout.addWidget(self._typing_target)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root_layout.addWidget(buttons)

        self.setFocusPolicy(Qt.StrongFocus)

    def showEvent(self, event):
        super().showEvent(event)
        self.activateWindow()
        self.setFocus(Qt.ActiveWindowFocusReason)

    def text_value(self):
        return self._typing_target.text().strip()

    def keyPressEvent(self, event):
        if self._typing_target.hasFocus():
            super().keyPressEvent(event)
            return

        modifiers = event.modifiers()
        text = event.text()
        is_plain_text = bool(text) and text.isprintable() and not (modifiers & (Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier))

        if is_plain_text:
            self._typing_target.setFocus(Qt.ActiveWindowFocusReason)
            self._typing_target.insert(text)
            event.accept()
            return

        if event.key() == Qt.Key_Backspace:
            self._typing_target.setFocus(Qt.ActiveWindowFocusReason)
            event.accept()
            return

        super().keyPressEvent(event)


class WindowDragBar(QWidget):
    drag_started = Signal(object)
    drag_moved = Signal(object)
    drag_finished = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("windowDragBar")
        self.setCursor(Qt.OpenHandCursor)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.setCursor(Qt.ClosedHandCursor)
            self.drag_started.emit(event.globalPosition().toPoint())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.LeftButton:
            self.drag_moved.emit(event.globalPosition().toPoint())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.setCursor(Qt.OpenHandCursor)
            self.drag_finished.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)


class LauncherListWidget(QListWidget):
    move_requested = Signal(str, object, object, int)
    selection_changed = Signal(object, str)

    def __init__(self, group_id, parent=None):
        super().__init__(parent)
        self.group_id = group_id
        self._reorder_enabled = True

        self.setObjectName("launcherList")
        self.setAlternatingRowColors(False)
        self.setViewMode(QListView.IconMode)
        self.setFlow(QListView.LeftToRight)
        self.setMovement(QListView.Static)
        self.setResizeMode(QListView.Adjust)
        self.setWrapping(True)
        self.setWordWrap(True)
        self.setUniformItemSizes(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setTextElideMode(Qt.ElideRight)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setDragEnabled(True)
        self.viewport().setAcceptDrops(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)

        self.currentItemChanged.connect(self._on_current_item_changed)

    def set_group_id(self, group_id):
        self.group_id = group_id

    def set_reorder_enabled(self, enabled):
        self._reorder_enabled = bool(enabled)
        self.setDragEnabled(self._reorder_enabled)
        self.viewport().setAcceptDrops(self._reorder_enabled)
        self.setAcceptDrops(self._reorder_enabled)
        self.setDropIndicatorShown(self._reorder_enabled)

    def startDrag(self, supported_actions):
        del supported_actions
        if not self._reorder_enabled:
            return

        item = self.currentItem()
        if item is None:
            return

        entry = item.data(Qt.UserRole)
        if entry is None:
            return

        mime_data = self.mimeData(self.selectedItems())
        if mime_data is None:
            return
        mime_data.setData(MIME_TILE, _encode_drag_payload(entry.entry_key, self.group_id))

        drag = QDrag(self)
        drag.setMimeData(mime_data)
        drag.setPixmap(_invisible_drag_pixmap())

        drag.exec(Qt.MoveAction)

    def dragEnterEvent(self, event):
        payload = _decode_drag_payload(event.mimeData())
        if self._reorder_enabled and payload is not None:
            event.setDropAction(Qt.MoveAction)
            event.accept()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        payload = _decode_drag_payload(event.mimeData())
        if self._reorder_enabled and payload is not None:
            event.setDropAction(Qt.MoveAction)
            event.accept()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event):
        payload = _decode_drag_payload(event.mimeData())
        if self._reorder_enabled and payload is not None:
            item_key, source_group_id = payload
            target_row = self._drop_insert_row(event.position().toPoint())
            self.move_requested.emit(item_key, source_group_id, self.group_id, target_row)
            event.setDropAction(Qt.MoveAction)
            event.accept()
            return
        super().dropEvent(event)

    def _drop_insert_row(self, pos):
        count = self.count()
        if count <= 0:
            return 0

        first_rect = self.visualItemRect(self.item(0))
        grid_size = self.gridSize()
        cell_width = max(1, grid_size.width())
        cell_height = max(1, grid_size.height())
        origin_x = first_rect.left()
        origin_y = first_rect.top()
        relative_x = pos.x() - origin_x
        relative_y = pos.y() - origin_y

        if relative_y < 0:
            return 0

        column_count = self.column_count()
        row_index = max(0, relative_y // cell_height)

        if relative_x < 0:
            column_index = 0
            insert_after = False
        else:
            column_index = min(column_count - 1, relative_x // cell_width)
            insert_after = (relative_x % cell_width) >= (cell_width / 2)

        insert_row = row_index * column_count + column_index
        if insert_after:
            insert_row += 1

        return max(0, min(count, int(insert_row)))

    def column_count(self):
        grid_width = self.gridSize().width()
        if grid_width <= 0:
            return 1
        viewport_width = max(1, self.viewport().width())
        return max(1, viewport_width // grid_width)

    def update_height_to_contents(self):
        count = self.count()
        if count <= 0:
            self.setFixedHeight(0)
            return

        rows = int((count + self.column_count() - 1) / self.column_count())
        frame_height = self.frameWidth() * 2
        content_height = rows * self.gridSize().height()
        self.setFixedHeight(content_height + frame_height + 4)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.update_height_to_contents()

    def _on_current_item_changed(self, current, previous):
        del previous
        if current is None:
            return
        entry = current.data(Qt.UserRole)
        if entry is None:
            return
        self.selection_changed.emit(self.group_id, entry.entry_key)


class GroupHeaderWidget(QWidget):
    toggle_requested = Signal(object)
    rename_requested = Signal(object)
    delete_requested = Signal(object)
    move_requested = Signal(str, object, object, int)
    reorder_requested = Signal(object, object, bool)

    def __init__(self, group_id, editable, collapsible, parent=None):
        super().__init__(parent)
        self.group_id = group_id
        self.editable = editable
        self.collapsible = collapsible
        self._reorder_enabled = True
        self._item_count = 0
        self._drag_start_pos = None
        self.setObjectName("groupHeader")
        self.setAcceptDrops(True)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)

        self.toggle_button = QPushButton("▾" if collapsible else "")
        self.toggle_button.setObjectName("groupToggleButton")
        self.toggle_button.setFixedWidth(24)
        self.toggle_button.setProperty("surfaceRole", "launcher")
        self.toggle_button.clicked.connect(self._emit_toggle)
        layout.addWidget(self.toggle_button)

        self.title_label = QLabel()
        self.title_label.setObjectName("groupTitleLabel")
        self.title_label.setCursor(Qt.OpenHandCursor if self.editable else Qt.ArrowCursor)
        layout.addWidget(self.title_label, 1)

        self.count_label = QLabel()
        self.count_label.setObjectName("groupCountLabel")
        self.count_label.setCursor(Qt.OpenHandCursor if self.editable else Qt.ArrowCursor)
        layout.addWidget(self.count_label)

        self.rename_button = QPushButton("重命名")
        self.rename_button.setProperty("surfaceRole", "launcher")
        self.rename_button.clicked.connect(lambda: self.rename_requested.emit(self.group_id))
        self.rename_button.setVisible(self.editable)
        layout.addWidget(self.rename_button)

        self.delete_button = QPushButton("删除")
        self.delete_button.setProperty("surfaceRole", "launcher")
        self.delete_button.clicked.connect(lambda: self.delete_requested.emit(self.group_id))
        self.delete_button.setVisible(self.editable)
        layout.addWidget(self.delete_button)

        if not self.collapsible:
            self.toggle_button.setEnabled(False)

        for child in (
            self,
            self.toggle_button,
            self.title_label,
            self.count_label,
            self.rename_button,
            self.delete_button,
        ):
            child.setAcceptDrops(True)
            if child is not self:
                child.installEventFilter(self)

    def set_state(self, title, item_count, collapsed):
        self._item_count = max(0, int(item_count))
        self.title_label.setText(title)
        self.count_label.setText("{0} 项".format(self._item_count))
        self.toggle_button.setText("▸" if collapsed else "▾")
        self.toggle_button.setVisible(self.collapsible)

    def set_reorder_enabled(self, enabled):
        self._reorder_enabled = bool(enabled)

    def dragEnterEvent(self, event):
        if self._accept_group_drag_event(event):
            return
        if self._accept_drag_event(event):
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if self._accept_group_drag_event(event):
            return
        if self._accept_drag_event(event):
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event):
        source_group_id = _decode_group_drag_payload(event.mimeData())
        if self._can_accept_group_drop(source_group_id):
            self.reorder_requested.emit(source_group_id, self.group_id, self._insert_after_for_pos(event.position().toPoint().y()))
            event.setDropAction(Qt.MoveAction)
            event.accept()
            return

        payload = _decode_drag_payload(event.mimeData())
        if self._reorder_enabled and payload is not None:
            item_key, source_group_id = payload
            self.move_requested.emit(item_key, source_group_id, self.group_id, self._item_count)
            event.setDropAction(Qt.MoveAction)
            event.accept()
            return
        super().dropEvent(event)

    def eventFilter(self, watched, event):
        if watched in (
            self.toggle_button,
            self.title_label,
            self.count_label,
            self.rename_button,
            self.delete_button,
        ):
            if watched in (self.title_label, self.count_label):
                if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.LeftButton:
                    self._drag_start_pos = watched.mapTo(self, event.position().toPoint())
                    return False
                if event.type() == QEvent.Type.MouseMove and self._should_start_group_drag(watched.mapTo(self, event.position().toPoint()), event.buttons()):
                    return True
                if event.type() == QEvent.Type.MouseButtonRelease and event.button() == Qt.LeftButton:
                    self._drag_start_pos = None
                    return False

            if event.type() == QEvent.Type.DragEnter and self._accept_group_drag_event(event):
                return True
            if event.type() == QEvent.Type.DragMove and self._accept_group_drag_event(event):
                return True
            if event.type() == QEvent.Type.Drop:
                source_group_id = _decode_group_drag_payload(event.mimeData())
                if self._can_accept_group_drop(source_group_id):
                    self.reorder_requested.emit(
                        source_group_id,
                        self.group_id,
                        self._insert_after_for_pos(watched.mapTo(self, event.position().toPoint()).y()),
                    )
                    event.setDropAction(Qt.MoveAction)
                    event.accept()
                    return True

            if event.type() == QEvent.Type.DragEnter and self._accept_drag_event(event):
                return True
            if event.type() == QEvent.Type.DragMove and self._accept_drag_event(event):
                return True
            if event.type() == QEvent.Type.Drop:
                payload = _decode_drag_payload(event.mimeData())
                if self._reorder_enabled and payload is not None:
                    item_key, source_group_id = payload
                    self.move_requested.emit(item_key, source_group_id, self.group_id, self._item_count)
                    event.setDropAction(Qt.MoveAction)
                    event.accept()
                    return True
        return super().eventFilter(watched, event)

    def _accept_drag_event(self, event):
        payload = _decode_drag_payload(event.mimeData())
        if self._reorder_enabled and payload is not None:
            event.setDropAction(Qt.MoveAction)
            event.accept()
            return True
        return False

    def _accept_group_drag_event(self, event):
        source_group_id = _decode_group_drag_payload(event.mimeData())
        if self._can_accept_group_drop(source_group_id):
            event.setDropAction(Qt.MoveAction)
            event.accept()
            return True
        return False

    def _can_accept_group_drop(self, source_group_id):
        source_group_id = str(source_group_id or "").strip()
        if not self.editable or not source_group_id:
            return False
        if source_group_id == ROOT_GROUP_ID:
            return False
        return source_group_id != self.group_id

    def _insert_after_for_pos(self, y_pos):
        return y_pos >= max(1, self.height()) / 2

    def _can_drag_group(self):
        return self.editable and self.group_id != ROOT_GROUP_ID

    def _should_start_group_drag(self, local_pos, buttons):
        if not self._can_drag_group():
            return False
        if self._drag_start_pos is None or not (buttons & Qt.LeftButton):
            return False
        if (local_pos - self._drag_start_pos).manhattanLength() < QApplication.startDragDistance():
            return False
        self._start_group_drag(local_pos)
        self._drag_start_pos = None
        return True

    def _start_group_drag(self, local_pos):
        mime_data = QMimeData()
        mime_data.setData(MIME_GROUP, _encode_group_drag_payload(self.group_id))

        drag = QDrag(self)
        drag.setMimeData(mime_data)
        drag.setPixmap(_invisible_drag_pixmap())
        drag.exec(Qt.MoveAction)

    def _emit_toggle(self):
        if self.collapsible:
            self.toggle_requested.emit(self.group_id)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self._can_drag_group():
            self._drag_start_pos = event.position().toPoint()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._should_start_group_drag(event.position().toPoint(), event.buttons()):
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_start_pos = None
        super().mouseReleaseEvent(event)


class GroupSectionWidget(QWidget):
    toggle_requested = Signal(object)
    rename_requested = Signal(object)
    delete_requested = Signal(object)
    move_requested = Signal(str, object, object, int)
    reorder_requested = Signal(object, object, bool)
    selection_changed = Signal(object, str)
    item_activated = Signal(object)

    def __init__(self, group_id, title, editable, collapsible, parent=None):
        super().__init__(parent)
        self.group_id = group_id
        self._entries = []
        self._collapsed = False

        self.setObjectName("groupSection")
        self.setAcceptDrops(True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.header = GroupHeaderWidget(group_id, editable, collapsible)
        self.header.set_state(title, 0, False)
        self.header.toggle_requested.connect(lambda current_group_id: self.toggle_requested.emit(current_group_id))
        self.header.rename_requested.connect(lambda current_group_id: self.rename_requested.emit(current_group_id))
        self.header.delete_requested.connect(lambda current_group_id: self.delete_requested.emit(current_group_id))
        self.header.move_requested.connect(
            lambda item_key, source_group_id, target_group_id, target_row: self.move_requested.emit(
                item_key,
                source_group_id,
                target_group_id,
                target_row,
            )
        )
        self.header.reorder_requested.connect(
            lambda source_group_id, target_group_id, insert_after: self.reorder_requested.emit(
                source_group_id,
                target_group_id,
                insert_after,
            )
        )
        layout.addWidget(self.header)

        self.tile_list = LauncherListWidget(group_id)
        self.tile_list.move_requested.connect(
            lambda item_key, source_group_id, target_group_id, target_row: self.move_requested.emit(
                item_key,
                source_group_id,
                target_group_id,
                target_row,
            )
        )
        self.tile_list.selection_changed.connect(
            lambda current_group_id, entry_key: self.selection_changed.emit(current_group_id, entry_key)
        )
        self.tile_list.itemActivated.connect(lambda item: self.item_activated.emit(item))
        layout.addWidget(self.tile_list)

        self.empty_label = QLabel("拖动磁贴到这里")
        self.empty_label.setObjectName("groupEmptyLabel")
        self.empty_label.setAlignment(Qt.AlignCenter)
        self.empty_label.setAcceptDrops(True)
        self.empty_label.installEventFilter(self)
        self.empty_label.hide()
        layout.addWidget(self.empty_label)

    def populate(self, entries, icon_provider, icon_size, grid_size, collapsed, reorder_enabled):
        self._entries = list(entries)
        self._collapsed = bool(collapsed)
        self.header.set_state(self.header.title_label.text(), len(self._entries), self._collapsed)
        self.header.set_reorder_enabled(reorder_enabled)
        self.tile_list.set_group_id(self.group_id)
        self.tile_list.set_reorder_enabled(reorder_enabled)
        self.tile_list.setIconSize(QSize(icon_size, icon_size))
        self.tile_list.setGridSize(grid_size)
        self.tile_list.setSpacing(6)
        self.tile_list.clear()

        for entry in self._entries:
            item = QListWidgetItem(icon_provider(entry.path), entry.display_name)
            item.setData(Qt.UserRole, entry)
            item.setToolTip(str(entry.path))
            item.setTextAlignment(Qt.AlignHCenter | Qt.AlignTop)
            item.setSizeHint(grid_size)
            self.tile_list.addItem(item)

        self._apply_collapsed_state()
        self.tile_list.update_height_to_contents()

    def set_title(self, title):
        self.header.title_label.setText(title)

    def set_selected_entry(self, entry_key):
        target_key = str(entry_key or "").strip().casefold()
        for index in range(self.tile_list.count()):
            item = self.tile_list.item(index)
            entry = item.data(Qt.UserRole)
            if entry is None:
                continue
            if entry.entry_key.casefold() == target_key:
                self.tile_list.setCurrentRow(index)
                return True
        self.tile_list.clearSelection()
        self.tile_list.setCurrentItem(None)
        return False

    def clear_selection(self):
        self.tile_list.blockSignals(True)
        try:
            self.tile_list.clearSelection()
            self.tile_list.setCurrentItem(None)
        finally:
            self.tile_list.blockSignals(False)

    def scroll_selected_into_view(self):
        item = self.tile_list.currentItem()
        if item is not None:
            self.tile_list.scrollToItem(item)

    def entry_keys(self):
        return [entry.entry_key for entry in self._entries]

    def _apply_collapsed_state(self):
        show_tiles = (not self._collapsed) and bool(self._entries)
        self.tile_list.setVisible(show_tiles)
        self.empty_label.setVisible((not self._collapsed) and not self._entries)

    def dragEnterEvent(self, event):
        if self._accept_drag_event(event):
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if self._accept_drag_event(event):
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event):
        payload = _decode_drag_payload(event.mimeData())
        if payload is not None:
            item_key, source_group_id = payload
            self.move_requested.emit(item_key, source_group_id, self.group_id, len(self._entries))
            event.setDropAction(Qt.MoveAction)
            event.accept()
            return
        super().dropEvent(event)

    def eventFilter(self, watched, event):
        if watched is self.empty_label:
            if event.type() == QEvent.Type.DragEnter and self._accept_drag_event(event):
                return True
            if event.type() == QEvent.Type.DragMove and self._accept_drag_event(event):
                return True
            if event.type() == QEvent.Type.Drop:
                payload = _decode_drag_payload(event.mimeData())
                if payload is not None:
                    item_key, source_group_id = payload
                    self.move_requested.emit(item_key, source_group_id, self.group_id, len(self._entries))
                    event.setDropAction(Qt.MoveAction)
                    event.accept()
                    return True
        return super().eventFilter(watched, event)

    def _accept_drag_event(self, event):
        payload = _decode_drag_payload(event.mimeData())
        if payload is not None:
            event.setDropAction(Qt.MoveAction)
            event.accept()
            return True
        return False


class MainWindow(QMainWindow):
    hotkey_triggered = Signal()
    dismiss_requested = Signal()

    def __init__(self, config_store):
        super().__init__()
        self.config_store = config_store
        self.config = self.config_store.load()
        self.menu_layout = self.config_store.load_menu_layout()
        self.icon_provider = QFileIconProvider()
        self.hotkey_manager = GlobalHotkeyManager(
            self.hotkey_triggered.emit,
            self.config.global_hotkey,
            on_escape=self.dismiss_requested.emit,
            on_outside_click=self.dismiss_requested.emit,
            is_launcher_visible=self.isVisible,
            can_dismiss_launcher=lambda: not self._suspend_auto_hide,
            is_point_inside_launcher=self._contains_global_point,
        )
        self._initial_position_done = False
        self._allow_close = False
        self._suspend_auto_hide = False
        self._theme_name = None
        self._all_entries = []
        self._entry_map = {}
        self._drag_offset = None
        self._section_widgets = {}
        self._visible_item_order = []
        self._selected_group_id = ROOT_GROUP_ID
        self._selected_entry_key = None

        self.setWindowTitle("StartMenuXG")
        self.setMinimumSize(460, 360)
        self.resize(self.config.window_width, self.config.window_height)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground, True)

        self._build_ui()
        self._apply_style()
        self._apply_font_size()
        self.hotkey_triggered.connect(self.toggle_visibility, Qt.QueuedConnection)
        self.dismiss_requested.connect(self.hide_launcher, Qt.QueuedConnection)
        self._register_global_hotkey()
        self._start_theme_sync()
        self.reload_items()

    def _build_ui(self):
        self.surface = BackgroundSurface()
        root_layout = QVBoxLayout(self.surface)
        root_layout.setContentsMargins(18, 18, 18, 18)
        root_layout.setSpacing(10)

        self.drag_bar = WindowDragBar()
        self.drag_bar.drag_started.connect(self._start_window_drag)
        self.drag_bar.drag_moved.connect(self._move_window_drag)
        self.drag_bar.drag_finished.connect(self._finish_window_drag)
        top_row = QHBoxLayout(self.drag_bar)
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.setSpacing(8)

        self.status_label = QLabel("已加载 0 项")
        self.status_label.setObjectName("statusLabel")
        self.status_label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        top_row.addWidget(self.status_label, 1)

        new_group_button = QPushButton("新建分类")
        new_group_button.setProperty("surfaceRole", "launcher")
        new_group_button.clicked.connect(self.create_group)
        top_row.addWidget(new_group_button)

        root_layout.addWidget(self.drag_bar)

        self.search_input = QLineEdit()
        self.search_input.setObjectName("launcherSearchInput")
        self.search_input.setPlaceholderText("搜索当前菜单中的文件或目录")
        self.search_input.setClearButtonEnabled(True)
        self.search_input.installEventFilter(self)
        self.search_input.textChanged.connect(self._apply_search_filter)
        self.search_input.returnPressed.connect(self.open_selected)
        root_layout.addWidget(self.search_input)

        self.sections_scroll = QScrollArea()
        self.sections_scroll.setWidgetResizable(True)
        self.sections_scroll.setObjectName("sectionsScrollArea")
        self.sections_scroll.setFrameShape(QFrame.NoFrame)
        self.sections_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.sections_scroll.setWidgetResizable(True)

        self.sections_container = QWidget()
        self.sections_container.setObjectName("sectionsContainer")
        self.sections_layout = QVBoxLayout(self.sections_container)
        self.sections_layout.setContentsMargins(0, 0, 0, 0)
        self.sections_layout.setSpacing(12)
        self.sections_layout.setAlignment(Qt.AlignTop)
        self.sections_scroll.setWidget(self.sections_container)
        root_layout.addWidget(self.sections_scroll, 1)

        button_row = QHBoxLayout()
        button_row.setSpacing(8)

        open_folder_button = QPushButton("打开目录")
        open_folder_button.setProperty("surfaceRole", "launcher")
        open_folder_button.clicked.connect(self.open_menu_directory)
        button_row.addWidget(open_folder_button)

        refresh_button = QPushButton("刷新")
        refresh_button.setProperty("surfaceRole", "launcher")
        refresh_button.clicked.connect(self.reload_items)
        button_row.addWidget(refresh_button)

        settings_button = QPushButton("设置")
        settings_button.setProperty("surfaceRole", "launcher")
        settings_button.clicked.connect(self.open_settings)
        button_row.addWidget(settings_button)

        exit_button = QPushButton("退出")
        exit_button.setProperty("surfaceRole", "launcher")
        exit_button.clicked.connect(self.exit_application)
        button_row.addWidget(exit_button)

        root_layout.addLayout(button_row)

        QShortcut(QKeySequence("Return"), self, activated=self.open_selected)
        QShortcut(QKeySequence("Enter"), self, activated=self.open_selected)
        QShortcut(QKeySequence("Escape"), self, activated=self.hide_launcher)
        QShortcut(QKeySequence("F5"), self, activated=self.reload_items)
        self.setCentralWidget(self.surface)

    def _contains_global_point(self, x, y):
        if not self.isVisible():
            return False
        if not IS_WINDOWS:
            geometry = self.frameGeometry()
            return geometry.contains(x, y)

        rect = RECT()
        hwnd = int(self.winId())
        if not hwnd:
            return False

        if not ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return False

        if x < rect.left or x >= rect.right or y < rect.top or y >= rect.bottom:
            return False

        width_physical = max(1, rect.right - rect.left)
        height_physical = max(1, rect.bottom - rect.top)
        radius_x = self.surface._corner_radius * (width_physical / max(1, self.width()))
        radius_y = self.surface._corner_radius * (height_physical / max(1, self.height()))
        local_x = x - rect.left
        local_y = y - rect.top

        if local_x < radius_x and local_y < radius_y:
            return self._point_in_corner_ellipse(local_x, local_y, radius_x, radius_y, radius_x, radius_y)
        if local_x >= width_physical - radius_x and local_y < radius_y:
            return self._point_in_corner_ellipse(
                local_x,
                local_y,
                width_physical - radius_x,
                radius_y,
                radius_x,
                radius_y,
            )
        if local_x < radius_x and local_y >= height_physical - radius_y:
            return self._point_in_corner_ellipse(
                local_x,
                local_y,
                radius_x,
                height_physical - radius_y,
                radius_x,
                radius_y,
            )
        if local_x >= width_physical - radius_x and local_y >= height_physical - radius_y:
            return self._point_in_corner_ellipse(
                local_x,
                local_y,
                width_physical - radius_x,
                height_physical - radius_y,
                radius_x,
                radius_y,
            )

        return True

    def _point_in_corner_ellipse(self, x, y, center_x, center_y, radius_x, radius_y):
        if radius_x <= 0 or radius_y <= 0:
            return True
        dx = (x - center_x) / radius_x
        dy = (y - center_y) / radius_y
        return (dx * dx + dy * dy) <= 1.0

    def _apply_style(self):
        theme = get_system_theme()
        self._theme_name = theme.name
        self.surface.set_theme(theme)
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(build_stylesheet(theme, self.config.surface_opacity))

    def _start_theme_sync(self):
        self._theme_timer = QTimer(self)
        self._theme_timer.setInterval(1500)
        self._theme_timer.timeout.connect(self._sync_theme)
        self._theme_timer.start()

    def _sync_theme(self):
        theme = get_system_theme()
        if theme.name == self._theme_name:
            return
        self._apply_style()

    def _apply_font_size(self):
        app_font = QFont(self.font())
        app_font.setPointSize(self.config.font_size)
        self.setFont(app_font)
        self._render_sections()

    def _tile_icon_size(self):
        return max(32, int(self.config.icon_size))

    def _tile_grid_size(self):
        icon_size = self._tile_icon_size()
        metrics = self.fontMetrics()
        text_height = max(24, metrics.lineSpacing())
        width = max(100, icon_size + 24)
        height = max(82, icon_size + text_height + 12)
        return QSize(width, height)

    def showEvent(self, event):
        super().showEvent(event)
        if not self._initial_position_done:
            self._initial_position_done = True
            QTimer.singleShot(0, self.position_to_cursor)
        QTimer.singleShot(0, self._refresh_section_heights)

    def event(self, event):
        if event.type() == QEvent.Type.WindowDeactivate and not self._suspend_auto_hide:
            QTimer.singleShot(0, self._hide_if_inactive)
        return super().event(event)

    def eventFilter(self, watched, event):
        if watched is self.search_input and event.type() == QEvent.Type.KeyPress:
            if event.key() in (Qt.Key_Down, Qt.Key_Right):
                self.select_next_item()
                event.accept()
                return True
            if event.key() in (Qt.Key_Up, Qt.Key_Left):
                self.select_previous_item()
                event.accept()
                return True
        return super().eventFilter(watched, event)

    def closeEvent(self, event):
        self.config.window_width = self.width()
        self.config.window_height = self.height()
        self.config_store.save(self.config)
        if not self._allow_close:
            event.ignore()
            self.hide_launcher()
            return

        self.hotkey_manager.unregister()
        super().closeEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.surface.update()
        QTimer.singleShot(0, self._refresh_section_heights)

    def position_to_cursor(self):
        screen = QGuiApplication.screenAt(QCursor.pos())
        if screen is None:
            screen = self.screen() or QGuiApplication.primaryScreen()
        if screen is None:
            return

        available = screen.availableGeometry()
        frame = self.frameGeometry()
        cursor_pos = QCursor.pos()
        offset = 12

        x = cursor_pos.x() + offset
        y = cursor_pos.y() + offset

        if x + frame.width() > available.x() + available.width() - self.config.edge_margin:
            x = cursor_pos.x() - frame.width() - offset
        if y + frame.height() > available.y() + available.height() - self.config.edge_margin:
            y = cursor_pos.y() - frame.height() - offset

        min_x = available.x() + self.config.edge_margin
        min_y = available.y() + self.config.edge_margin
        max_x = available.x() + available.width() - frame.width() - self.config.edge_margin
        max_y = available.y() + available.height() - frame.height() - self.config.edge_margin

        x = max(min_x, min(max_x, x))
        y = max(min_y, min(max_y, y))
        self.move(x, y)

    def _menu_dir(self):
        return Path(self.config.menu_dir)

    def _search_query(self):
        return self.search_input.text().strip().casefold()

    def _register_global_hotkey(self):
        self.hotkey_manager.set_hotkey_text(self.config.global_hotkey)
        self.hotkey_manager.register()

    def _hide_if_inactive(self):
        if self._suspend_auto_hide:
            return
        if not self.isVisible():
            return

        app = QApplication.instance()
        active_window = app.activeWindow() if app is not None else None
        if active_window in (self,):
            return
        self.hide_launcher()

    def _apply_background_image(self):
        self.surface.set_background_image(self.config.background_image_path)

    def _entry_matches_search(self, entry, query):
        if not query:
            return True
        return query in entry.display_name.casefold()

    def _ordered_entries_for_keys(self, item_keys, query):
        entries = []
        for key in item_keys:
            entry = self._entry_map.get(key.casefold())
            if entry is None:
                continue
            if not self._entry_matches_search(entry, query):
                continue
            entries.append(entry)
        return entries

    def _update_status_for_groups(self, visible_count):
        query = self.search_input.text().strip()
        if query:
            self.status_label.setText("已加载 {0} 项，匹配 {1} 项".format(len(self._all_entries), visible_count))
            return
        self.status_label.setText("已加载 {0} 项".format(len(self._all_entries)))

    def _ensure_section(self, group_id, title, editable, collapsible):
        section = self._section_widgets.get(group_id)
        if section is not None:
            section.set_title(title)
            return section

        section = GroupSectionWidget(group_id, title, editable, collapsible)
        section.toggle_requested.connect(self.toggle_group)
        section.rename_requested.connect(self.rename_group)
        section.delete_requested.connect(self.delete_group)
        section.move_requested.connect(self.handle_move_request)
        section.reorder_requested.connect(self.handle_group_reorder_request)
        section.selection_changed.connect(self.handle_selection_changed)
        section.item_activated.connect(self._open_item)
        self._section_widgets[group_id] = section
        return section

    def _clear_section_layout(self):
        while self.sections_layout.count():
            item = self.sections_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()

    def _remove_deleted_sections(self, valid_group_ids):
        stale_group_ids = [group_id for group_id in self._section_widgets if group_id not in valid_group_ids]
        for group_id in stale_group_ids:
            section = self._section_widgets.pop(group_id)
            self.sections_layout.removeWidget(section)
            section.hide()
            section.deleteLater()

    def _render_sections(self):
        query = self._search_query()
        reorder_enabled = not query
        visible_count = 0
        self._visible_item_order = []
        visible_sections = []
        valid_group_ids = {ROOT_GROUP_ID}
        valid_group_ids.update(group.id for group in self.menu_layout.groups)

        self.sections_container.setUpdatesEnabled(False)
        try:
            self._remove_deleted_sections(valid_group_ids)

            for group in self.menu_layout.groups:
                group_entries = self._ordered_entries_for_keys(group.item_keys, query)
                if query and not group_entries:
                    continue

                visible_count += len(group_entries)
                collapsed = False if query else group.collapsed
                section = self._ensure_section(group.id, group.name, editable=True, collapsible=True)
                section.populate(
                    group_entries,
                    self._icon_for,
                    self._tile_icon_size(),
                    self._tile_grid_size(),
                    collapsed=collapsed,
                    reorder_enabled=reorder_enabled,
                )
                visible_sections.append(section)
                self._visible_item_order.extend((group.id, entry.entry_key) for entry in group_entries)

            root_entries = self._ordered_entries_for_keys(self.menu_layout.root_item_keys, query)
            visible_count += len(root_entries)
            root_section = self._ensure_section(ROOT_GROUP_ID, "未分类", editable=False, collapsible=False)
            root_section.populate(
                root_entries,
                self._icon_for,
                self._tile_icon_size(),
                self._tile_grid_size(),
                collapsed=False,
                reorder_enabled=reorder_enabled,
            )
            visible_sections.append(root_section)
            self._visible_item_order.extend((ROOT_GROUP_ID, entry.entry_key) for entry in root_entries)

            self._clear_section_layout()
            for section in visible_sections:
                self.sections_layout.addWidget(section)
                section.show()
        finally:
            self.sections_container.setUpdatesEnabled(True)
            self.sections_container.update()

        self._update_status_for_groups(visible_count)
        self._restore_or_select_first_visible()
        QTimer.singleShot(0, self._refresh_section_heights)

    def _refresh_section_heights(self):
        for section in self._section_widgets.values():
            section.tile_list.update_height_to_contents()

    def _restore_or_select_first_visible(self):
        if self._selected_entry_key is not None:
            if self._set_current_selection(self._selected_group_id, self._selected_entry_key):
                return
        if self._visible_item_order:
            group_id, entry_key = self._visible_item_order[0]
            self._set_current_selection(group_id, entry_key)
            return

        self._selected_group_id = ROOT_GROUP_ID
        self._selected_entry_key = None
        for section in self._section_widgets.values():
            section.clear_selection()

    def _set_current_selection(self, group_id, entry_key):
        selected = False
        for current_group_id, section in self._section_widgets.items():
            if current_group_id == group_id:
                selected = section.set_selected_entry(entry_key) or selected
            else:
                section.clear_selection()

        if selected:
            self._selected_group_id = group_id
            self._selected_entry_key = entry_key
            section = self._section_widgets.get(group_id)
            if section is not None:
                section.scroll_selected_into_view()
            return True
        return False

    def _current_item_index(self):
        if self._selected_entry_key is None:
            return -1
        target = (self._selected_group_id, self._selected_entry_key)
        try:
            return self._visible_item_order.index(target)
        except ValueError:
            return -1

    def _step_selection(self, delta):
        count = len(self._visible_item_order)
        if count <= 0:
            return

        current_index = self._current_item_index()
        if current_index < 0:
            next_index = 0 if delta >= 0 else count - 1
        else:
            next_index = max(0, min(count - 1, current_index + delta))

        group_id, entry_key = self._visible_item_order[next_index]
        self._set_current_selection(group_id, entry_key)

    def select_next_item(self):
        self._step_selection(1)

    def select_previous_item(self):
        self._step_selection(-1)

    def handle_selection_changed(self, group_id, entry_key):
        self._selected_group_id = group_id
        self._selected_entry_key = entry_key
        for current_group_id, section in self._section_widgets.items():
            if current_group_id == group_id:
                continue
            section.clear_selection()

    def _apply_search_filter(self):
        self._render_sections()

    def reload_items(self, clear_search=False):
        menu_dir = self._menu_dir()
        menu_dir.mkdir(parents=True, exist_ok=True)
        self._all_entries = scan_menu_directory(menu_dir)
        self._entry_map = {entry.entry_key.casefold(): entry for entry in self._all_entries}
        if self.menu_layout.sync_with_entry_keys(
            [entry.entry_key for entry in self._all_entries],
            legacy_root_order=self.config.tile_order,
        ):
            self.config_store.save_menu_layout(self.menu_layout)
        self._apply_background_image()

        if clear_search and self.search_input.text():
            self.search_input.blockSignals(True)
            self.search_input.clear()
            self.search_input.blockSignals(False)

        self._render_sections()

    def _group_item_keys(self, group_id):
        if group_id in (None, "", ROOT_GROUP_ID):
            return self.menu_layout.root_item_keys

        group = self.menu_layout.group_by_id(group_id)
        if group is None:
            return None
        return group.item_keys

    def _group_name(self, group_id):
        if group_id in (None, "", ROOT_GROUP_ID):
            return "未分类"
        group = self.menu_layout.group_by_id(group_id)
        return group.name if group is not None else ""

    def create_group(self):
        dialog = DeferredTextInputDialog("新建分类", "分类名称：", "新分类", self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        name = dialog.text_value()
        if not name:
            return

        group = MenuLayoutGroup(
            id="group_" + uuid.uuid4().hex[:10],
            name=name,
            collapsed=False,
            item_keys=[],
        ).normalized()
        self.menu_layout.groups.append(group)
        self.config_store.save_menu_layout(self.menu_layout)
        self._render_sections()

    def rename_group(self, group_id):
        group = self.menu_layout.group_by_id(group_id)
        if group is None:
            return

        dialog = DeferredTextInputDialog("重命名分类", "分类名称：", group.name, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        name = dialog.text_value()
        if not name or name == group.name:
            return

        group.name = name
        self.config_store.save_menu_layout(self.menu_layout)
        self._render_sections()

    def delete_group(self, group_id):
        group = self.menu_layout.group_by_id(group_id)
        if group is None:
            return

        result = QMessageBox.question(
            self,
            "删除分类",
            "删除分类“{0}”后，其中的磁贴会回到未分类。是否继续？".format(group.name),
        )
        if result != QMessageBox.StandardButton.Yes:
            return

        for item_key in group.item_keys:
            if item_key not in self.menu_layout.root_item_keys:
                self.menu_layout.root_item_keys.append(item_key)

        self.menu_layout.groups = [item for item in self.menu_layout.groups if item.id != group.id]
        self.config_store.save_menu_layout(self.menu_layout)
        self._render_sections()

    def toggle_group(self, group_id):
        group = self.menu_layout.group_by_id(group_id)
        if group is None:
            return

        group.collapsed = not group.collapsed
        self.config_store.save_menu_layout(self.menu_layout)
        self._render_sections()

    def handle_move_request(self, item_key, source_group_id, target_group_id, target_row):
        if self._search_query():
            return

        source_keys = self._group_item_keys(source_group_id)
        target_keys = self._group_item_keys(target_group_id)
        if source_keys is None or target_keys is None:
            return

        try:
            source_index = source_keys.index(item_key)
        except ValueError:
            return

        source_keys.pop(source_index)
        if source_keys is target_keys and target_row > source_index:
            target_row -= 1

        target_row = max(0, min(len(target_keys), int(target_row)))
        target_keys.insert(target_row, item_key)
        self.config_store.save_menu_layout(self.menu_layout)
        self._selected_group_id = ROOT_GROUP_ID if target_group_id in (None, "", ROOT_GROUP_ID) else target_group_id
        self._selected_entry_key = item_key
        self._render_sections()

    def handle_group_reorder_request(self, source_group_id, target_group_id, insert_after):
        source_group_id = str(source_group_id or "").strip()
        target_group_id = str(target_group_id or "").strip()
        if not source_group_id or not target_group_id:
            return
        if source_group_id == target_group_id:
            return
        if ROOT_GROUP_ID in (source_group_id, target_group_id):
            return

        source_index = -1
        target_index = -1
        for index, group in enumerate(self.menu_layout.groups):
            if group.id == source_group_id:
                source_index = index
            if group.id == target_group_id:
                target_index = index

        if source_index < 0 or target_index < 0:
            return

        group = self.menu_layout.groups.pop(source_index)
        if source_index < target_index:
            target_index -= 1
        if insert_after:
            target_index += 1
        target_index = max(0, min(len(self.menu_layout.groups), target_index))
        self.menu_layout.groups.insert(target_index, group)
        self.config_store.save_menu_layout(self.menu_layout)
        self._render_sections()

    def _icon_for(self, path):
        return self.icon_provider.icon(QFileInfo(str(path)))

    def _open_item(self, item):
        if isinstance(item, QListWidgetItem):
            entry = item.data(Qt.UserRole)
        else:
            entry = item
        if entry is None:
            return
        self._open_entry(entry)

    def open_selected(self):
        if self._selected_entry_key is None:
            return

        entry = self._entry_map.get(str(self._selected_entry_key).casefold())
        if entry is None:
            return
        self._open_entry(entry)

    def _open_entry(self, entry):
        try:
            open_path(entry.path)
        except OSError as exc:
            QMessageBox.warning(self, "打开失败", str(exc))

    def open_menu_directory(self):
        try:
            open_path(self._menu_dir())
        except OSError as exc:
            QMessageBox.warning(self, "打开目录失败", str(exc))

    def _focus_launcher(self):
        self.raise_()
        self.activateWindow()
        window_handle = self.windowHandle()
        if window_handle is not None:
            window_handle.requestActivate()
        if sys.platform.startswith("win"):
            user32 = ctypes.windll.user32
            hwnd = int(self.winId())
            user32.BringWindowToTop(hwnd)
            user32.SetForegroundWindow(hwnd)
            user32.SetActiveWindow(hwnd)
        self.setFocus(Qt.ActiveWindowFocusReason)
        current_section = self._section_widgets.get(self._selected_group_id)
        if current_section is not None and current_section.tile_list.isVisible():
            current_section.tile_list.setFocus(Qt.ActiveWindowFocusReason)

    def _start_window_drag(self, global_pos):
        self._drag_offset = global_pos - self.frameGeometry().topLeft()

    def _move_window_drag(self, global_pos):
        if self._drag_offset is None:
            return
        self.move(global_pos - self._drag_offset)

    def _finish_window_drag(self):
        self._drag_offset = None

    def show_launcher(self):
        self.reload_items(clear_search=True)
        self.showNormal()
        self.position_to_cursor()
        QTimer.singleShot(0, self._focus_launcher)

    def hide_launcher(self):
        self.hide()

    def toggle_visibility(self):
        if self.isVisible() and self.isActiveWindow() and not self.isMinimized():
            self.hide_launcher()
            return
        self.show_launcher()

    def exit_application(self):
        self._allow_close = True
        app = QApplication.instance()
        if app is not None:
            app.quit()
            return
        self.close()

    def open_settings(self):
        self._suspend_auto_hide = True
        dialog = SettingsDialog(self.config, self)
        try:
            result = dialog.exec()
        finally:
            self._suspend_auto_hide = False

        if result != QDialog.DialogCode.Accepted:
            return

        self.config = dialog.build_config()
        self.config_store.save(self.config)
        self.hotkey_manager.set_hotkey_text(self.config.global_hotkey)
        self.resize(self.config.window_width, self.config.window_height)
        self._apply_style()
        self._apply_font_size()
        self._apply_background_image()
        self.reload_items()
        self.position_to_cursor()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.hide_launcher()
            event.accept()
            return
        if event.key() == Qt.Key_Down and not self.search_input.hasFocus():
            self.select_next_item()
            event.accept()
            return
        if event.key() == Qt.Key_Up and not self.search_input.hasFocus():
            self.select_previous_item()
            event.accept()
            return
        if event.key() == Qt.Key_Right and not self.search_input.hasFocus():
            self.select_next_item()
            event.accept()
            return
        if event.key() == Qt.Key_Left and not self.search_input.hasFocus():
            self.select_previous_item()
            event.accept()
            return
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and not self.search_input.hasFocus():
            self.open_selected()
            event.accept()
            return

        modifiers = event.modifiers()
        text = event.text()
        is_plain_text = bool(text) and text.isprintable() and not (modifiers & (Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier))
        if is_plain_text and not self.search_input.hasFocus():
            self.search_input.setFocus(Qt.ActiveWindowFocusReason)
            self.search_input.insert(text)
            event.accept()
            return

        if event.key() == Qt.Key_Backspace and not self.search_input.hasFocus() and self.search_input.text():
            self.search_input.setFocus(Qt.ActiveWindowFocusReason)
            current_text = self.search_input.text()
            self.search_input.setText(current_text[:-1])
            event.accept()
            return

        super().keyPressEvent(event)
