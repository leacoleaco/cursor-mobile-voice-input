"""Windows input/clipboard helpers (SendInput, focus, clipboard)."""
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
    """
    hwnd = _get_focus_hwnd()
    if not hwnd:
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


def read_target_input_content() -> str:
    """Read content from focused input: select all, copy, return clipboard text.
    Caller should ensure target has focus (e.g. user clicked it)."""
    select_all()
    time.sleep(0.05)
    copy_selection()
    time.sleep(0.08)
    return get_clipboard_text()


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
