# -*- coding: utf-8 -*-
"""
Unit tests for LLM streaming in llm_assistant.
Run: python -m pytest test_llm_stream.py -v
"""
import json
import sys

# Fix Windows console UTF-8 display
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
from unittest.mock import patch


# Simulate Ollama streaming response (NDJSON format)
# Use .encode() to avoid ASCII-only restriction in bytes literals
OLLAMA_STREAM_CHUNKS = [
    ('{"model":"qwen","message":{"role":"assistant","content":"' + "清" + '"},"done":false}\n').encode("utf-8"),
    ('{"model":"qwen","message":{"role":"assistant","content":"' + "空" + '"},"done":false}\n').encode("utf-8"),
    b'{"model":"qwen","message":{"role":"assistant","content":""},"done":true}\n',
]

OLLAMA_STREAM_SINGLE = ('{"model":"qwen","message":{"role":"assistant","content":"' + "无" + '"},"done":true}\n').encode("utf-8")

OLLAMA_STREAM_NO_NEWLINE = ('{"model":"qwen","message":{"role":"assistant","content":"' + "无" + '"},"done":true}').encode("utf-8")

# qwen3.5 reasoning model: thinking first, then content
OLLAMA_STREAM_THINKING = [
    b'{"model":"qwen","message":{"role":"assistant","content":"","thinking":"Think"},"done":false}\n',
    b'{"model":"qwen","message":{"role":"assistant","content":"","thinking":"ing"},"done":false}\n',
    ('{"model":"qwen","message":{"role":"assistant","content":"' + "清空" + '"},"done":false}\n').encode("utf-8"),
    b'{"model":"qwen","message":{"role":"assistant","content":""},"done":true}\n',
]


class MockResponse:
    """Simulate HTTPResponse with chunked read."""
    def __init__(self, chunks):
        self._chunks = chunks
        self._pos = 0

    def read(self, size=8192):
        if self._pos >= len(self._chunks):
            return b""
        chunk = self._chunks[self._pos]
        self._pos += 1
        return chunk

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


def test_parse_stream_response_chunked():
    """Test _parse_stream_response with multiple chunks (simulates streaming)."""
    from llm_assistant import _parse_stream_response

    chunks = []
    def on_stream(acc):
        chunks.append(acc)

    resp = MockResponse(OLLAMA_STREAM_CHUNKS)
    result = _parse_stream_response(resp, on_stream=on_stream)

    assert result == "清空", f"Expected '清空', got {result!r}"
    assert len(chunks) >= 2, f"Expected on_stream to be called 2+ times, got {len(chunks)}"
    assert "清" in chunks[0], f"First chunk should contain '清', got {chunks}"
    assert chunks[-1] == "清空", f"Last chunk should be '清空', got {chunks}"


def test_parse_stream_response_single_chunk():
    """Test _parse_stream_response with single chunk (full response at once)."""
    from llm_assistant import _parse_stream_response

    chunks = []
    def on_stream(acc):
        chunks.append(acc)

    resp = MockResponse([OLLAMA_STREAM_SINGLE])
    result = _parse_stream_response(resp, on_stream=on_stream)

    assert result == "无", f"Expected '无', got {result!r}"
    assert len(chunks) >= 1, f"Expected on_stream to be called, got {len(chunks)}"
    assert chunks[-1] == "无", f"Last chunk should be '无', got {chunks}"


def test_parse_stream_response_no_trailing_newline():
    """Test _parse_stream_response when response has no trailing newline."""
    from llm_assistant import _parse_stream_response

    chunks = []
    def on_stream(acc):
        chunks.append(acc)

    resp = MockResponse([OLLAMA_STREAM_NO_NEWLINE])
    result = _parse_stream_response(resp, on_stream=on_stream)

    assert result == "无", f"Expected '无', got {result!r}"
    assert len(chunks) >= 1, f"Expected on_stream to be called, got {len(chunks)}"


def test_parse_stream_response_thinking():
    """Test _parse_stream_response with reasoning model (thinking then content)."""
    from llm_assistant import _parse_stream_response

    chunks = []
    def on_stream(acc):
        chunks.append(acc)

    resp = MockResponse(OLLAMA_STREAM_THINKING)
    result = _parse_stream_response(resp, on_stream=on_stream)

    assert result == "清空", f"Expected '清空', got {result!r}"
    assert len(chunks) >= 2, f"Expected on_stream during thinking+content, got {len(chunks)}"
    assert any("思考中" in c for c in chunks), f"Expected '思考中' in stream, got {chunks}"
    assert chunks[-1] == "清空", f"Final should be '清空', got {chunks[-1]!r}"


def test_chat_via_http_mocked():
    """Test _chat_via_http with mocked urlopen (full integration)."""
    from llm_assistant import _chat_via_http

    streamed = []

    def fake_urlopen(req, timeout=None):
        return MockResponse(OLLAMA_STREAM_CHUNKS)

    with patch("llm_assistant.urllib.request.urlopen", fake_urlopen):
        result = _chat_via_http(
            "http://127.0.0.1:11434",
            "qwen",
            "test prompt",
            timeout=5,
            stream=True,
            on_stream=streamed.append,
        )

    assert result == "清空", f"Expected '清空', got {result!r}"
    assert len(streamed) >= 2, f"Expected on_stream calls, got {len(streamed)}"


def test_real_ollama_stream():
    """
    Call real Ollama API to capture actual response format.
    Run with: python test_llm_stream.py --live
    """
    import urllib.request

    url = "http://127.0.0.1:11434/api/chat"
    payload = {
        "model": "qwen3.5:0.8b",
        "messages": [{"role": "user", "content": "只回复一个字：好"}],
        "stream": True,
        "think": False,
        "options": {"num_predict": 8},
    }
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    print("\n--- Raw Ollama stream response (first 2000 bytes) ---")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read(2000)
            print(repr(raw[:500]))
            print("...")
            print("\n--- Parsed lines ---")
            for i, line in enumerate(raw.split(b"\n")[:5]):
                if line.strip():
                    print(f"  Line {i}: {line[:120]!r}")
    except Exception as e:
        print(f"  Error: {e}")

    print("\n--- Test with on_stream callback ---")
    streamed = []
    try:
        from llm_assistant import _chat_via_http
        result = _chat_via_http(
            "http://127.0.0.1:11434",
            "qwen3.5:0.8b",
            "只回复一个字：好",
            timeout=15,
            stream=True,
            on_stream=lambda x: (streamed.append(x), print(f"  on_stream: {x!r}"))[1],
        )
        print(f"  Result: {result!r}")
        print(f"  on_stream called {len(streamed)} times")
        if result and len(streamed) > 0:
            print("\n  ✅ Live test passed: got content and on_stream was called")
        elif not result:
            print("\n  ⚠ Result empty - model may have returned only thinking")
    except Exception as e:
        print(f"  Error: {e}")


if __name__ == "__main__":
    import sys
    if "--live" in sys.argv:
        test_real_ollama_stream()
        sys.exit(0)

    print("Running test_parse_stream_response_chunked...")
    test_parse_stream_response_chunked()
    print("  OK")

    print("Running test_parse_stream_response_single_chunk...")
    test_parse_stream_response_single_chunk()
    print("  OK")

    print("Running test_parse_stream_response_no_trailing_newline...")
    test_parse_stream_response_no_trailing_newline()
    print("  OK")

    print("Running test_parse_stream_response_thinking...")
    test_parse_stream_response_thinking()
    print("  OK")

    print("Running test_chat_via_http_mocked...")
    test_chat_via_http_mocked()
    print("  OK")

    print("\nAll tests passed!")
