# -*- coding: utf-8 -*-
"""
Optional LLM-assisted command judgment via Ollama.
Uses small models (e.g. qwen3.5:0.8b) for semantic/fuzzy command matching.
Supports remote URL (direct API call when client parsing fails).
"""
import json
import threading
import urllib.request
import urllib.error
from typing import Callable, List, Optional

from i18n import _

_ollama_available: Optional[bool] = None
_ollama_base_url: Optional[str] = None


def _get_client(base_url: str = "http://127.0.0.1:11434"):
    """Get Ollama client with optional custom base URL (supports remote URL)."""
    try:
        from ollama import Client
        url = (base_url or "").strip().rstrip("/") or "http://127.0.0.1:11434"
        try:
            return Client(base_url=url)
        except TypeError:
            return Client(host=url)
    except ImportError:
        return None


def _chat_via_http(
    base_url: str,
    model: str,
    prompt: str,
    timeout: float = 30.0,
    stream: bool = True,
    on_stream: Optional[Callable[[str], None]] = None,
    num_predict: int = 256,
) -> Optional[str]:
    """
    Call Ollama /api/chat via raw HTTP. Uses streaming by default for lower latency.
    on_stream(accumulated_text) is called for each token received when streaming.
    num_predict: max tokens to generate (default 256; use higher for long outputs like modify_text).
    """
    url = (base_url or "").strip().rstrip("/") or "http://127.0.0.1:11434"
    endpoint = f"{url}/api/chat"
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": stream,
        "options": {"num_predict": max(32, num_predict)},
    }
    # Disable reasoning for qwen3.5 - we need direct content for command matching
    payload["think"] = False
    try:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            endpoint,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if stream:
                return _parse_stream_response(resp, on_stream)
            body = resp.read().decode("utf-8", errors="replace")
            try:
                out = json.loads(body)
                content = (out.get("message", {}).get("content") or "").strip()
                return content
            except json.JSONDecodeError as e:
                print(f"[cmd] HTTP 响应 JSON 解析失败: {e}")
                return None
    except Exception as e:
        print(f"[cmd] HTTP 请求失败: {e}")
        return None


def _parse_stream_response(resp, on_stream: Optional[Callable[[str], None]] = None) -> Optional[str]:
    """Parse Ollama NDJSON stream; accumulate message.content until done.
    Handles reasoning models (qwen3.5 etc.) that send message.thinking before content.
    """
    content_parts = []
    thinking_parts = []
    buffer = b""

    def _emit(accumulated: str):
        if on_stream and accumulated:
            try:
                on_stream(accumulated)
            except Exception:
                pass

    def _process_line(line: bytes) -> Optional[str]:
        line = line.strip()
        if not line:
            return None
        try:
            obj = json.loads(line.decode("utf-8", errors="replace"))
            msg = obj.get("message") or {}
            delta = msg.get("content") or ""
            thinking = msg.get("thinking") or ""

            if delta:
                content_parts.append(delta)
                _emit("".join(content_parts).strip())
            elif thinking:
                thinking_parts.append(thinking)
                # Show progress while reasoning (qwen3.5 etc.)
                full = "".join(thinking_parts)
                _emit(_("Thinking...") + (full[-30:] if len(full) > 30 else full))

            if obj.get("done"):
                return "".join(content_parts).strip()
        except json.JSONDecodeError:
            pass
        return None

    while True:
        chunk = resp.read(8192)
        if not chunk:
            break
        buffer += chunk
        while b"\n" in buffer:
            line, buffer = buffer.split(b"\n", 1)
            result = _process_line(line)
            if result is not None:
                return result

    # Handle remaining buffer (no trailing newline)
    if buffer.strip():
        result = _process_line(buffer)
        if result is not None:
            return result

    final = "".join(content_parts).strip()
    if final and on_stream:
        _emit(final)  # ensure we emit at least once
    return final or None


def _check_ollama(base_url: str = "http://127.0.0.1:11434") -> bool:
    """Check if Ollama is available and reachable at the given URL."""
    global _ollama_available, _ollama_base_url
    if _ollama_available is not None and _ollama_base_url == base_url:
        return _ollama_available
    url = (base_url or "").strip().rstrip("/") or "http://127.0.0.1:11434"
    # Try ollama client first (if package installed)
    try:
        client = _get_client(base_url)
        if client:
            client.list()
            _ollama_available = True
            _ollama_base_url = base_url
            return _ollama_available
    except Exception:
        pass
    # Fallback: raw HTTP to /api/tags (works without ollama package)
    try:
        req = urllib.request.Request(f"{url}/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=5) as _:
            _ollama_available = True
            _ollama_base_url = base_url
    except Exception:
        _ollama_available = False
        _ollama_base_url = base_url
    return _ollama_available


def resolve_command_via_llm(
    user_text: str,
    candidates: List[str],
    model: str = "qwen3.5:0.8b",
    base_url: str = "http://127.0.0.1:11434",
    timeout: float = 30.0,
) -> Optional[str]:
    """
    Use LLM to infer which command the user intended.
    Returns the matched candidate string, or None if no match.

    Args:
        user_text: Raw user input (e.g. from voice).
        candidates: List of command match-strings to choose from.
        model: Ollama model name.
        timeout: Max seconds to wait for response.

    Returns:
        The best-matching candidate, or None.
    """
    if not candidates or not (user_text or "").strip():
        return None
    if not _check_ollama(base_url):
        return None

    prompt = f"""用户说了：「{user_text}」
以下是指令列表（每行一个）：
{chr(10).join(candidates)}

请判断用户是否想执行其中某个指令。只回复一个指令原文，如果都不匹配则回复「无」。
不要解释，不要标点，只输出指令或「无」。"""

    result = [None]  # mutable to capture from thread

    def _call():
        content = _chat_via_http(base_url, model, prompt, timeout)
        if content:
            content = content.strip("\"'").strip()
            if content != "无" and content in candidates:
                result[0] = content

    t = threading.Thread(target=_call, daemon=True)
    t.start()
    t.join(timeout=timeout)
    return result[0]


def resolve_command_with_progress(
    user_text: str,
    candidates: List[str],
    model: str = "qwen3.5:0.8b",
    base_url: str = "http://127.0.0.1:11434",
    on_progress: Optional[Callable[[str, str], None]] = None,
    on_stream: Optional[Callable[[str], None]] = None,
    timeout: float = 30.0,
) -> Optional[str]:
    """
    Same as resolve_command_via_llm but calls on_progress(step, message) for visualization.
    on_stream(accumulated_text) is called for each token when streaming LLM output.
    """
    if not candidates or not (user_text or "").strip():
        return None

    def _emit(step: str, msg: str):
        if on_progress:
            try:
                on_progress(step, msg)
            except Exception:
                pass

    if not _check_ollama(base_url):
        _emit("error", _("Ollama unavailable"))
        return None

    _emit("llm_judging", _("Using LLM to judge..."))

    prompt = f"""用户说了：「{user_text}」
以下是指令列表（每行一个）：
{chr(10).join(candidates)}

请判断用户是否想执行其中某个指令。只回复一个指令原文，如果都不匹配则回复「无」。
不要解释，不要标点，只输出指令或「无」。"""

    result = [None]

    def _call():
        content = _chat_via_http(base_url, model, prompt, timeout, stream=True, on_stream=on_stream)
        if content:
            content = content.strip("\"'").strip()
            if content != "无" and content in candidates:
                result[0] = content

    t = threading.Thread(target=_call, daemon=True)
    t.start()
    t.join(timeout=timeout)

    if result[0]:
        _emit("matched", _("Matched: {ms}").format(ms=result[0]))
    else:
        _emit("no_match", _("No command matched"))
    return result[0]


def preload_model(model: str = "qwen3.5:0.8b", base_url: str = "http://127.0.0.1:11434") -> bool:
    """
    Preload model in background for faster first inference.
    Call at startup. Returns True if successful.
    """
    if not _check_ollama(base_url):
        return False
    try:
        client = _get_client(base_url)
        if client:
            client.chat(model=model, messages=[{"role": "user", "content": "hi"}], options={"num_predict": 1})
            return True
    except Exception:
        pass
    return False


def is_available(base_url: str = "http://127.0.0.1:11434") -> bool:
    """Whether Ollama LLM assistant is available at the given URL."""
    return _check_ollama(base_url)


# --- Intent-based command mode (all prompts in English) ---

from llm_tools import (
    get_intent_prompt_suffix,
    INTENT_MODIFY_TEXT,
    INTENT_NEWLINE,
    INTENT_PUNCTUATION,
    INTENT_DELETE_LAST,
    INTENT_DELETE_N,
    INTENT_CLEAR,
    INTENT_EXECUTE_CONFIG,
    INTENT_UNKNOWN,
)


def detect_intent(
    user_command: str,
    config_match_strings: List[str],
    model: str = "qwen3.5:0.8b",
    base_url: str = "http://127.0.0.1:11434",
    timeout: float = 30.0,
    on_stream: Optional[Callable[[str], None]] = None,
) -> dict:
    """
    Use LLM to detect user intent from voice command. All prompts in English.
    Returns dict: { "intent": str, "match_string": str|None, "punctuation": str|None, "delete_n": int|None }
    - match_string: for execute_config, the matched config command
    - punctuation: for punctuation intent, the char to insert (e.g. "，", "。")
    - delete_n: for delete_n intent, number of chars to delete
    """
    user_command = (user_command or "").strip()
    if not user_command:
        return {"intent": INTENT_UNKNOWN}

    if not _check_ollama(base_url):
        return {"intent": INTENT_UNKNOWN}

    suffix = get_intent_prompt_suffix(config_match_strings or [])

    prompt = f"""You are a voice command assistant. The user said (in any language): "{user_command}"

{suffix}

Reply with ONLY the intent name (e.g. modify_text, newline, comma, etc.). For punctuation intent, reply with the punctuation name: comma, period, question, exclamation, colon, semicolon, pause_comma.
For execute_config, reply with: execute_config:"exact_match_string" (use the exact config match string).
For delete_n, reply with: delete_n:N (e.g. delete_n:3 for "delete 3 characters").
No explanation, no quotes, just the intent or intent:param."""

    result = [None]

    def _call():
        content = _chat_via_http(
            base_url, model, prompt, timeout,
            stream=bool(on_stream), on_stream=on_stream,
            num_predict=64,
        )
        if content:
            content = content.strip().strip("\"'").lower()
            result[0] = _parse_intent_response(content, config_match_strings or [])

    t = threading.Thread(target=_call, daemon=True)
    t.start()
    t.join(timeout=timeout)

    out = result[0]
    return out if isinstance(out, dict) else {"intent": INTENT_UNKNOWN}


def _parse_intent_response(content: str, config_match_strings: List[str]) -> dict:
    """Parse LLM response into intent dict."""
    content = (content or "").strip().lower()

    # delete_n:N
    if content.startswith("delete_n:"):
        try:
            n = int(content.split(":", 1)[1].strip())
            return {"intent": INTENT_DELETE_N, "delete_n": max(1, min(n, 999))}
        except (ValueError, IndexError):
            pass

    # execute_config:"match_string"
    if content.startswith("execute_config:"):
        rest = content.split(":", 1)[1].strip().strip("\"'")
        for ms in config_match_strings:
            if not ms:
                continue
            ms_clean = ms.strip()
            if rest == ms_clean.lower() or rest in ms_clean.lower() or ms_clean.lower() in rest:
                return {"intent": INTENT_EXECUTE_CONFIG, "match_string": ms_clean}

    # Punctuation mapping
    punc_map = {
        "comma": "，", "period": "。", "question": "？", "exclamation": "！",
        "colon": "：", "semicolon": "；", "pause_comma": "、",
        "逗号": "，", "句号": "。", "问号": "？", "感叹号": "！",
        "冒号": "：", "分号": "；", "顿号": "、",
    }
    if content in punc_map:
        return {"intent": INTENT_PUNCTUATION, "punctuation": punc_map[content]}

    # Simple intent names
    intent_map = {
        "modify_text": INTENT_MODIFY_TEXT,
        "newline": INTENT_NEWLINE,
        "delete_last": INTENT_DELETE_LAST,
        "clear": INTENT_CLEAR,
    }
    if content in intent_map:
        return {"intent": intent_map[content]}

    # Aliases
    if content in ("enter", "next line", "换行", "回车"):
        return {"intent": INTENT_NEWLINE}

    return {"intent": INTENT_UNKNOWN}


def modify_text_via_llm(
    instruction: str,
    input_text: str,
    model: str = "qwen3.5:0.8b",
    base_url: str = "http://127.0.0.1:11434",
    timeout: float = 60.0,
    on_stream: Optional[Callable[[str], None]] = None,
) -> Optional[str]:
    """
    Use LLM to modify text according to user instruction. All prompts in English.
    Returns the modified text, or None on failure.
    """
    instruction = (instruction or "").strip()
    input_text = (input_text or "").strip()

    if not instruction:
        return input_text
    if not input_text:
        return ""

    if not _check_ollama(base_url):
        return None

    prompt = f"""You are a text editing assistant. The user wants to modify the following text.

User instruction (in any language): "{instruction}"

Original text:
---
{input_text}
---

Apply the user's instruction to the text. Output ONLY the modified text, nothing else. No explanation, no markdown, no quotes around the output. Preserve the language and structure unless the instruction says otherwise."""

    result = [None]

    def _call():
        content = _chat_via_http(
            base_url, model, prompt, timeout,
            stream=bool(on_stream), on_stream=on_stream,
            num_predict=4096,
        )
        print(f"[cmd] llm response: {content}")
        if content:
            result[0] = content.strip()

    t = threading.Thread(target=_call, daemon=True)
    t.start()
    t.join(timeout=timeout)

    return result[0]


def analyze_command_plan(
    user_command: str,
    config_match_strings: List[str],
    model: str = "qwen3.5:0.8b",
    base_url: str = "http://127.0.0.1:11434",
    timeout: float = 30.0,
    on_stream: Optional[Callable[[str], None]] = None,
) -> dict:
    """
    Use LLM to analyze user command and return execution plan.
    Returns: {
        "intent": "modify_text" | "open_app" | "open_browser" | "execute_config" | "unknown",
        "instruction": str,      # for modify_text
        "app_path": str,        # for open_app (exe path or app name like notepad)
        "app_args": list,       # for open_app
        "url": str,             # for open_browser direct URL
        "search_query": str,    # for open_browser search
        "match_string": str,    # for execute_config
    }
    """
    user_command = (user_command or "").strip()
    if not user_command:
        return {"intent": "unknown"}

    if not _check_ollama(base_url):
        return {"intent": "unknown"}

    config_section = ""
    if config_match_strings:
        config_section = f"""
Configured commands (use execute_config with exact match_string if user intent matches):
{chr(10).join(f'  - "{ms}"' for ms in config_match_strings if ms)}
"""

    prompt = f"""You are a voice command analyzer. The user said (in any language): "{user_command}"

Analyze and output a JSON object with ONE of these intents:

1. modify_text - User wants to edit/change text in the focused input box. Examples: "change xx to xxx", "修改xx为xxx", "fix typos", "translate to English"
   Output: {{"intent":"modify_text","instruction":"<user's edit instruction in full>"}}

2. open_app - User wants to open an application/software and optionally run something. Examples: "open notepad", "打开记事本", "run calculator"
   Output: {{"intent":"open_app","app_path":"<exe path or app name like notepad>","app_args":[]}}
   For Windows: notepad, calc, cmd, explorer. For full path use the path.

3. open_browser - User wants to open browser and do something. Examples: "open browser search xxx", "打开浏览器搜索xxx", "open google.com"
   For URL: {{"intent":"open_browser","url":"https://..."}}
   For search: {{"intent":"open_browser","search_query":"<search terms>"}}
{config_section}
4. execute_config - User intent matches one of the configured commands above. Use exact match_string.
   Output: {{"intent":"execute_config","match_string":"<exact match from list>"}}

5. unknown - None of the above
   Output: {{"intent":"unknown"}}

Reply with ONLY valid JSON, no explanation, no markdown code block."""

    result = [None]

    def _call():
        content = _chat_via_http(
            base_url, model, prompt, timeout,
            stream=bool(on_stream), on_stream=on_stream,
            num_predict=256,
        )
        print(f"[cmd] llm response: {content}")
        if content:
            content = content.strip().strip("`").strip()
            if content.startswith("json"):
                content = content[4:].strip()
            try:
                obj = json.loads(content)
                if isinstance(obj, dict):
                    result[0] = obj
            except json.JSONDecodeError:
                # Fallback: try to extract intent from raw text
                lower = content.lower()
                if "modify_text" in lower or "modify text" in lower:
                    result[0] = {"intent": "modify_text", "instruction": user_command}
                elif "open_app" in lower or "open app" in lower:
                    result[0] = {"intent": "open_app", "app_path": "notepad", "app_args": []}
                elif "open_browser" in lower or "open browser" in lower:
                    result[0] = {"intent": "open_browser", "search_query": user_command}
                elif "execute_config" in lower:
                    for ms in config_match_strings or []:
                        if ms and ms.lower() in lower:
                            result[0] = {"intent": "execute_config", "match_string": ms}
                            break
                if result[0] is None:
                    result[0] = {"intent": "unknown"}

    t = threading.Thread(target=_call, daemon=True)
    t.start()
    t.join(timeout=timeout)

    out = result[0]
    return out if isinstance(out, dict) else {"intent": "unknown"}
