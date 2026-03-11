"""High-level text handling and deduplication."""
import time
from typing import Optional

from i18n import _
from commands import CommandResult, processor
from input_control import (
    backspace,
    focus_target,
    press_enter,
    press_ctrl_v,
    select_all,
    send_unicode_text,
    send_unicode_text_with_newlines,
    set_clipboard_text,
)
from notifier import notify
from settings import SERVER_DEDUP_WINDOW_SEC, TEST_INJECT_TEXT

_last_msg = ""
_last_time = 0.0
_last_mode = ""


def server_dedup(text: str, mode: str = "text") -> bool:
    """Drop duplicate messages within a short window."""
    global _last_msg, _last_time, _last_mode
    now = time.time()
    if text == _last_msg and mode == _last_mode and (now - _last_time) < SERVER_DEDUP_WINDOW_SEC:
        return True
    _last_msg = text
    _last_mode = mode
    _last_time = now
    return False


def execute_output(out):
    if out == "":
        return
    if isinstance(out, tuple):
        if out[0] == "__BACKSPACE__":
            backspace(int(out[1]))
            return
        if out[0] == "__ENTER__":
            press_enter()
            return
    if isinstance(out, str):
        send_unicode_text(out)


def handle_text_replace(text: str, last_sync_text: str | None = None, last_sync_html: str | None = None):
    """Replace target input content via clipboard paste (Ctrl+A, Ctrl+V).
    Uses last_sync_html when content unchanged to preserve @mentions and HTML refs."""
    text = text or ""
    if server_dedup(text, "text_sync"):
        return
    if processor.paused:
        notify(_("Command execution"), _("Paused - sync"))
        return
    focus_target()
    # Use HTML when content unchanged to preserve Cursor @mentions and refs
    use_html = (
        last_sync_html
        and last_sync_text is not None
        and text == last_sync_text
    )
    ok = set_clipboard_text(text, html=last_sync_html if use_html else None)
    if not ok:
        # Fallback to typing if clipboard fails
        select_all()
        if text:
            send_unicode_text_with_newlines(text)
        else:
            backspace(1)
        processor.record_output(text)
        return
    select_all()
    time.sleep(0.03)
    press_ctrl_v()
    processor.record_output(text)


def handle_text(
    text: str,
    mode: str = "text",
    replace: bool = False,
    last_sync_text: str | None = None,
    last_sync_html: str | None = None,
):
    text = text or ""
    text_stripped = text.strip()
    if not replace and not text_stripped and "\n" not in text:
        return

    mode = (mode or "text").strip() or "text"

    if server_dedup(text, mode):
        print(f"⏭️ 服务器去重({mode})：", text)
        return

    if text == "__TEST_INJECT__":
        notify(_("Test inject"), _("Place cursor in Notepad, injecting test text..."))
        focus_target()
        try:
            send_unicode_text(TEST_INJECT_TEXT)
            press_enter()
            send_unicode_text("✅ " + _("If you see this, SendInput succeeded!"))
            press_enter()
            notify(_("Test inject success"), _("Check if two lines appear in Notepad"))
        except Exception as e:
            notify(_("Test inject failed"), str(e))
        return

    if mode != "cmd":
        if replace:
            handle_text_replace(text, last_sync_text, last_sync_html)
            return
        if processor.paused:
            notify(_("Command execution"), _("Paused - {text}").format(text=text))
            return
        focus_target()
        execute_output(text)
        processor.record_output(text)
        return

    result: CommandResult = processor.handle(text)
    if result.output == "":
        notify("指令执行", result.display_text)
        return

    focus_target()
    execute_output(result.output)

    if not result.handled and isinstance(result.output, str):
        processor.record_output(result.output)
