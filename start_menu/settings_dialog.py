from pathlib import Path

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from start_menu.config import AppConfig
from start_menu.hotkey import HOTKEY_PRESETS


class SettingsDialog(QDialog):
    def __init__(self, config, parent=None):
        super().__init__(parent)
        self._config = config
        self.setWindowTitle("设置")
        self.setModal(True)
        self.setMinimumWidth(460)

        self._build_ui()

    def _build_ui(self):
        root_layout = QVBoxLayout(self)
        form_layout = QFormLayout()
        form_layout.setContentsMargins(0, 0, 0, 0)
        form_layout.setSpacing(12)

        self.menu_dir_edit = QLineEdit(self._config.menu_dir)
        browse_button = QPushButton("浏览")
        browse_button.clicked.connect(self._browse_directory)

        directory_row = QHBoxLayout()
        directory_row.addWidget(self.menu_dir_edit)
        directory_row.addWidget(browse_button)

        directory_widget = QWidget()
        directory_wrapper = QVBoxLayout(directory_widget)
        directory_wrapper.setContentsMargins(0, 0, 0, 0)
        directory_wrapper.addLayout(directory_row)

        helper_label = QLabel("这个目录里的文件、文件夹、快捷方式会显示在主菜单里。")
        helper_label.setWordWrap(True)
        helper_label.setObjectName("helperLabel")
        directory_wrapper.addWidget(helper_label)

        self.background_image_edit = QLineEdit(self._config.background_image_path)
        self.background_image_edit.setPlaceholderText("留空表示不使用背景图")

        image_browse_button = QPushButton("浏览")
        image_browse_button.clicked.connect(self._browse_background_image)

        image_clear_button = QPushButton("清除")
        image_clear_button.clicked.connect(self._clear_background_image)

        image_row = QHBoxLayout()
        image_row.addWidget(self.background_image_edit)
        image_row.addWidget(image_browse_button)
        image_row.addWidget(image_clear_button)

        image_widget = QWidget()
        image_wrapper = QVBoxLayout(image_widget)
        image_wrapper.setContentsMargins(0, 0, 0, 0)
        image_wrapper.addLayout(image_row)

        image_helper_label = QLabel("支持 PNG、JPG、JPEG、BMP、WEBP，会显示在主菜单背景里。")
        image_helper_label.setWordWrap(True)
        image_helper_label.setObjectName("helperLabel")
        image_wrapper.addWidget(image_helper_label)

        self.hotkey_combo = QComboBox()
        for preset in HOTKEY_PRESETS:
            self.hotkey_combo.addItem(preset.display_text)
        current_index = self.hotkey_combo.findText(self._config.global_hotkey)
        if current_index >= 0:
            self.hotkey_combo.setCurrentIndex(current_index)

        hotkey_helper_label = QLabel("当前只支持预设热键组合，优先推荐使用 Win+Z。")
        hotkey_helper_label.setWordWrap(True)
        hotkey_helper_label.setObjectName("helperLabel")

        hotkey_widget = QWidget()
        hotkey_wrapper = QVBoxLayout(hotkey_widget)
        hotkey_wrapper.setContentsMargins(0, 0, 0, 0)
        hotkey_wrapper.addWidget(self.hotkey_combo)
        hotkey_wrapper.addWidget(hotkey_helper_label)

        self.font_size_spin = QSpinBox()
        self.font_size_spin.setRange(8, 28)
        self.font_size_spin.setValue(self._config.font_size)

        self.surface_opacity_spin = QSpinBox()
        self.surface_opacity_spin.setRange(20, 100)
        self.surface_opacity_spin.setSuffix("%")
        self.surface_opacity_spin.setValue(self._config.surface_opacity)

        form_layout.addRow("菜单目录", directory_widget)
        form_layout.addRow("背景图片", image_widget)
        form_layout.addRow("全局热键", hotkey_widget)
        form_layout.addRow("面板透明度", self.surface_opacity_spin)
        form_layout.addRow("字体大小", self.font_size_spin)
        root_layout.addLayout(form_layout)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root_layout.addWidget(buttons)

    def _browse_directory(self):
        start_dir = self.menu_dir_edit.text().strip() or str(Path.home())
        selected = QFileDialog.getExistingDirectory(self, "选择菜单目录", start_dir)
        if selected:
            self.menu_dir_edit.setText(selected)

    def _browse_background_image(self):
        current_value = self.background_image_edit.text().strip()
        start_dir = current_value or str(Path.home())
        selected, _ = QFileDialog.getOpenFileName(
            self,
            "选择背景图片",
            start_dir,
            "Image Files (*.png *.jpg *.jpeg *.bmp *.webp);;All Files (*.*)",
        )
        if selected:
            self.background_image_edit.setText(selected)

    def _clear_background_image(self):
        self.background_image_edit.clear()

    def build_config(self):
        menu_dir = self.menu_dir_edit.text().strip() or self._config.menu_dir
        background_image_path = self.background_image_edit.text().strip()
        return AppConfig(
            menu_dir=menu_dir,
            background_image_path=background_image_path,
            global_hotkey=self.hotkey_combo.currentText(),
            surface_opacity=self.surface_opacity_spin.value(),
            font_size=self.font_size_spin.value(),
            window_width=self._config.window_width,
            window_height=self._config.window_height,
            icon_size=self._config.icon_size,
            edge_margin=self._config.edge_margin,
        ).normalized()
