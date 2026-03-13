"""Voice command handling: LLM analyzes intent and executes the plan."""
import json
import webbrowser
from typing import Callable

import config_store
from i18n import _
import time
from input_control import (
    copy_selection,
    focus_target,
    get_clipboard_html,
    get_clipboard_text,
    press_ctrl_v,
    press_ctrl_x,
    press_ctrl_y,
    press_ctrl_z,
    press_key_combo,
    read_target_input_content,
    select_all,
    set_clipboard_text,
)
from llm_assistant import analyze_command_plan, modify_text_via_llm
from text_handler import handle_text_replace, server_dedup


def _run_browser(url: str = None, search_query: str = None) -> dict:
    """Open browser with URL or search query."""
    if url:
        u = (url or "").strip()
        if not u.startswith(("http://", "https://")):
            u = "https://" + u
        try:
            webbrowser.open(u)
            return {"ok": True, "message": _("Browser opened") + f": {u}"}
        except Exception as e:
            return {"ok": False, "message": _("Failed to open browser") + f": {e}"}
    if search_query:
        q = (search_query or "").strip()
        if q:
            encoded = __import__("urllib.parse").quote(q)
            u = f"https://www.google.com/search?q={encoded}"
            try:
                webbrowser.open(u)
                return {"ok": True, "message": _("Browser search") + f": {q}"}
            except Exception as e:
                return {"ok": False, "message": _("Failed to open browser") + f": {e}"}
    return {"ok": False, "message": _("No URL or search query specified")}


MODIFY_TEXT_MAX_CHARS = 5000

# App-level buffer for voice copy/cut: paste uses this when available
_voice_copied_text: str = ""
_voice_copied_html: str | None = None


def _store_voice_copy() -> None:
    """Read clipboard and store in app buffer for later paste."""
    global _voice_copied_text, _voice_copied_html
    time.sleep(0.08)  # allow clipboard to update
    _voice_copied_text = get_clipboard_text() or ""
    _voice_copied_html = get_clipboard_html()


def handle_command_with_llm(
    raw_text: str,
    send_progress: Callable[[dict], None],
    send_result: Callable[[dict], None],
) -> None:
    """
    Handle command mode: LLM analyzes sentence -> generate execution plan -> execute.
    Supports:
    1. modify_text - Edit text in focused input (e.g. "change xx to xxx")
    2. edit_select_all - Select all text (e.g. "全选", "select all")
    3. edit_select_all_copy - Select all and copy (e.g. "全选后复制")
    4. edit_copy - Copy selection (e.g. "复制", "copy")
    5. edit_paste - Paste from clipboard (e.g. "粘贴", "paste")
    6. edit_cut - Cut selection (e.g. "剪切", "cut")
    7. edit_undo - Undo (e.g. "撤销", "undo")
    8. edit_redo - Redo (e.g. "重做", "redo")
    9. edit_clear - Clear all text (e.g. "清空", "clear")
    10. open_browser - Open browser (e.g. "open browser search xxx")
    """
    text = (raw_text or "").strip()
    if not text:
        return

    print(f"[cmd] received: {text}")

    if server_dedup(text, "cmd"):
        print("[cmd] dedup skip")
        return

    if not config_store.LLM_ENABLED:
        send_progress({"type": "cmd_progress", "step": "error", "message": _("LLM required for command mode")})
        send_result({"type": "cmd_result", "string": text, "ok": False, "message": _("LLM required for command mode")})
        return

    def on_progress(step: str, msg: str):
        print(f"[cmd] {step}: {msg}")
        send_progress({"type": "cmd_progress", "step": step, "message": msg})

    def on_stream(accumulated: str):
        if accumulated:
            send_progress({"type": "cmd_progress", "step": "llm_stream", "message": accumulated})

    on_progress("llm_judging", _("Using LLM to analyze..."))

    try:
        plan = analyze_command_plan(
            text,
            model=config_store.LLM_MODEL,
            base_url=config_store.LLM_BASE_URL,
            on_stream=on_stream,
        )
        on_progress("llm_plan", json.dumps(plan, ensure_ascii=False, indent=2))
    except Exception as e:
        print(f"[cmd] LLM error: {e}")
        on_progress("error", str(e))
        send_result({"type": "cmd_result", "string": text, "ok": False, "message": _("LLM error: {e}").format(e=e)})
        return

    # Support compound commands: "steps" array or single "intent"
    steps = plan.get("steps")
    if steps and isinstance(steps, list) and len(steps) > 0:
        steps_to_run = steps
        print(f"[cmd] compound steps: {[s.get('intent') for s in steps_to_run]}")
    else:
        steps_to_run = [plan]
        print(f"[cmd] single intent: {plan.get('intent')}")

    last_message = ""
    for i, step in enumerate(steps_to_run):
        intent = (step.get("intent") or "unknown").strip().lower()
        ok, msg = _execute_single_step(step, text, intent, on_progress, on_stream)
        if not ok:
            send_result({"type": "cmd_result", "string": text, "ok": False, "message": msg})
            return
        last_message = msg
        if i < len(steps_to_run) - 1:
            time.sleep(0.05)  # brief pause between steps

    send_result({"type": "cmd_result", "string": text, "ok": True, "message": last_message or _("Done")})


def _execute_single_step(step, text, intent, on_progress, on_stream) -> tuple[bool, str]:
    """Execute one step. Returns (ok, message)."""
    plan = step  # step has intent and params

    # --- modify_text ---
    if intent == "modify_text":
        instruction = plan.get("instruction") or text
        on_progress("reading", _("Reading input..."))
        try:
            input_text, input_html = read_target_input_content()
        except Exception as e:
            print(f"[cmd] read_target error: {e}")
            return (False, _("Failed to read input: {e}").format(e=e))

        input_text = (input_text or "").strip()
        if not input_text:
            return (False, _("Input box is empty, cannot modify"))

        if len(input_text) > MODIFY_TEXT_MAX_CHARS:
            return (False, _("Edit box text too long, cannot execute"))

        on_progress("modifying", _("Modifying text..."))

        def on_rule_parsed(raw_content: str, rule: dict):
            on_progress("llm_rule", json.dumps(rule, ensure_ascii=False, indent=2))

        try:
            modified, err_reason = modify_text_via_llm(
                instruction=instruction,
                input_text=input_text,
                model=config_store.LLM_MODEL,
                base_url=config_store.LLM_BASE_URL,
                on_stream=on_stream,
                on_rule_parsed=on_rule_parsed,
            )
        except Exception as e:
            print(f"[cmd] modify_text LLM error: {e}")
            return (False, _("LLM error: {e}").format(e=e))

        if modified is None:
            if err_reason == "unsupported":
                return (False, _("Modification type not supported (input not sent to LLM)"))
            return (False, _("Failed to modify text"))

        focus_target()
        handle_text_replace(modified, last_sync_text=input_text, last_sync_html=input_html)
        return (True, _("Text modified"))

    # --- edit_select_all ---
    if intent == "edit_select_all":
        focus_target()
        select_all()
        return (True, _("Select all"))

    # --- edit_select_all_copy ---
    if intent == "edit_select_all_copy":
        focus_target()
        select_all()
        time.sleep(0.05)
        copy_selection()
        _store_voice_copy()
        return (True, _("Select all and copy"))

    # --- edit_copy ---
    if intent == "edit_copy":
        focus_target()
        copy_selection()
        _store_voice_copy()
        return (True, _("Copy"))

    # --- edit_paste ---
    if intent == "edit_paste":
        focus_target()
        if _voice_copied_text:
            set_clipboard_text(_voice_copied_text, _voice_copied_html)
        press_ctrl_v()
        return (True, _("Paste"))

    # --- edit_cut ---
    if intent == "edit_cut":
        focus_target()
        press_ctrl_x()
        _store_voice_copy()
        return (True, _("Cut"))

    # --- edit_undo ---
    if intent == "edit_undo":
        focus_target()
        press_ctrl_z()
        return (True, _("Undo"))

    # --- edit_redo ---
    if intent == "edit_redo":
        focus_target()
        press_ctrl_y()
        return (True, _("Redo"))

    # --- edit_clear ---
    if intent == "edit_clear":
        focus_target()
        select_all()
        time.sleep(0.03)
        press_key_combo("delete")
        return (True, _("Clear"))

    # --- open_browser ---
    if intent == "open_browser":
        url = plan.get("url", "").strip()
        search_query = plan.get("search_query", "").strip()
        on_progress("executing", _("Opening browser..."))
        result = _run_browser(url=url if url else None, search_query=search_query if search_query else None)
        return (result.get("ok", False), result.get("message", ""))

    # --- unknown ---
    return (False, _("No command matched"))
