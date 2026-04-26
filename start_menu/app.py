import ctypes
import sys


APP_NAME = "StartMenuXG"
SINGLE_INSTANCE_MUTEX_NAME = r"Local\StartMenuXG_SingleInstance"
ERROR_ALREADY_EXISTS = 183
MB_OK = 0x00000000
MB_ICONWARNING = 0x00000030
MB_SETFOREGROUND = 0x00010000
MB_TOPMOST = 0x00040000


class SingleInstanceGuard:
    def __init__(self, mutex_name):
        self.mutex_name = mutex_name
        self._mutex_handle = None
        self._kernel32 = None

        if sys.platform.startswith("win"):
            self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    def acquire(self):
        if self._kernel32 is None:
            return True

        handle = self._kernel32.CreateMutexW(None, False, self.mutex_name)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())

        if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
            self._kernel32.CloseHandle(handle)
            return False

        self._mutex_handle = handle
        return True

    def release(self):
        if self._kernel32 is None or not self._mutex_handle:
            return
        self._kernel32.CloseHandle(self._mutex_handle)
        self._mutex_handle = None


def _show_already_running_warning():
    message = "程序已启动\n程序已启"
    if sys.platform.startswith("win"):
        user32 = ctypes.WinDLL( "user32", use_last_error=True)
        user32.MessageBoxW(
            None,
            message,
            APP_NAME,
            MB_OK | MB_ICONWARNING | MB_SETFOREGROUND | MB_TOPMOST,
        )
        return
    print(message, file=sys.stderr)


def run():
    instance_guard = SingleInstanceGuard(SINGLE_INSTANCE_MUTEX_NAME)
    if not instance_guard.acquire():
        _show_already_running_warning()
        return 0

    from PySide6.QtWidgets import QApplication

    from start_menu.config import ConfigStore
    from start_menu.main_window import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName("Local")
    app.setStyle("Fusion")
    app.setQuitOnLastWindowClosed(False)

    try:
        window = MainWindow(ConfigStore.default())
        window.show()
        return app.exec()
    finally:
        instance_guard.release()
