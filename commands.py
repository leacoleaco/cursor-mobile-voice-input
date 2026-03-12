"""Voice command handling: LLM analyzes intent and executes the plan."""
import shlex
import subprocess
import webbrowser
from typing import Callable, List

import config_store
from i18n import _
from input_control import focus_target, read_target_input_content
from llm_assistant import analyze_command_plan, modify_text_via_llm
from text_handler import handle_text_replace, server_dedup


def _run_config_command(match_string: str) -> dict:
    """Execute a config command by match-string. Returns {ok, message}."""
    for cmd in config_store.COMMANDS:
        ms = (cmd.get("match-string") or "").strip()
        if ms == match_string:
            command = cmd.get("command")
            args = cmd.get("args") or []
            if isinstance(command, str) and command.strip():
                parts = shlex.split(command, posix=False)
            elif isinstance(command, list):
                parts = [str(x) for x in command if str(x).strip()]
            else:
                return {"ok": False, "message": _("Command config error")}
            parts.extend([str(x) for x in args if str(x).strip()])
            if not parts:
                return {"ok": False, "message": _("Command config error")}
            try:
                completed = subprocess.run(parts, capture_output=True, text=True, timeout=60)
                ok = completed.returncode == 0
                stderr = (completed.stderr or "").strip()
                msg = (_("Command executed successfully") if ok else _("Command execution failed")) + f": {match_string}"
                if not ok:
                    msg += f" (exit {completed.returncode})"
                if stderr:
                    msg = f"{msg} - {stderr}"
                return {"ok": ok, "message": msg}
            except subprocess.TimeoutExpired:
                return {"ok": False, "message": _("Command execution timeout") + f": {match_string}"}
            except Exception as e:
                return {"ok": False, "message": _("Command execution error") + f": {match_string} - {e}"}
    return {"ok": False, "message": _("Command not found") + f": {match_string}"}


def _run_app(app_path: str, app_args: List[str]) -> dict:
    """Open application. app_path can be name (notepad, calc) or full path."""
    path = (app_path or "").strip()
    if not path:
        return {"ok": False, "message": _("No application specified")}
    args = [str(x) for x in (app_args or []) if str(x).strip()]
    parts = [path] + args
    try:
        subprocess.Popen(parts, shell=False)
        return {"ok": True, "message": _("Application opened") + f": {path}"}
    except FileNotFoundError:
        try:
            subprocess.Popen(parts, shell=True)
            return {"ok": True, "message": _("Application opened") + f": {path}"}
        except Exception as e:
            return {"ok": False, "message": _("Failed to open application") + f": {e}"}
    except Exception as e:
        return {"ok": False, "message": _("Failed to open application") + f": {e}"}


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


def handle_command_with_llm(
    raw_text: str,
    send_progress: Callable[[dict], None],
    send_result: Callable[[dict], None],
) -> None:
    """
    Handle command mode: LLM analyzes sentence -> generate execution plan -> execute.
    Supports:
    1. modify_text - Edit text in focused input (e.g. "change xx to xxx")
    2. open_app - Open software and optionally execute (e.g. "open notepad")
    3. open_browser - Open browser and execute (e.g. "open browser search xxx")
    4. execute_config - Run configured command from config
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

    config_match_strings = [
        (c.get("match-string") or "").strip()
        for c in config_store.COMMANDS
        if (c.get("match-string") or "").strip()
    ]

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
            config_match_strings,
            model=config_store.LLM_MODEL,
            base_url=config_store.LLM_BASE_URL,
            on_stream=on_stream,
        )
    except Exception as e:
        print(f"[cmd] LLM error: {e}")
        on_progress("error", str(e))
        send_result({"type": "cmd_result", "string": text, "ok": False, "message": _("LLM error: {e}").format(e=e)})
        return

    intent = (plan.get("intent") or "unknown").strip().lower()
    print(f"[cmd] intent: {intent}")

    # --- modify_text ---
    if intent == "modify_text":
        instruction = plan.get("instruction") or text
        on_progress("reading", _("Reading input..."))
        try:
            input_text, input_html = read_target_input_content()
        except Exception as e:
            print(f"[cmd] read_target error: {e}")
            send_result({"type": "cmd_result", "string": text, "ok": False, "message": _("Failed to read input: {e}").format(e=e)})
            return

        input_text = (input_text or "").strip()
        if not input_text:
            send_result({"type": "cmd_result", "string": text, "ok": False, "message": _("Input box is empty, cannot modify")})
            return

        if len(input_text) > MODIFY_TEXT_MAX_CHARS:
            send_result({"type": "cmd_result", "string": text, "ok": False, "message": _("Edit box text too long, cannot execute")})
            return

        on_progress("modifying", _("Modifying text..."))
        try:
            modified = modify_text_via_llm(
                instruction=instruction,
                input_text=input_text,
                model=config_store.LLM_MODEL,
                base_url=config_store.LLM_BASE_URL,
                on_stream=on_stream,
            )
        except Exception as e:
            print(f"[cmd] modify_text LLM error: {e}")
            send_result({"type": "cmd_result", "string": text, "ok": False, "message": _("LLM error: {e}").format(e=e)})
            return

        if modified is None:
            send_result({"type": "cmd_result", "string": text, "ok": False, "message": _("Failed to modify text")})
            return

        focus_target()
        handle_text_replace(modified, last_sync_text=input_text, last_sync_html=input_html)
        on_progress("done", _("Text modified"))
        send_result({"type": "cmd_result", "string": text, "ok": True, "message": _("Text modified")})
        return

    # --- execute_config ---
    if intent == "execute_config":
        match_string = plan.get("match_string", "").strip()
        if not match_string:
            send_result({"type": "cmd_result", "string": text, "ok": False, "message": _("No command matched")})
            return
        on_progress("executing", _("Executing: {resolved}").format(resolved=match_string))
        result = _run_config_command(match_string)
        send_result({"type": "cmd_result", "string": text, "ok": result.get("ok", False), "message": result.get("message", "")})
        return

    # --- open_app ---
    if intent == "open_app":
        app_path = plan.get("app_path", "").strip()
        app_args = plan.get("app_args")
        if isinstance(app_args, list):
            app_args = [str(x) for x in app_args if str(x).strip()]
        else:
            app_args = []
        on_progress("executing", _("Opening application: {app}").format(app=app_path or "..."))
        result = _run_app(app_path, app_args)
        send_result({"type": "cmd_result", "string": text, "ok": result.get("ok", False), "message": result.get("message", "")})
        return

    # --- open_browser ---
    if intent == "open_browser":
        url = plan.get("url", "").strip()
        search_query = plan.get("search_query", "").strip()
        on_progress("executing", _("Opening browser..."))
        result = _run_browser(url=url if url else None, search_query=search_query if search_query else None)
        send_result({"type": "cmd_result", "string": text, "ok": result.get("ok", False), "message": result.get("message", "")})
        return

    # --- unknown ---
    send_result({"type": "cmd_result", "string": text, "ok": False, "message": _("No command matched")})
