"""User-facing progress sanitization for Hermes gateway/API streams.

Default chat-platform mode is intentionally quiet: keep raw tool arguments,
internal loop state, provider reasoning, and request objects in logs only.
Set HERMES_DEBUG_TOOL_EVENTS=1 for local/CLI/API debugging paths that need
raw structured tool events.
"""
from __future__ import annotations

import os
import re
from collections import Counter
from pathlib import Path
from typing import Any, Optional

_DEBUG_TRUTHY = {"1", "true", "yes", "on", "debug", "verbose"}
_INTERNAL_PATTERNS = (
    re.compile(r"receiving stream response", re.I),
    re.compile(r"tool completed:\s*\w+", re.I),
    re.compile(r"\b(?:reasoning|chain_of_thought|internal_analysis|reasoning_content)\b", re.I),
    re.compile(r"第\s*\d+\s*/\s*\d+\s*轮"),
    re.compile(r"\biteration\s+\d+\s*/\s*\d+\b", re.I),
    re.compile(r"traceback \(most recent call last\)", re.I),
)
_EDIT_TOOLS = {"patch", "write_file", "skill_manage"}
_CODE_TOOLS = {"execute_code", "terminal"}


def debug_tool_events_enabled() -> bool:
    return str(os.getenv("HERMES_DEBUG_TOOL_EVENTS", "0")).strip().lower() in _DEBUG_TRUTHY


def is_chat_platform(platform_key: str | None) -> bool:
    return (platform_key or "").lower() not in {"cli", "local", "api_server"}


def should_show_raw_tool_events(platform_key: str | None) -> bool:
    """Return whether gateway progress may expose raw tool names/previews."""
    return True


def _basename_from_args(args: Any) -> Optional[str]:
    if not isinstance(args, dict):
        return None
    for key in ("path", "file_path"):
        val = args.get(key)
        if isinstance(val, str) and val.strip():
            return Path(val).name
    return None


def summarize_tool_started(tool_name: str | None, preview: str | None = None, args: Any = None, *, platform_key: str | None = None) -> Optional[str]:
    name = (tool_name or "").strip()
    if not name or name.startswith("_"):
        return None
    if should_show_raw_tool_events(platform_key):
        try:
            from agent.display import get_tool_emoji, get_tool_preview_max_len
            emoji = get_tool_emoji(name, default="⚙️")
            max_len = int(get_tool_preview_max_len() or 0)
        except Exception:
            emoji = "⚙️"
            max_len = 0
        if preview:
            shown = str(preview)
            if max_len == 0:
                default_len = 0 if (platform_key or "").lower() == "feishu" else 40
                if default_len and len(shown) > default_len:
                    shown = shown[: default_len - 3] + "..."
            elif max_len > 0 and len(shown) > max_len:
                shown = shown[: max_len - 3] + "..."
            return f'{emoji} {name}: "{shown}"'
        return f"{emoji} {name}..."
    if name == "execute_code":
        return "正在执行代码检查……"
    if name == "terminal":
        return "正在执行命令……"
    if name in _EDIT_TOOLS:
        base = _basename_from_args(args)
        return f"正在修改 {base}……" if base else "正在修改文件……"
    if name in {"read_file", "search_files"}:
        return "正在读取项目文件……"
    if name in {"browser_navigate", "browser_click", "browser_type", "browser_snapshot"}:
        return "正在操作浏览器……"
    if name in {"web_search", "web_extract"}:
        return "正在检索资料……"
    return "正在执行下一步……"


def summarize_tool_completed(tool_name: str | None, args: Any = None, *, duration: float | None = None, is_error: bool = False, platform_key: str | None = None) -> Optional[str]:
    name = (tool_name or "").strip()
    if not name or name.startswith("_"):
        return None
    if should_show_raw_tool_events(platform_key):
        suffix = " failed" if is_error else " completed"
        return f"{name}{suffix}"
    if is_error:
        return "步骤执行失败，正在查看错误摘要。"
    if name == "execute_code":
        return "代码检查完成。"
    if name == "terminal":
        return "命令执行完成。"
    if name in _EDIT_TOOLS:
        base = _basename_from_args(args)
        return f"已修改 {base}。" if base else "已修改文件。"
    return None


def merge_progress_lines(lines: list[str]) -> list[str]:
    """Deduplicate and aggregate noisy user-mode progress lines."""
    cleaned: list[str] = []
    counts: Counter[str] = Counter()
    for raw in lines:
        msg = str(raw or "").strip()
        if not msg:
            continue
        # Drop explicitly internal statuses unless debug is on upstream.
        if any(p.search(msg) for p in _INTERNAL_PATTERNS):
            continue
        counts[msg] += 1
        if msg not in cleaned:
            cleaned.append(msg)
    merged: list[str] = []
    for msg in cleaned:
        count = counts[msg]
        m = re.match(r"^已修改\s+(.+?)(?:。)?$", msg)
        if m and count > 1:
            merged.append(f"已修改 {m.group(1)}，共 {count} 处。")
        elif count > 1 and not msg.startswith("正在"):
            merged.append(f"{msg} (×{count})")
        else:
            merged.append(msg)
    return merged


def sanitize_user_status_message(message: str, *, fallback: str = "正在处理任务……") -> str:
    text = str(message or "").strip()
    if not text:
        return fallback
    low = text.lower()
    if "compression summary failed" in low or "invalid x-api-key" in low or "api key" in low and "invalid" in low:
        return "上下文压缩失败：API key 无效，已使用 fallback 继续。"
    if any(p.search(text) for p in _INTERNAL_PATTERNS):
        return fallback
    # Avoid dumping tracebacks/errors to users; logs retain full details.
    if "traceback" in low:
        return "执行过程中出现错误，已记录详细日志。"
    return text


def sanitize_final_response_text(text: str, *, show_debug: bool = False) -> str:
    if show_debug:
        return text
    if not text:
        return text
    # Remove common XML-style hidden reasoning blocks from user-visible content.
    text = re.sub(r"<\s*(?:think|reasoning|REASONING_SCRATCHPAD)\b[^>]*>.*?<\s*/\s*(?:think|reasoning|REASONING_SCRATCHPAD)\s*>", "", text, flags=re.I | re.S)
    text = re.sub(r"^\s*(?:Reasoning|chain_of_thought|internal_analysis)\s*:\s*.*?(?=\n\S|\Z)", "", text, flags=re.I | re.S | re.M)
    return text.strip()
