"""Windows input/clipboard helpers (SendInput, focus, clipboard).

Clipboard-based replace (Ctrl+V) into Windows Sandbox requires clipboard redirection
enabled in the .wsb file (<ClipboardRedirection>Enable</ClipboardRedirection>).
"""
import ctypes
import subprocess
import sys
import time
from ctypes import wintypes
from typing import Optional

import pyautogui

from settings import FORCE_CLICK_BEFORE_TYPE, FOCUS_SETTLE_DELAY

# Prepare ctypes structures for SendInput
if not hasattr(wintypes, "ULONG_PTR"):
    wintypes.ULONG_PTR = ctypes.c_size_t

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
WM_CHAR = 0x0102
VK_BACK = 0x08
VK_RETURN = 0x0D
VK_CONTROL = 0x11
VK_LEFT = 0x25
VK_UP = 0x26
VK_RIGHT = 0x27
VK_DOWN = 0x28
VK_I = 0x49
VK_N = 0x4E
VK_A = 0x41
VK_C = 0x43
VK_SHIFT = 0x10


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", wintypes.ULONG_PTR),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", wintypes.ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class _INPUTunion(ctypes.Union):
    _fields_ = [
        ("mi", MOUSEINPUT),
        ("ki", KEYBDINPUT),
        ("hi", HARDWAREINPUT),
    ]


class INPUT(ctypes.Structure):
    _anonymous_ = ("union",)
    _fields_ = [("type", wintypes.DWORD), ("union", _INPUTunion)]


class GUITHREADINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("hwndActive", wintypes.HWND),
        ("hwndFocus", wintypes.HWND),
        ("hwndCapture", wintypes.HWND),
        ("hwndMenuOwner", wintypes.HWND),
        ("hwndMoveSize", wintypes.HWND),
        ("hwndCaret", wintypes.HWND),
        ("rcCaret", wintypes.RECT),
    ]


GA_ROOT = 2

# RDP / VM client surfaces: PostMessage(WM_CHAR) often succeeds locally but does not
# reach the remote session (Windows Sandbox, mstsc, Hyper-V viewer).
_WM_CHAR_UNRELIABLE_CLASSES = frozenset(
    name.lower()
    for name in (
        "TscShellContainerClass",
        "IHWindowClass",
        "RAIL_WINDOW",
        "VMWindow",
    )
)


def _get_root_hwnd(hwnd: int) -> int:
    if not hwnd:
        return 0
    try:
        root = user32.GetAncestor(hwnd, GA_ROOT)
        return int(root) if root else hwnd
    except Exception:
        return hwnd


def _get_window_text(hwnd: int, buf_size: int = 512) -> str:
    try:
        buf = ctypes.create_unicode_buffer(buf_size)
        if user32.GetWindowTextW(hwnd, buf, buf_size):
            return buf.value
    except Exception:
        pass
    return ""


def _get_window_class(hwnd: int, buf_size: int = 256) -> str:
    try:
        buf = ctypes.create_unicode_buffer(buf_size)
        if user32.GetClassNameW(hwnd, buf, buf_size):
            return buf.value
    except Exception:
        pass
    return ""


def _focus_surface_unreliable_for_wm_char(hwnd: Optional[int]) -> bool:
    """
    True if WM_CHAR posted to the focused HWND will not reach the real text target
    (e.g. Windows Sandbox / RDP guest). In those cases we must use SendInput.
    """
    if not hwnd:
        return False
    to_check = [hwnd, _get_root_hwnd(hwnd)]
    cur = hwnd
    for _ in range(24):
        parent = user32.GetParent(cur)
        if not parent:
            break
        to_check.append(int(parent))
        cur = int(parent)

    for h in to_check:
        if not h:
            continue
        title = _get_window_text(h)
        tl = title.lower()
        if "windows sandbox" in tl or "沙盒" in title:
            return True
        if "remote desktop connection" in tl or "远程桌面" in title:
            return True
        cls = _get_window_class(h).lower()
        if cls in _WM_CHAR_UNRELIABLE_CLASSES:
            return True
    return False


def _get_focus_hwnd() -> Optional[int]:
    """Get the HWND that owns keyboard focus (fallback to foreground window)."""
    info = GUITHREADINFO()
    info.cbSize = ctypes.sizeof(GUITHREADINFO)
    try:
        if user32.GetGUIThreadInfo(0, ctypes.byref(info)):
            return info.hwndFocus or info.hwndActive or user32.GetForegroundWindow()
    except Exception:
        pass
    try:
        return user32.GetForegroundWindow()
    except Exception:
        return None


def _try_post_chars(text: str) -> bool:
    """
    Prefer PostMessage(WM_CHAR) injection to avoid first-character loss in Notepad.
    Falls back to SendInput when code points exceed BMP.
    Skipped for Windows Sandbox / RDP-style surfaces: WM_CHAR does not reach the guest.
    """
    hwnd = _get_focus_hwnd()
    if not hwnd:
        return False
    if _focus_surface_unreliable_for_wm_char(hwnd):
        return False
    ok = True
    for ch in text:
        code = ord(ch)
        if code > 0xFFFF:
            return False
        if user32.PostMessageW(hwnd, WM_CHAR, code, 0) == 0:
            ok = False
    return ok


def _send_input(inputs):
    n = len(inputs)
    arr = (INPUT * n)(*inputs)
    cb = ctypes.sizeof(INPUT)
    sent = user32.SendInput(n, arr, cb)
    if sent != n:
        err = ctypes.get_last_error()
        raise ctypes.WinError(err)


def send_unicode_text_with_newlines(text: str):
    """Send text, using Shift+Enter for newlines (avoids submit in Cursor chat)."""
    if not text:
        return
    parts = text.split("\n")
    for i, part in enumerate(parts):
        if part:
            send_unicode_text(part)
        if i < len(parts) - 1:
            press_shift_enter()


def send_unicode_text(text: str):
    text = text or ""
    if not text:
        return

    if _try_post_chars(text):
        return

    inputs = []
    print("⌨️ 输入文本：", text)
    for ch in text:
        code = ord(ch)
        inputs.append(
            INPUT(
                type=INPUT_KEYBOARD,
                ki=KEYBDINPUT(wVk=0, wScan=code, dwFlags=KEYEVENTF_UNICODE, time=0, dwExtraInfo=0),
            )
        )
        inputs.append(
            INPUT(
                type=INPUT_KEYBOARD,
                ki=KEYBDINPUT(
                    wVk=0, wScan=code, dwFlags=KEYEVENTF_UNICODE | KEYEVENTF_KEYUP, time=0, dwExtraInfo=0
                ),
            )
        )
    _send_input(inputs)


def press_vk(vk_code: int, times: int = 1):
    for _ in range(times):
        down = INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=vk_code, wScan=0, dwFlags=0, time=0, dwExtraInfo=0))
        up = INPUT(
            type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=vk_code, wScan=0, dwFlags=KEYEVENTF_KEYUP, time=0, dwExtraInfo=0)
        )
        _send_input([down, up])


def backspace(n: int):
    if n > 0:
        press_vk(VK_BACK, times=n)


def press_enter():
    press_vk(VK_RETURN, times=1)


def press_shift_enter():
    """Shift+Enter - inserts newline without submitting (Cursor, web inputs)."""
    down_shift = INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=VK_SHIFT, wScan=0, dwFlags=0, time=0, dwExtraInfo=0))
    down_ret = INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=VK_RETURN, wScan=0, dwFlags=0, time=0, dwExtraInfo=0))
    up_ret = INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=VK_RETURN, wScan=0, dwFlags=KEYEVENTF_KEYUP, time=0, dwExtraInfo=0))
    up_shift = INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=VK_SHIFT, wScan=0, dwFlags=KEYEVENTF_KEYUP, time=0, dwExtraInfo=0))
    _send_input([down_shift, down_ret, up_ret, up_shift])


def press_arrow(direction: str):
    """Press arrow key: 'up', 'down', 'left', 'right'."""
    vk_map = {"left": VK_LEFT, "up": VK_UP, "right": VK_RIGHT, "down": VK_DOWN}
    vk = vk_map.get((direction or "").lower())
    if vk is not None:
        press_vk(vk, times=1)


def _find_cursor_window() -> Optional[int]:
    """Find Cursor IDE main window by title (contains 'Cursor'). Returns HWND or None."""
    candidates = []  # (hwnd, title) - prefer main editor (has " - " in title)

    def enum_cb(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        try:
            buf = ctypes.create_unicode_buffer(512)
            if user32.GetWindowTextW(hwnd, buf, 512):
                title = buf.value
                if "cursor" in title.lower():
                    candidates.append((hwnd, title))
        except Exception:
            pass
        return True

    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows(WNDENUMPROC(enum_cb), 0)

    if not candidates:
        return None
    # Prefer main editor window (title like "Cursor - path") over dialogs
    main = next((h for h, t in candidates if " - " in t), None)
    return main if main else candidates[0][0]


SW_RESTORE = 9
SW_SHOW = 5


def _activate_window(hwnd: int) -> bool:
    """
    Bring window to foreground. Uses AttachThreadInput workaround so a background
    process can activate another app's window. Returns True if successful.
    """
    try:
        fg_hwnd = user32.GetForegroundWindow()
        if fg_hwnd == hwnd:
            time.sleep(0.1)
            return True

        attached = False
        if fg_hwnd:
            fg_tid = user32.GetWindowThreadProcessId(fg_hwnd, None)
            my_tid = kernel32.GetCurrentThreadId()
            if fg_tid and fg_tid != my_tid:
                # Attach our thread to foreground thread so we can call SetForegroundWindow
                attached = bool(user32.AttachThreadInput(my_tid, fg_tid, True))

        try:
            if user32.IsIconic(hwnd):
                user32.ShowWindow(hwnd, SW_RESTORE)
            user32.BringWindowToTop(hwnd)
            user32.SetForegroundWindow(hwnd)
            # SwitchToThisWindow can force focus when SetForegroundWindow is restricted (fUnknown=False)
            if hasattr(user32, "SwitchToThisWindow"):
                user32.SwitchToThisWindow(hwnd, False)
        finally:
            if attached and fg_hwnd:
                fg_tid = user32.GetWindowThreadProcessId(fg_hwnd, None)
                my_tid = kernel32.GetCurrentThreadId()
                user32.AttachThreadInput(my_tid, fg_tid, False)

        time.sleep(0.2)
        return True
    except Exception:
        return False


def focus_cursor_and_press_ctrl_i() -> bool:
    """
    Find Cursor IDE window, activate it, then press Ctrl+I.
    Returns True if Cursor was found and activated, False otherwise.
    """
    hwnd = _find_cursor_window()
    if not hwnd:
        return False
    if not _activate_window(hwnd):
        return False
    press_ctrl_i()
    return True


def copy_selection():
    """Press Ctrl+C to copy selected text to clipboard."""
    down_ctrl = INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=VK_CONTROL, wScan=0, dwFlags=0, time=0, dwExtraInfo=0))
    down_c = INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=VK_C, wScan=0, dwFlags=0, time=0, dwExtraInfo=0))
    up_c = INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=VK_C, wScan=0, dwFlags=KEYEVENTF_KEYUP, time=0, dwExtraInfo=0))
    up_ctrl = INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=VK_CONTROL, wScan=0, dwFlags=KEYEVENTF_KEYUP, time=0, dwExtraInfo=0))
    _send_input([down_ctrl, down_c, up_c, up_ctrl])


VK_X = 0x58
VK_Z = 0x5A
VK_Y = 0x59
VK_V = 0x56
VK_TAB = 0x09
VK_ESCAPE = 0x1B
VK_SPACE = 0x20
VK_MENU = 0x12  # Alt
VK_LWIN = 0x5B
VK_DELETE = 0x2E
VK_INSERT = 0x2D
VK_HOME = 0x24
VK_END = 0x23
VK_PRIOR = 0x21  # Page Up
VK_NEXT = 0x22   # Page Down
VK_OEM_1 = 0xBA   # ;:
VK_OEM_PLUS = 0xBB   # =+
VK_OEM_COMMA = 0xBC  # ,<
VK_OEM_MINUS = 0xBD  # -_
VK_OEM_PERIOD = 0xBE # .>
VK_OEM_2 = 0xBF     # /?
VK_OEM_3 = 0xC0     # `~
VK_OEM_4 = 0xDB     # [{
VK_OEM_5 = 0xDC     # \|
VK_OEM_6 = 0xDD     # ]}
VK_OEM_7 = 0xDE     # '"
VK_F1, VK_F2, VK_F3, VK_F4, VK_F5 = 0x70, 0x71, 0x72, 0x73, 0x74
VK_F6, VK_F7, VK_F8, VK_F9, VK_F10 = 0x75, 0x76, 0x77, 0x78, 0x79
VK_F11, VK_F12 = 0x7A, 0x7B
VK_CAPITAL = 0x14  # Caps Lock

# Map key names (lowercase) to VK codes for press_key_combo
_VK_MAP = {
    "backspace": VK_BACK, "tab": VK_TAB, "enter": VK_RETURN, "return": VK_RETURN,
    "capslock": VK_CAPITAL, "caps": VK_CAPITAL,
    "escape": VK_ESCAPE, "esc": VK_ESCAPE, "space": VK_SPACE,
    "delete": VK_DELETE, "del": VK_DELETE, "insert": VK_INSERT, "ins": VK_INSERT,
    "home": VK_HOME, "end": VK_END, "pageup": VK_PRIOR, "pagedown": VK_NEXT,
    "up": VK_UP, "down": VK_DOWN, "left": VK_LEFT, "right": VK_RIGHT,
    "f1": VK_F1, "f2": VK_F2, "f3": VK_F3, "f4": VK_F4, "f5": VK_F5,
    "f6": VK_F6, "f7": VK_F7, "f8": VK_F8, "f9": VK_F9, "f10": VK_F10,
    "f11": VK_F11, "f12": VK_F12,
    "semicolon": VK_OEM_1, "equals": VK_OEM_PLUS, "comma": VK_OEM_COMMA,
    "minus": VK_OEM_MINUS, "period": VK_OEM_PERIOD, "slash": VK_OEM_2,
    "backquote": VK_OEM_3, "backtick": VK_OEM_3, "openbracket": VK_OEM_4,
    "backslash": VK_OEM_5, "closebracket": VK_OEM_6, "quote": VK_OEM_7,
}
# Character-to-VK for symbols (so shift+; etc. works)
for ch, vk in (
    (";", VK_OEM_1), ("=", VK_OEM_PLUS), (",", VK_OEM_COMMA), ("-", VK_OEM_MINUS),
    (".", VK_OEM_PERIOD), ("/", VK_OEM_2), ("`", VK_OEM_3), ("[", VK_OEM_4),
    ("\\", VK_OEM_5), ("]", VK_OEM_6), ("'", VK_OEM_7),
):
    _VK_MAP[ch] = vk
for c in "abcdefghijklmnopqrstuvwxyz":
    _VK_MAP[c] = 0x41 + ord(c) - ord("a")
for c in "0123456789":
    _VK_MAP[c] = 0x30 + ord(c) - ord("0")


def press_key_combo(key_str: str) -> bool:
    """
    Parse and send a key combination. Examples:
      "a" -> send_unicode_text("a")
      "ctrl+a", "ctrl+c", "ctrl+v" -> modifier + key
      "tab", "enter", "escape" -> special key
      "ctrl+shift+z" -> multiple modifiers
    Returns True on success.
    """
    key_str = (key_str or "").strip().lower()
    if not key_str:
        return False
    parts = key_str.replace(" ", "").split("+")
    modifiers = []
    key_part = None
    for p in parts:
        if p in ("ctrl", "control"):
            modifiers.append(("ctrl", VK_CONTROL))
        elif p in ("alt"):
            modifiers.append(("alt", VK_MENU))
        elif p in ("shift"):
            modifiers.append(("shift", VK_SHIFT))
        elif p in ("meta", "win", "super"):
            modifiers.append(("win", VK_LWIN))
        else:
            key_part = p
    if not key_part:
        return False
    # Single character, no modifiers -> use send_unicode_text for better IME support
    if len(key_part) == 1 and not modifiers:
        ch = key_part
        if ch in "abcdefghijklmnopqrstuvwxyz0123456789" or ch in " !@#$%^&*()_+-=[]{}|;':\",./<>?`~":
            send_unicode_text(ch)
            return True
    vk = _VK_MAP.get(key_part)
    if vk is None and len(key_part) == 1:
        send_unicode_text(key_part)
        return True
    if vk is None:
        return False
    focus_target()
    inputs = []
    for _name, mod_vk in modifiers:
        inputs.append(INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=mod_vk, wScan=0, dwFlags=0, time=0, dwExtraInfo=0)))
    inputs.append(INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=vk, wScan=0, dwFlags=0, time=0, dwExtraInfo=0)))
    inputs.append(INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=vk, wScan=0, dwFlags=KEYEVENTF_KEYUP, time=0, dwExtraInfo=0)))
    for _name, mod_vk in reversed(modifiers):
        inputs.append(INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=mod_vk, wScan=0, dwFlags=KEYEVENTF_KEYUP, time=0, dwExtraInfo=0)))
    _send_input(inputs)
    return True


def press_ctrl_v():
    """Press Ctrl+V to paste from clipboard."""
    down_ctrl = INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=VK_CONTROL, wScan=0, dwFlags=0, time=0, dwExtraInfo=0))
    down_v = INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=VK_V, wScan=0, dwFlags=0, time=0, dwExtraInfo=0))
    up_v = INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=VK_V, wScan=0, dwFlags=KEYEVENTF_KEYUP, time=0, dwExtraInfo=0))
    up_ctrl = INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=VK_CONTROL, wScan=0, dwFlags=KEYEVENTF_KEYUP, time=0, dwExtraInfo=0))
    _send_input([down_ctrl, down_v, up_v, up_ctrl])


def press_ctrl_x():
    """Press Ctrl+X to cut selected text to clipboard."""
    down_ctrl = INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=VK_CONTROL, wScan=0, dwFlags=0, time=0, dwExtraInfo=0))
    down_x = INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=VK_X, wScan=0, dwFlags=0, time=0, dwExtraInfo=0))
    up_x = INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=VK_X, wScan=0, dwFlags=KEYEVENTF_KEYUP, time=0, dwExtraInfo=0))
    up_ctrl = INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=VK_CONTROL, wScan=0, dwFlags=KEYEVENTF_KEYUP, time=0, dwExtraInfo=0))
    _send_input([down_ctrl, down_x, up_x, up_ctrl])


def press_ctrl_z():
    """Press Ctrl+Z to undo."""
    down_ctrl = INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=VK_CONTROL, wScan=0, dwFlags=0, time=0, dwExtraInfo=0))
    down_z = INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=VK_Z, wScan=0, dwFlags=0, time=0, dwExtraInfo=0))
    up_z = INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=VK_Z, wScan=0, dwFlags=KEYEVENTF_KEYUP, time=0, dwExtraInfo=0))
    up_ctrl = INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=VK_CONTROL, wScan=0, dwFlags=KEYEVENTF_KEYUP, time=0, dwExtraInfo=0))
    _send_input([down_ctrl, down_z, up_z, up_ctrl])


def press_ctrl_y():
    """Press Ctrl+Y to redo (Windows convention)."""
    down_ctrl = INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=VK_CONTROL, wScan=0, dwFlags=0, time=0, dwExtraInfo=0))
    down_y = INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=VK_Y, wScan=0, dwFlags=0, time=0, dwExtraInfo=0))
    up_y = INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=VK_Y, wScan=0, dwFlags=KEYEVENTF_KEYUP, time=0, dwExtraInfo=0))
    up_ctrl = INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=VK_CONTROL, wScan=0, dwFlags=KEYEVENTF_KEYUP, time=0, dwExtraInfo=0))
    _send_input([down_ctrl, down_y, up_y, up_ctrl])


def select_all():
    """Press Ctrl+A to select all text in focused input."""
    down_ctrl = INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=VK_CONTROL, wScan=0, dwFlags=0, time=0, dwExtraInfo=0))
    down_a = INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=VK_A, wScan=0, dwFlags=0, time=0, dwExtraInfo=0))
    up_a = INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=VK_A, wScan=0, dwFlags=KEYEVENTF_KEYUP, time=0, dwExtraInfo=0))
    up_ctrl = INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=VK_CONTROL, wScan=0, dwFlags=KEYEVENTF_KEYUP, time=0, dwExtraInfo=0))
    _send_input([down_ctrl, down_a, up_a, up_ctrl])


def press_ctrl_i():
    """Press Ctrl+I to focus Cursor IDE agent input box."""
    down_ctrl = INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=VK_CONTROL, wScan=0, dwFlags=0, time=0, dwExtraInfo=0))
    down_i = INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=VK_I, wScan=0, dwFlags=0, time=0, dwExtraInfo=0))
    up_i = INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=VK_I, wScan=0, dwFlags=KEYEVENTF_KEYUP, time=0, dwExtraInfo=0))
    up_ctrl = INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=VK_CONTROL, wScan=0, dwFlags=KEYEVENTF_KEYUP, time=0, dwExtraInfo=0))
    _send_input([down_ctrl, down_i, up_i, up_ctrl])


def press_ctrl_n():
    """Press Ctrl+N to create new Agent in Cursor IDE."""
    down_ctrl = INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=VK_CONTROL, wScan=0, dwFlags=0, time=0, dwExtraInfo=0))
    down_n = INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=VK_N, wScan=0, dwFlags=0, time=0, dwExtraInfo=0))
    up_n = INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=VK_N, wScan=0, dwFlags=KEYEVENTF_KEYUP, time=0, dwExtraInfo=0))
    up_ctrl = INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=VK_CONTROL, wScan=0, dwFlags=KEYEVENTF_KEYUP, time=0, dwExtraInfo=0))
    _send_input([down_ctrl, down_n, up_n, up_ctrl])


def focus_cursor_and_press_ctrl_n() -> bool:
    """
    Find Cursor IDE window, activate it, then press Ctrl+N (new Agent).
    Returns True if Cursor was found and activated, False otherwise.
    """
    hwnd = _find_cursor_window()
    if not hwnd:
        return False
    if not _activate_window(hwnd):
        return False
    press_ctrl_n()
    return True


_LAST_FG_HWND = None


def focus_target():
    """Optionally click current mouse position once to ensure focus."""
    global _LAST_FG_HWND
    if not FORCE_CLICK_BEFORE_TYPE:
        return

    try:
        current_hwnd = user32.GetForegroundWindow()
    except Exception:
        current_hwnd = None

    if current_hwnd and current_hwnd == _LAST_FG_HWND:
        return

    try:
        x, y = pyautogui.position()
        pyautogui.click(x, y)
        time.sleep(FOCUS_SETTLE_DELAY)
    except Exception:
        pass
    finally:
        try:
            _LAST_FG_HWND = user32.GetForegroundWindow()
        except Exception:
            _LAST_FG_HWND = current_hwnd


def move_mouse(x: int, y: int):
    """Move mouse to absolute screen coordinates (virtual screen)."""
    pyautogui.moveTo(x, y)


def move_mouse_rel(dx: int, dy: int):
    """Move mouse by relative offset (for trackpad-style fine adjustment)."""
    pyautogui.move(dx, dy)


def left_click(x: Optional[int] = None, y: Optional[int] = None):
    """Left click at (x, y) or current position. Coordinates are virtual screen."""
    if x is not None and y is not None:
        pyautogui.click(x, y)
    else:
        pyautogui.click()


def right_click(x: Optional[int] = None, y: Optional[int] = None):
    """Right click at (x, y) or current position. Coordinates are virtual screen."""
    if x is not None and y is not None:
        pyautogui.rightClick(x, y)
    else:
        pyautogui.rightClick()


def scroll_mouse(delta: int, x: Optional[int] = None, y: Optional[int] = None):
    """Scroll: positive = up, negative = down. At (x,y) or current position."""
    if x is not None and y is not None:
        pyautogui.scroll(delta, x=x, y=y)
    else:
        pyautogui.scroll(delta)


def read_target_input_content() -> tuple[str, str | None]:
    """Read content from focused input: select all, copy, return (text, html).
    html is CF_HTML format if available (for preserving @mentions/refs when pasting back).
    Caller should ensure target has focus (e.g. user clicked it)."""
    select_all()
    time.sleep(0.05)
    copy_selection()
    time.sleep(0.08)
    text = get_clipboard_text()
    html = get_clipboard_html()
    return (text or "", html)


def get_clipboard_html() -> str | None:
    """Read CF_HTML from clipboard if available. Returns None if not present."""
    CF_HTML = _get_cf_html()
    if not CF_HTML:
        return None
    for _ in range(3):
        opened = user32.OpenClipboard(None)
        if not opened:
            time.sleep(0.03)
            continue
        try:
            if user32.IsClipboardFormatAvailable(CF_HTML):
                handle = user32.GetClipboardData(CF_HTML)
                if handle:
                    size = kernel32.GlobalSize(handle)
                    ptr = kernel32.GlobalLock(handle)
                    if ptr and size:
                        try:
                            raw = ctypes.string_at(ptr, size)
                            return raw.decode("utf-8", errors="replace").rstrip("\x00")
                        finally:
                            kernel32.GlobalUnlock(handle)
            return None
        except Exception:
            pass
        finally:
            try:
                user32.CloseClipboard()
            except Exception:
                pass
        time.sleep(0.03)
    return None


def _get_cf_html() -> int | None:
    """Get clipboard format ID for HTML Format."""
    try:
        fmt = user32.RegisterClipboardFormatW("HTML Format")
        return fmt if fmt else None
    except Exception:
        return None


def set_clipboard_text(text: str, html: str | None = None) -> bool:
    """Set clipboard with plain text. If html is provided, also set CF_HTML for rich paste.
    Returns True on success."""
    text = text or ""
    for _ in range(5):
        opened = user32.OpenClipboard(None)
        if not opened:
            time.sleep(0.05)
            continue
        try:
            user32.EmptyClipboard()
            if text:
                data = text.encode("utf-16-le") + b"\x00\x00"
                size = len(data)
                GMEM_MOVEABLE = 0x0002
                h = kernel32.GlobalAlloc(GMEM_MOVEABLE, size)
                if h:
                    ptr = kernel32.GlobalLock(h)
                    if ptr:
                        ctypes.memmove(ptr, data, size)
                        kernel32.GlobalUnlock(h)
                        CF_UNICODETEXT = 13
                        user32.SetClipboardData(CF_UNICODETEXT, h)
            if html and _get_cf_html():
                data = html.encode("utf-8")
                size = len(data)
                GMEM_MOVEABLE = 0x0002
                h = kernel32.GlobalAlloc(GMEM_MOVEABLE, size)
                if h:
                    ptr = kernel32.GlobalLock(h)
                    if ptr:
                        ctypes.memmove(ptr, data, size)
                        kernel32.GlobalUnlock(h)
                        user32.SetClipboardData(_get_cf_html(), h)
            return True
        except Exception:
            pass
        finally:
            try:
                user32.CloseClipboard()
            except Exception:
                pass
        time.sleep(0.05)
    try:
        import tempfile
        import os
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", suffix=".txt", delete=False
        ) as f:
            f.write(text)
            path = f.name
        try:
            subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    f'Get-Content -LiteralPath "{path}" -Raw -Encoding UTF8 | Set-Clipboard',
                ],
                check=True,
                timeout=5,
                capture_output=True,
            )
            return True
        finally:
            try:
                os.unlink(path)
            except Exception:
                pass
    except Exception:
        pass
    return False


def get_clipboard_text() -> str:
    """Best-effort clipboard read with retries and a PowerShell fallback."""
    CF_UNICODETEXT = 13
    CF_TEXT = 1

    def _read_handle(handle, is_unicode=False):
        if not handle:
            return "", 0
        size = kernel32.GlobalSize(handle)
        ptr = kernel32.GlobalLock(handle)
        if not ptr:
            return "", size
        try:
            if size:
                raw = ctypes.string_at(ptr, size)
            else:
                raw = ctypes.string_at(ptr)  # some apps expose readable data with reported size 0
        finally:
            kernel32.GlobalUnlock(handle)

        if is_unicode:
            try:
                text = raw.decode("utf-16-le").rstrip("\x00")
                return text, size if size else len(text) * 2
            except Exception:
                return "", size
        else:
            for enc in ("utf-8", "gbk", sys.getdefaultencoding()):
                try:
                    text = raw.decode(enc).rstrip("\x00")
                    return text, size if size else len(text)
                except Exception:
                    continue
            return raw.decode("utf-8", errors="ignore").rstrip("\x00"), size

    for _ in range(5):
        opened = user32.OpenClipboard(None)
        if not opened:
            time.sleep(0.05)
            continue
        try:
            if user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
                txt, _size = _read_handle(user32.GetClipboardData(CF_UNICODETEXT), is_unicode=True)
                if txt:
                    return txt
            elif user32.IsClipboardFormatAvailable(CF_TEXT):
                txt, _size = _read_handle(user32.GetClipboardData(CF_TEXT), is_unicode=False)
                if txt:
                    return txt
            else:
                return ""
        except Exception:
            pass
        finally:
            try:
                user32.CloseClipboard()
            except Exception:
                pass
        time.sleep(0.05)

    try:
        out = subprocess.check_output(
            ["powershell", "-NoProfile", "-Command", "Get-Clipboard -Raw"],
            text=True,
            stderr=subprocess.STDOUT,
            timeout=3,
        )
        if out:
            return out
    except Exception:
        pass
    return ""
