import ctypes
import sys
from ctypes import wintypes
from dataclasses import dataclass


IS_WINDOWS = sys.platform.startswith("win")
ULONG_PTR = getattr(wintypes, "ULONG_PTR", ctypes.c_size_t)
WH_KEYBOARD_LL = 13
WH_MOUSE_LL = 14
HC_ACTION = 0

WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105

WM_LBUTTONDOWN = 0x0201
WM_RBUTTONDOWN = 0x0204
WM_MBUTTONDOWN = 0x0207
WM_NCLBUTTONDOWN = 0x00A1
WM_NCRBUTTONDOWN = 0x00A4
WM_NCMBUTTONDOWN = 0x00A7

KEYEVENTF_KEYUP = 0x0002

VK_ESCAPE = 0x1B
VK_Z = 0x5A
VK_LWIN = 0x5B
VK_RWIN = 0x5C
CONTROL_VKS = {0x11, 0xA2, 0xA3}
SHIFT_VKS = {0x10, 0xA0, 0xA1}
ALT_VKS = {0x12, 0xA4, 0xA5}
MOUSE_DOWN_MESSAGES = {
    WM_LBUTTONDOWN,
    WM_RBUTTONDOWN,
    WM_MBUTTONDOWN,
    WM_NCLBUTTONDOWN,
    WM_NCRBUTTONDOWN,
    WM_NCMBUTTONDOWN,
}


@dataclass(frozen=True)
class HotkeySpec:
    display_text: str
    modifiers: tuple
    trigger_vk: int


DEFAULT_HOTKEY_TEXT = "Win+Z"
HOTKEY_PRESETS = (
    HotkeySpec("Win+Z", ("win",), VK_Z),
    HotkeySpec("Ctrl+Alt+Z", ("ctrl", "alt"), VK_Z),
    HotkeySpec("Ctrl+Shift+Z", ("ctrl", "shift"), VK_Z),
    HotkeySpec("Alt+Z", ("alt",), VK_Z),
)
HOTKEY_PRESET_MAP = {spec.display_text: spec for spec in HOTKEY_PRESETS}


def normalize_hotkey_text(text):
    candidate = str(text or "").strip()
    if candidate in HOTKEY_PRESET_MAP:
        return candidate
    return DEFAULT_HOTKEY_TEXT


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class POINT(ctypes.Structure):
    _fields_ = [
        ("x", wintypes.LONG),
        ("y", wintypes.LONG),
    ]


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("pt", POINT),
        ("mouseData", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class GlobalHotkeyManager:
    def __init__(
        self,
        on_trigger,
        hotkey_text=DEFAULT_HOTKEY_TEXT,
        on_escape=None,
        on_outside_click=None,
        is_launcher_visible=None,
        can_dismiss_launcher=None,
        is_point_inside_launcher=None,
    ):
        self.on_trigger = on_trigger
        self.on_escape = on_escape
        self.on_outside_click = on_outside_click
        self.is_launcher_visible = is_launcher_visible or (lambda: False)
        self.can_dismiss_launcher = can_dismiss_launcher or (lambda: False)
        self.is_point_inside_launcher = is_point_inside_launcher or (lambda x, y: False)
        self.hotkey_text = normalize_hotkey_text(hotkey_text)
        self._user32 = None
        self._kernel32 = None
        self._keyboard_hook_handle = None
        self._mouse_hook_handle = None
        self._keyboard_proc = None
        self._mouse_proc = None
        self._modifier_state = {}
        self._combo_active = False

        if IS_WINDOWS:
            self._user32 = ctypes.windll.user32
            self._kernel32 = ctypes.windll.kernel32
            self._keyboard_hook_proc_type = ctypes.WINFUNCTYPE(
                wintypes.LPARAM,
                ctypes.c_int,
                wintypes.WPARAM,
                wintypes.LPARAM,
            )
            self._mouse_hook_proc_type = ctypes.WINFUNCTYPE(
                wintypes.LPARAM,
                ctypes.c_int,
                wintypes.WPARAM,
                wintypes.LPARAM,
            )
            self._user32.SetWindowsHookExW.argtypes = (
                ctypes.c_int,
                self._keyboard_hook_proc_type,
                wintypes.HINSTANCE,
                wintypes.DWORD,
            )
            self._user32.SetWindowsHookExW.restype = wintypes.HHOOK
            self._user32.CallNextHookEx.argtypes = (
                wintypes.HHOOK,
                ctypes.c_int,
                wintypes.WPARAM,
                wintypes.LPARAM,
            )
            self._user32.CallNextHookEx.restype = wintypes.LPARAM
            self._user32.UnhookWindowsHookEx.argtypes = (wintypes.HHOOK,)
            self._user32.UnhookWindowsHookEx.restype = wintypes.BOOL
            self._user32.GetAsyncKeyState.argtypes = (ctypes.c_int,)
            self._user32.GetAsyncKeyState.restype = ctypes.c_short
            self._user32.keybd_event.argtypes = (
                wintypes.BYTE,
                wintypes.BYTE,
                wintypes.DWORD,
                ULONG_PTR,
            )
            self._user32.keybd_event.restype = None
            self._kernel32.GetModuleHandleW.argtypes = (wintypes.LPCWSTR,)
            self._kernel32.GetModuleHandleW.restype = wintypes.HMODULE

        self._reset_state()

    @property
    def active_display_text(self):
        return self.hotkey_text

    @property
    def is_active(self):
        return bool(self._keyboard_hook_handle)

    @property
    def hotkey_spec(self):
        return HOTKEY_PRESET_MAP[normalize_hotkey_text(self.hotkey_text)]

    def _reset_state(self):
        self._modifier_state = {
            "win": False,
            "ctrl": False,
            "alt": False,
            "shift": False,
        }
        self._combo_active = False

    def set_hotkey_text(self, hotkey_text):
        self.hotkey_text = normalize_hotkey_text(hotkey_text)
        self._reset_state()
        return self.hotkey_text

    def register(self):
        if not IS_WINDOWS:
            return False
        if self._keyboard_hook_handle and self._mouse_hook_handle:
            return True

        module_handle = self._kernel32.GetModuleHandleW(None)
        self._keyboard_proc = self._keyboard_hook_proc_type(self._keyboard_hook_callback)
        self._mouse_proc = self._mouse_hook_proc_type(self._mouse_hook_callback)
        self._keyboard_hook_handle = self._user32.SetWindowsHookExW(
            WH_KEYBOARD_LL,
            self._keyboard_proc,
            module_handle,
            0,
        )
        self._mouse_hook_handle = self._user32.SetWindowsHookExW(
            WH_MOUSE_LL,
            self._mouse_proc,
            module_handle,
            0,
        )
        return bool(self._keyboard_hook_handle and self._mouse_hook_handle)

    def unregister(self):
        if not IS_WINDOWS:
            return

        if self._keyboard_hook_handle:
            self._user32.UnhookWindowsHookEx(self._keyboard_hook_handle)
        if self._mouse_hook_handle:
            self._user32.UnhookWindowsHookEx(self._mouse_hook_handle)
        self._keyboard_hook_handle = None
        self._mouse_hook_handle = None
        self._keyboard_proc = None
        self._mouse_proc = None
        self._reset_state()

    def _call_next_keyboard(self, n_code, w_param, l_param):
        return self._user32.CallNextHookEx(self._keyboard_hook_handle, n_code, w_param, l_param)

    def _call_next_mouse(self, n_code, w_param, l_param):
        return self._user32.CallNextHookEx(self._mouse_hook_handle, n_code, w_param, l_param)

    def _release_windows_modifiers(self):
        self._modifier_state["win"] = False
        for vk_code in (VK_LWIN, VK_RWIN):
            if self._user32.GetAsyncKeyState(vk_code) & 0x8000:
                self._user32.keybd_event(vk_code, 0, KEYEVENTF_KEYUP, 0)

    def _set_modifier_state(self, modifier_name, is_pressed):
        self._modifier_state[modifier_name] = is_pressed
        if not is_pressed and self._combo_active:
            self._combo_active = False

    def _required_modifiers_pressed(self):
        return all(self._modifier_state.get(name, False) for name in self.hotkey_spec.modifiers)

    def _should_handle_launcher_dismiss(self):
        return self.is_launcher_visible() and self.can_dismiss_launcher()

    def _keyboard_hook_callback(self, n_code, w_param, l_param):
        if n_code != HC_ACTION:
            return self._call_next_keyboard(n_code, w_param, l_param)

        key_info = ctypes.cast(l_param, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
        vk_code = int(key_info.vkCode)
        message = int(w_param)

        if vk_code == VK_ESCAPE and self._should_handle_launcher_dismiss():
            if message in (WM_KEYDOWN, WM_SYSKEYDOWN) and self.on_escape is not None:
                self.on_escape()
            return 1

        if vk_code in (VK_LWIN, VK_RWIN):
            self._set_modifier_state("win", message in (WM_KEYDOWN, WM_SYSKEYDOWN))
            return self._call_next_keyboard(n_code, w_param, l_param)

        if vk_code in CONTROL_VKS:
            self._set_modifier_state("ctrl", message in (WM_KEYDOWN, WM_SYSKEYDOWN))
            return self._call_next_keyboard(n_code, w_param, l_param)

        if vk_code in ALT_VKS:
            self._set_modifier_state("alt", message in (WM_KEYDOWN, WM_SYSKEYDOWN))
            return self._call_next_keyboard(n_code, w_param, l_param)

        if vk_code in SHIFT_VKS:
            self._set_modifier_state("shift", message in (WM_KEYDOWN, WM_SYSKEYDOWN))
            return self._call_next_keyboard(n_code, w_param, l_param)

        if vk_code == self.hotkey_spec.trigger_vk and self._required_modifiers_pressed():
            if message in (WM_KEYDOWN, WM_SYSKEYDOWN) and not self._combo_active:
                self._combo_active = True
                if "win" in self.hotkey_spec.modifiers:
                    self._release_windows_modifiers()
                self.on_trigger()
                return 1
            if message in (WM_KEYUP, WM_SYSKEYUP):
                return 1

        return self._call_next_keyboard(n_code, w_param, l_param)

    def _mouse_hook_callback(self, n_code, w_param, l_param):
        if n_code != HC_ACTION:
            return self._call_next_mouse(n_code, w_param, l_param)

        if int(w_param) in MOUSE_DOWN_MESSAGES and self._should_handle_launcher_dismiss():
            mouse_info = ctypes.cast(l_param, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
            x = int(mouse_info.pt.x)
            y = int(mouse_info.pt.y)
            if not self.is_point_inside_launcher(x, y) and self.on_outside_click is not None:
                self.on_outside_click()

        return self._call_next_mouse(n_code, w_param, l_param)
