# -*- coding: utf-8 -*-
"""
LLM tools for command mode.
Tools = external tools (calling external commands). Invoked after LLM analyzes intent.
Flow: analyze (LLM) -> auto execute tool.
"""
from typing import Any, Callable, Dict, List, Optional

# Max characters for text modification (user requirement: 5000)
MODIFY_TEXT_MAX_CHARS = 5000

# Intent names (used in LLM responses)
INTENT_MODIFY_TEXT = "modify_text"
INTENT_NEWLINE = "newline"
INTENT_PUNCTUATION = "punctuation"
INTENT_DELETE_LAST = "delete_last"
INTENT_DELETE_N = "delete_n"
INTENT_CLEAR = "clear"
INTENT_EXECUTE_CONFIG = "execute_config"
INTENT_UNKNOWN = "unknown"

# Intents that invoke external tools (analyze -> auto execute)
TOOL_INTENTS: List[str] = [INTENT_EXECUTE_CONFIG]

# Tool executor registry: intent -> executor(match_string=None, **kwargs) -> {"ok": bool, "message": str}
# Registered by commands.py to avoid circular imports
TOOL_EXECUTORS: Dict[str, Callable[..., Dict[str, Any]]] = {}

# Tool definitions for LLM (English descriptions)
TOOL_SCHEMAS = {
    INTENT_MODIFY_TEXT: {
        "name": "modify_text",
        "description": "User wants to edit or transform the text in the focused input box. Examples: 'change to formal tone', 'fix typos', 'translate to English', 'rewrite more concisely', 'change word X to Y'.",
    },
    INTENT_NEWLINE: {
        "name": "newline",
        "description": "User wants to insert a newline/line break. Examples: 'newline', 'enter', 'next line', '换行', '回车'.",
    },
    INTENT_PUNCTUATION: {
        "name": "punctuation",
        "description": "User wants to insert a punctuation mark. Examples: comma, period, question mark, exclamation, colon, semicolon.",
    },
    INTENT_DELETE_LAST: {
        "name": "delete_last",
        "description": "User wants to delete the last sentence or phrase. Examples: 'delete last sentence', 'undo last', '撤回上一句'.",
    },
    INTENT_DELETE_N: {
        "name": "delete_n",
        "description": "User wants to delete N characters. Examples: 'delete 3 characters', 'backspace 5', '删3个字'.",
    },
    INTENT_CLEAR: {
        "name": "clear",
        "description": "User wants to clear all text in the input. Examples: 'clear', 'clear all', '清空'.",
    },
    INTENT_EXECUTE_CONFIG: {
        "name": "execute_config",
        "description": "User wants to run a configured external command. Match against config command match-strings.",
    },
}


def is_tool_intent(intent: str) -> bool:
    """Whether the intent invokes an external tool."""
    return intent in TOOL_INTENTS


def execute_tool(intent: str, intent_result: dict) -> Optional[Dict[str, Any]]:
    """
    Execute external tool by intent. Called after LLM analysis.
    Returns {"ok": bool, "message": str} or None if no executor registered.
    """
    executor = TOOL_EXECUTORS.get(intent)
    if not executor:
        return None
    if intent == INTENT_EXECUTE_CONFIG:
        match_string = intent_result.get("match_string")
        if not match_string:
            return {"ok": False, "message": "No command matched"}
        return executor(match_string)
    return executor(**intent_result)


def get_intent_prompt_suffix(config_match_strings: list) -> str:
    """Build the list of intents and config commands for the LLM prompt."""
    lines = [
        "Available intents (reply with exactly one):",
        f"  - {INTENT_MODIFY_TEXT}: edit/transform text in input box",
        f"  - {INTENT_NEWLINE}: insert newline",
        f"  - {INTENT_PUNCTUATION}: insert punctuation (comma, period, etc.)",
        f"  - {INTENT_DELETE_LAST}: delete last sentence",
        f"  - {INTENT_DELETE_N}: delete N characters",
        f"  - {INTENT_CLEAR}: clear all text",
    ]
    if config_match_strings:
        lines.append("  - execute_config: run external command (match one of):")
        for ms in config_match_strings:
            lines.append(f"      \"{ms}\"")
    lines.append(f"  - {INTENT_UNKNOWN}: none of the above")
    return "\n".join(lines)
