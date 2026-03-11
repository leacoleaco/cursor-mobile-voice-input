"""Voice command parsing and configurable command execution."""
import re
import shlex
import subprocess
from dataclasses import dataclass
from typing import Callable, List, Optional

import config_store
from i18n import _
from settings import CLEAR_BACKSPACE_MAX


@dataclass
class CommandResult:
    handled: bool
    display_text: str = ""
    output: object = ""


class CommandProcessor:
    def __init__(self):
        self.paused = False
        self.history = []
        self.alias = {"豆号": "逗号", "都好": "逗号", "据号": "句号", "聚好": "句号", "句点": "句号"}
        self.punc_map = {"逗号": "，", "句号": "。", "问号": "？", "感叹号": "！", "冒号": "：", "分号": "；", "顿号": "、"}

    def normalize(self, text: str) -> str:
        text = (text or "").strip()
        for k, v in self.alias.items():
            text = text.replace(k, v)
        return text

    def parse_delete_n(self, text: str):
        m = re.search(r"(删除|退格)\s*(\d+)\s*(个字|次)?", text)
        return int(m.group(2)) if m else None

    def handle(self, raw_text: str) -> CommandResult:
        text = self.normalize(raw_text)

        if text in ["暂停输入", "暂停", "停止输入"]:
            self.paused = True
            return CommandResult(True, "⏸ " + _("Paused"), "")

        if text in ["继续输入", "继续", "恢复输入"]:
            self.paused = False
            return CommandResult(True, "▶️ " + _("Resumed"), "")

        if self.paused:
            return CommandResult(True, _("Paused - {text}").format(text=raw_text), "")

        if text in ["换行", "回车", "下一行"]:
            return CommandResult(True, "↩️ " + _("Newline"), ("__ENTER__", 1))

        if text in self.punc_map:
            return CommandResult(True, f"⌨️ {text}", self.punc_map[text])

        if text in ["删除上一句", "撤回上一句", "撤销上一句", "删掉上一句"]:
            if not self.history:
                return CommandResult(True, "⚠️ " + _("Nothing to delete"), "")
            last = self.history.pop()
            return CommandResult(True, "⌫ " + _("Deleted last sentence: {last}").format(last=last), ("__BACKSPACE__", len(last)))

        n = self.parse_delete_n(text)
        if n is not None:
            return CommandResult(True, "⌫ " + _("Deleted {n} characters").format(n=n), ("__BACKSPACE__", n))

        if text in ["清空", "清除全部", "全部删除"]:
            return CommandResult(True, "🧹 " + _("Cleared"), ("__BACKSPACE__", CLEAR_BACKSPACE_MAX))

        return CommandResult(False, raw_text, raw_text)

    def record_output(self, out: str):
        if out and out != "\n":
            self.history.append(out)


processor = CommandProcessor()

# Canonical command strings for LLM (built-in + aliases collapsed to canonical)
BUILTIN_CANDIDATES = [
    "暂停输入",
    "继续输入",
    "换行",
    "逗号", "句号", "问号", "感叹号", "冒号", "分号", "顿号",
    "删除上一句",
    "删除 N 个字",  # LLM matches "删3个字" etc.; we parse N from raw text
    "清空",
]


def get_all_command_candidates() -> List[str]:
    """All command candidates for LLM: built-in + config match-strings."""
    candidates = list(BUILTIN_CANDIDATES)
    for cmd in config_store.COMMANDS:
        ms = (cmd.get("match-string") or "").strip()
        if ms and ms not in candidates:
            candidates.append(ms)
    return candidates


def _build_command_args(command, args) -> List[str]:
    if isinstance(command, str) and command.strip():
        parts = shlex.split(command, posix=False)
    elif isinstance(command, list):
        parts = [str(x) for x in command if str(x).strip()]
    else:
        parts = []

    if isinstance(args, list):
        parts.extend([str(x) for x in args if str(x).strip()])
    return parts


def match_command(text: str) -> Optional[dict]:
    text = (text or "").strip()
    if not text:
        return None
    for cmd in config_store.COMMANDS:
        match_string = (cmd.get("match-string") or "").strip()
        if match_string and match_string == text:
            return cmd
    # LLM-assisted fuzzy matching when exact match fails
    if config_store.LLM_ENABLED and config_store.COMMANDS:
        try:
            from llm_assistant import resolve_command_via_llm
            candidates = [
                (c.get("match-string") or "").strip()
                for c in config_store.COMMANDS
                if (c.get("match-string") or "").strip()
            ]
            if candidates:
                resolved = resolve_command_via_llm(
                    text, candidates,
                    model=config_store.LLM_MODEL,
                    base_url=config_store.LLM_BASE_URL,
                )
                if resolved:
                    for cmd in config_store.COMMANDS:
                        if (cmd.get("match-string") or "").strip() == resolved:
                            return cmd
        except Exception:
            pass
    return None


def execute_config_command_by_match_string(match_string: str) -> CommandResult:
    """Execute a config command by its match-string."""
    for cmd in config_store.COMMANDS:
        ms = (cmd.get("match-string") or "").strip()
        if ms == match_string:
            args = _build_command_args(cmd.get("command"), cmd.get("args"))
            if not args:
                return CommandResult(True, _("Command config error") + f": {match_string}", {"ok": False, "message": _("Command config error")})
            try:
                completed = subprocess.run(args, capture_output=True, text=True)
                ok = completed.returncode == 0
                stderr = (completed.stderr or "").strip()
                msg = (_("Command executed successfully") if ok else _("Command execution failed")) + f": {match_string}" + ("" if ok else f" (exit {completed.returncode})")
                if stderr:
                    msg = f"{msg} - {stderr}"
                return CommandResult(True, msg, {"ok": ok, "message": msg})
            except Exception as e:
                return CommandResult(True, _("Command execution error") + f": {match_string} - {e}", {"ok": False, "message": str(e)})
    return CommandResult(True, _("Command not found") + f": {match_string}", {"ok": False, "message": _("Command not found") + f": {match_string}"})


def execute_command(text: str) -> CommandResult:
    cmd = match_command(text)
    if not cmd:
        return CommandResult(True, _("No matching command") + f": {text}", {"ok": False, "message": _("No matching command")})

    args = _build_command_args(cmd.get("command"), cmd.get("args"))
    if not args:
        return CommandResult(True, _("Command config error") + f": {text}", {"ok": False, "message": _("Command config error")})

    try:
        completed = subprocess.run(args, capture_output=True, text=True)
        ok = completed.returncode == 0
        stderr = (completed.stderr or "").strip()
        if ok:
            msg = _("Command executed successfully") + f": {text}"
        else:
            msg = _("Command execution failed") + f": {text} (exit {completed.returncode})"
            if stderr:
                msg = f"{msg} - {stderr}"
        return CommandResult(True, msg, {"ok": ok, "message": msg})
    except Exception as e:
        return CommandResult(True, _("Command execution error") + f": {text} - {e}", {"ok": False, "message": _("Command execution error") + f": {e}"})


def handle_command_with_llm(
    raw_text: str,
    send_progress: Callable[[dict], None],
    send_result: Callable[[dict], None],
) -> None:
    """
    Handle command mode with LLM judgment and progress visualization.
    When LLM disabled, falls back to exact match + built-in processor.
    """
    text = (raw_text or "").strip()
    if not text:
        return

    print(f"[cmd] 收到命令: {text}")

    from text_handler import server_dedup
    if server_dedup(text, "cmd"):
        print("[cmd] 去重跳过")
        return

    if not config_store.LLM_ENABLED:
        # Fallback: exact match for config, then built-in processor
        cmd = match_command(text)
        if cmd:
            ms = (cmd.get("match-string") or "").strip()
            print(f"[cmd] matched: {ms}")
            send_progress({"type": "cmd_progress", "step": "matched", "message": _("Matched: {ms}").format(ms=ms)})
            result = execute_command(text)
            print(f"[cmd] result: {result.output.get('message') if isinstance(result.output, dict) else result.display_text}")
            send_result({
                "type": "cmd_result",
                "string": text,
                "ok": bool(result.output.get("ok")) if isinstance(result.output, dict) else False,
                "message": result.output.get("message") if isinstance(result.output, dict) else result.display_text,
            })
            return
        # Built-in processor
        result = processor.handle(text)
        if result.output == "":
            print(f"[cmd] done: {result.display_text}")
            send_progress({"type": "cmd_progress", "step": "done", "message": result.display_text})
            send_result({"type": "cmd_result", "string": text, "ok": True, "message": result.display_text})
            return
        # Execute output (backspace, enter, or text)
        from text_handler import execute_output
        from input_control import focus_target
        focus_target()
        execute_output(result.output)
        if not result.handled and isinstance(result.output, str):
            processor.record_output(result.output)
        print(f"[cmd] done: {result.display_text}")
        send_progress({"type": "cmd_progress", "step": "done", "message": result.display_text})
        send_result({"type": "cmd_result", "string": text, "ok": True, "message": result.display_text})
        return

    # LLM-enabled path: first try local matching, then LLM only when no match
    # Step 1: Try built-in processor
    result = processor.handle(text)
    if result.handled:
        if result.output == "":
            print(f"[cmd] done (local): {result.display_text}")
            send_progress({"type": "cmd_progress", "step": "done", "message": result.display_text})
            send_result({"type": "cmd_result", "string": text, "ok": True, "message": result.display_text})
            return
        from text_handler import execute_output
        from input_control import focus_target
        focus_target()
        execute_output(result.output)
        if not result.handled and isinstance(result.output, str):
            processor.record_output(result.output)
        print(f"[cmd] done (local): {result.display_text}")
        send_progress({"type": "cmd_progress", "step": "done", "message": result.display_text})
        send_result({"type": "cmd_result", "string": text, "ok": True, "message": result.display_text})
        return

    # Step 2: Try exact match on config commands
    for cmd in config_store.COMMANDS:
        match_string = (cmd.get("match-string") or "").strip()
        if match_string and match_string == text:
            print(f"[cmd] matched (local): {match_string}")
            send_progress({"type": "cmd_progress", "step": "matched", "message": _("Matched: {ms}").format(ms=match_string)})
            result = execute_config_command_by_match_string(match_string)
            msg = result.output.get("message") if isinstance(result.output, dict) else result.display_text
            ok = bool(result.output.get("ok")) if isinstance(result.output, dict) else False
            send_result({"type": "cmd_result", "string": text, "ok": ok, "message": msg})
            return

    # Step 3: No local match - call LLM for fuzzy matching
    candidates = get_all_command_candidates()
    print("[log] candidates: ", candidates)
    if not candidates:
        print("[cmd] error: 无可用指令")
        send_progress({"type": "cmd_progress", "step": "error", "message": _("No commands available")})
        send_result({"type": "cmd_result", "string": text, "ok": False, "message": _("No commands available")})
        return

    def on_progress(step: str, msg: str):
        print(f"[cmd] {step}: {msg}")
        send_progress({"type": "cmd_progress", "step": step, "message": msg})

    def on_stream(accumulated: str):
        print(f"[cmd] 大模型输出: {accumulated}", flush=True)
        send_progress({"type": "cmd_progress", "step": "llm_stream", "message": accumulated})
        send_progress({"type": "cmd_progress", "step": "llm_stream", "message": accumulated})

    try:
        from llm_assistant import resolve_command_with_progress
        resolved = resolve_command_with_progress(
            text, candidates,
            model=config_store.LLM_MODEL,
            base_url=config_store.LLM_BASE_URL,
            on_progress=on_progress,
            on_stream=on_stream,
        )
    except Exception as e:
        print(f"[cmd] error: LLM 异常 - {e}")
        on_progress("error", str(e))
        send_result({"type": "cmd_result", "string": text, "ok": False, "message": _("LLM error: {e}").format(e=e)})
        return

    if not resolved:
        print(f"[cmd] 未匹配到指令: {text}")
        send_result({"type": "cmd_result", "string": text, "ok": False, "message": _("No command matched")})
        return

    # Execute: config command or built-in
    print(f"[cmd] executing: {resolved}")
    send_progress({"type": "cmd_progress", "step": "executing", "message": _("Executing: {resolved}").format(resolved=resolved)})

    if resolved in [(c.get("match-string") or "").strip() for c in config_store.COMMANDS if (c.get("match-string") or "").strip()]:
        result = execute_config_command_by_match_string(resolved)
        msg = result.output.get("message") if isinstance(result.output, dict) else result.display_text
        ok = bool(result.output.get("ok")) if isinstance(result.output, dict) else False
        print(f"[cmd] result: {msg}" + (" (失败)" if not ok else ""))
        send_result({
            "type": "cmd_result",
            "string": text,
            "ok": ok,
            "message": msg,
        })
        return

    # Built-in: use raw text for "删除 N 个字" so we can parse the number
    exec_text = text if resolved == "删除 N 个字" else resolved
    result = processor.handle(exec_text)
    if result.output == "":
        print(f"[cmd] done: {result.display_text}")
        send_progress({"type": "cmd_progress", "step": "done", "message": result.display_text})
        send_result({"type": "cmd_result", "string": text, "ok": True, "message": result.display_text})
        return
    from text_handler import execute_output
    from input_control import focus_target
    focus_target()
    execute_output(result.output)
    if not result.handled and isinstance(result.output, str):
        processor.record_output(result.output)
    print(f"[cmd] done: {result.display_text}")
    send_progress({"type": "cmd_progress", "step": "done", "message": result.display_text})
    send_result({"type": "cmd_result", "string": text, "ok": True, "message": result.display_text})
