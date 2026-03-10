"""Screen capture for all monitors (Windows virtual screen)."""
import base64
import ctypes
import io
from typing import Optional, Tuple

from PIL import ImageGrab

user32 = ctypes.windll.user32

SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79


def get_virtual_screen_bounds() -> Tuple[int, int, int, int]:
    """Return (left, top, width, height) of the virtual screen (all monitors)."""
    left = user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
    top = user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
    width = user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)
    height = user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)
    return left, top, width, height


def capture_screenshot() -> Optional[Tuple[str, dict]]:
    """
    Capture all screens and return (base64_png_str, bounds_dict).
    bounds_dict: left, top, width, height (image size), logicalWidth, logicalHeight
    (from GetSystemMetrics - matches pyautogui/GetCursorPos coordinate system).
    When image size differs from logical (DPI scaling), coordinates must be scaled.
    """
    try:
        img = ImageGrab.grab(all_screens=True)
        if img is None:
            return None
        left, top, logical_w, logical_h = get_virtual_screen_bounds()
        img_w, img_h = img.size
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        bounds = {
            "left": left,
            "top": top,
            "width": img_w,
            "height": img_h,
            "logicalWidth": logical_w,
            "logicalHeight": logical_h,
        }
        return base64.b64encode(buf.getvalue()).decode("ascii"), bounds
    except Exception:
        return None
