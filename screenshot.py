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


def capture_screenshot() -> Optional[Tuple[str, Tuple[int, int, int, int]]]:
    """
    Capture all screens and return (base64_png_str, bounds).
    bounds = (left, top, width, height) for mapping click coordinates.
    Uses image dimensions for width/height to ensure correct coordinate mapping.
    """
    try:
        img = ImageGrab.grab(all_screens=True)
        if img is None:
            return None
        left, top, _, _ = get_virtual_screen_bounds()
        w, h = img.size
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode("ascii"), (left, top, w, h)
    except Exception:
        return None
