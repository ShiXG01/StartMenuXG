import ctypes
import sys
from pathlib import Path

from PySide6.QtCore import QFileInfo, QEvent, QTimer, Qt, QSize, Signal
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
    QAbstractItemView,
    QApplication,
    QDialog,
    QFileIconProvider,
    QLineEdit,
    QListView,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QHBoxLayout,
    QWidget,
    QLabel,
)

from start_menu.hotkey import GlobalHotkeyManager
from start_menu.scanner import open_path, scan_menu_directory
from start_menu.settings_dialog import SettingsDialog
from start_menu.theme import build_stylesheet, get_system_theme


IS_WINDOWS = sys.platform.startswith("win")

if IS_WINDOWS:
    from ctypes import wintypes

    class RECT(ctypes.Structure):
        _fields_ = [
            ("left", wintypes.LONG),
            ("top", wintypes.LONG),
            ("right", wintypes.LONG),
            ("bottom", wintypes.LONG),
        ]


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


class LauncherListWidget(QListWidget):
    items_reordered = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._drag_row = -1

    def startDrag(self, supported_actions):
        del supported_actions
        item = self.currentItem()
        if item is None:
            return

        self._drag_row = self.currentRow()
        mime_data = self.mimeData(self.selectedItems())
        if mime_data is None:
            self._drag_row = -1
            return

        drag = QDrag(self)
        drag.setMimeData(mime_data)

        item_rect = self.visualItemRect(item)
        if item_rect.isValid():
            drag.setPixmap(self.viewport().grab(item_rect))
            cursor_pos = self.viewport().mapFromGlobal(QCursor.pos())
            drag.setHotSpot(cursor_pos - item_rect.topLeft())

        drag.exec(Qt.MoveAction)
        self._drag_row = -1

    def dragEnterEvent(self, event):
        if event.source() is self:
            event.setDropAction(Qt.MoveAction)
            event.accept()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if event.source() is self:
            event.setDropAction(Qt.MoveAction)
            event.accept()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event):
        if event.source() is self and self._drag_row >= 0:
            target_row = self._drop_insert_row(event.position().toPoint())
            source_row = self._drag_row
            if target_row > source_row:
                target_row -= 1

            if target_row != source_row:
                item = self.takeItem(source_row)
                self.insertItem(target_row, item)
                self.setCurrentRow(target_row)
                self.viewport().update()
            event.setDropAction(Qt.MoveAction)
            event.accept()
            self.items_reordered.emit()
            self._drag_row = -1
            return

        super().dropEvent(event)
        self.items_reordered.emit()

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

        column_count = self._column_count_for_drop(cell_width, origin_x)
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

    def _column_count_for_drop(self, cell_width, origin_x):
        viewport_width = max(1, self.viewport().width())
        usable_width = max(cell_width, viewport_width - max(0, origin_x))
        return max(1, usable_width // cell_width)


class MainWindow(QMainWindow):
    hotkey_triggered = Signal()
    dismiss_requested = Signal()

    def __init__(self, config_store):
        super().__init__()
        self.config_store = config_store
        self.config = self.config_store.load()
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
        self._drag_offset = None

        self.setWindowTitle("StartMenuXG")
        self.setMinimumSize(400, 320)
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

        self.status_label = QLabel("已加载 0 项")
        self.status_label.setObjectName("statusLabel")
        root_layout.addWidget(self.status_label)

        self.search_input = QLineEdit()
        self.search_input.setObjectName("launcherSearchInput")
        self.search_input.setPlaceholderText("搜索当前菜单中的文件或目录")
        self.search_input.setClearButtonEnabled(True)
        self.search_input.installEventFilter(self)
        self.search_input.textChanged.connect(self._apply_search_filter)
        self.search_input.returnPressed.connect(self.open_selected)
        root_layout.addWidget(self.search_input)

        self.list_widget = LauncherListWidget()
        self.list_widget.setObjectName("launcherList")
        self.list_widget.setAlternatingRowColors(False)
        self.list_widget.setViewMode(QListView.IconMode)
        self.list_widget.setFlow(QListView.LeftToRight)
        self.list_widget.setMovement(QListView.Static)
        self.list_widget.setResizeMode(QListView.Adjust)
        self.list_widget.setWrapping(True)
        self.list_widget.setWordWrap(True)
        self.list_widget.setUniformItemSizes(True)
        self.list_widget.setSelectionMode(QAbstractItemView.SingleSelection)
        self.list_widget.setDragDropOverwriteMode(False)
        self.list_widget.setDropIndicatorShown(True)
        self.list_widget.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.list_widget.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.list_widget.setTextElideMode(Qt.ElideRight)
        self.list_widget.itemActivated.connect(self._open_item)
        self.list_widget.items_reordered.connect(self._save_tile_order)
        root_layout.addWidget(self.list_widget, 1)

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

        open_project_button = QPushButton("打开项目")
        open_project_button.setProperty("surfaceRole", "launcher")
        open_project_button.clicked.connect(self.open_project_directory)
        button_row.addWidget(open_project_button)

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
        self._configure_tile_list()

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
        self._configure_tile_list()

    def _tile_icon_size(self):
        return max(32, int(self.config.icon_size))

    def _tile_grid_size(self):
        icon_size = self._tile_icon_size()
        metrics = self.list_widget.fontMetrics()
        text_height = max(24, metrics.lineSpacing() * 2)
        width = max(100, icon_size + 24)
        height = max(80, icon_size + text_height + 12)
        return QSize(width, height)

    def _configure_tile_list(self):
        icon_size = self._tile_icon_size()
        grid_size = self._tile_grid_size()
        self.list_widget.setIconSize(QSize(icon_size, icon_size))
        self.list_widget.setGridSize(grid_size)
        self.list_widget.setSpacing(6)
        self._update_tile_reorder_state()

    def _grid_column_count(self):
        grid_width = self.list_widget.gridSize().width()
        if grid_width <= 0:
            return 1
        viewport_width = max(1, self.list_widget.viewport().width())
        return max(1, viewport_width // grid_width)

    def showEvent(self, event):
        super().showEvent(event)
        if not self._initial_position_done:
            self._initial_position_done = True
            QTimer.singleShot(0, self.position_to_cursor)

    def event(self, event):
        if event.type() == QEvent.Type.WindowDeactivate and not self._suspend_auto_hide:
            QTimer.singleShot(0, self._hide_if_inactive)
        return super().event(event)

    def eventFilter(self, watched, event):
        if watched is self.search_input and event.type() == QEvent.Type.KeyPress:
            if event.key() == Qt.Key_Down:
                self._step_selection(self._grid_column_count())
                event.accept()
                return True
            if event.key() == Qt.Key_Up:
                self._step_selection(-self._grid_column_count())
                event.accept()
                return True
            if event.key() == Qt.Key_Right:
                self.select_next_item()
                event.accept()
                return True
            if event.key() == Qt.Key_Left:
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

    def _render_entries(self, entries):
        current_item = self.list_widget.currentItem()
        current_path = None
        if current_item is not None:
            current_entry = current_item.data(Qt.UserRole)
            if current_entry is not None:
                current_path = str(current_entry.path)

        self.list_widget.clear()
        grid_size = self._tile_grid_size()

        selected_row = 0
        for index, entry in enumerate(entries):
            item = QListWidgetItem(self._icon_for(entry.path), entry.display_name)
            item.setData(Qt.UserRole, entry)
            item.setToolTip(str(entry.path))
            item.setTextAlignment(Qt.AlignHCenter | Qt.AlignTop)
            item.setSizeHint(grid_size)
            self.list_widget.addItem(item)
            if current_path and str(entry.path) == current_path:
                selected_row = index

        if entries:
            self.list_widget.setCurrentRow(selected_row)

    def _step_selection(self, delta):
        count = self.list_widget.count()
        if count <= 0:
            return

        current_row = self.list_widget.currentRow()
        if current_row < 0:
            next_row = 0 if delta >= 0 else count - 1
        else:
            next_row = max(0, min(count - 1, current_row + delta))

        self.list_widget.setCurrentRow(next_row)
        item = self.list_widget.currentItem()
        if item is not None:
            self.list_widget.scrollToItem(item)

    def select_next_item(self):
        self._step_selection(1)

    def select_previous_item(self):
        self._step_selection(-1)

    def _update_status_for_entries(self, entries):
        query = self.search_input.text().strip()
        if query:
            self.status_label.setText("已加载 {0} 项，匹配 {1} 项".format(len(self._all_entries), len(entries)))
            return
        self.status_label.setText("已加载 {0} 项".format(len(entries)))

    def _apply_search_filter(self):
        query = self._search_query()
        self._update_tile_reorder_state()
        filtered_entries = [entry for entry in self._all_entries if self._entry_matches_search(entry, query)]
        self._render_entries(filtered_entries)
        self._update_status_for_entries(filtered_entries)

    def reload_items(self, clear_search=False):
        menu_dir = self._menu_dir()
        menu_dir.mkdir(parents=True, exist_ok=True)
        self._all_entries = scan_menu_directory(menu_dir, self.config.tile_order)
        self._sync_tile_order_with_entries()
        self._apply_background_image()

        if clear_search and self.search_input.text():
            self.search_input.blockSignals(True)
            self.search_input.clear()
            self.search_input.blockSignals(False)

        self._apply_search_filter()

    def _update_tile_reorder_state(self):
        allow_reorder = not self._search_query()
        self.list_widget.setDragEnabled(allow_reorder)
        self.list_widget.viewport().setAcceptDrops(allow_reorder)
        self.list_widget.setAcceptDrops(allow_reorder)
        self.list_widget.setDropIndicatorShown(allow_reorder)
        self.list_widget.setDragDropMode(
            QAbstractItemView.InternalMove if allow_reorder else QAbstractItemView.NoDragDrop
        )
        if allow_reorder:
            self.list_widget.setDefaultDropAction(Qt.MoveAction)

    def _sync_tile_order_with_entries(self):
        actual_keys = {entry.entry_key.casefold(): entry.entry_key for entry in self._all_entries}
        normalized_order = []
        seen = set()

        for key in self.config.tile_order:
            folded = str(key or "").strip().casefold()
            actual_key = actual_keys.get(folded)
            if not actual_key or folded in seen:
                continue
            seen.add(folded)
            normalized_order.append(actual_key)

        for entry in self._all_entries:
            folded = entry.entry_key.casefold()
            if folded in seen:
                continue
            seen.add(folded)
            normalized_order.append(entry.entry_key)

        if normalized_order != self.config.tile_order:
            self.config.tile_order = normalized_order
            self.config_store.save(self.config)

    def _save_tile_order(self):
        if self._search_query():
            return

        tile_order = []
        for index in range(self.list_widget.count()):
            item = self.list_widget.item(index)
            entry = item.data(Qt.UserRole)
            if entry is None:
                continue
            tile_order.append(entry.entry_key)

        if tile_order and tile_order != self.config.tile_order:
            self.config.tile_order = tile_order
            self.config_store.save(self.config)

    def _icon_for(self, path):
        return self.icon_provider.icon(QFileInfo(str(path)))

    def _open_item(self, item):
        entry = item.data(Qt.UserRole)
        if entry is None:
            return
        self._open_entry(entry)

    def open_selected(self):
        item = self.list_widget.currentItem()
        if item is None:
            return
        self._open_item(item)

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

    def open_project_directory(self):
        try:
            open_path(self.config_store.project_root)
        except OSError as exc:
            QMessageBox.warning(self, "打开项目失败", str(exc))

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
        self.search_input.setFocus(Qt.ActiveWindowFocusReason)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_offset is not None and event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_offset = None
            event.accept()
            return
        super().mouseReleaseEvent(event)

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
        super().keyPressEvent(event)
