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
) -> Optional[str]:
    """
    Call Ollama /api/chat via raw HTTP. Uses streaming by default for lower latency.
    on_stream(accumulated_text) is called for each token received when streaming.
    """
    url = (base_url or "").strip().rstrip("/") or "http://127.0.0.1:11434"
    endpoint = f"{url}/api/chat"
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": stream,
        "options": {"num_predict": 32},
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
