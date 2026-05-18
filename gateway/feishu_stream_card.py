"""Clean Feishu task-card progress renderer.

This module deliberately renders only user-facing task status.  Raw gateway,
model, reasoning, traceback, request-id and tool event details must remain in
logs and never be copied into the card body.
"""
from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from gateway.progress_sanitizer import merge_progress_lines, sanitize_user_status_message

_INTERNAL_RE = re.compile(
    r"(Reasoning|chain_of_thought|internal_analysis|reasoning_content|receiving stream response|"
    r"tool completed:\s*\w+|execute_code|patch|第\s*\d+\s*/\s*\d+\s*轮|"
    r"iteration\s+\d+\s*/\s*\d+|traceback \(most recent call last\)|request_id|api[_ -]?key)",
    re.I,
)

_SENSITIVE_RE = re.compile(
    r"(api[_ -]?key|app[_ -]?secret|token|password|request[_ -]?id)\s*[:=]\s*[^\s,;]+",
    re.I,
)

_STATUS_TEMPLATE = {
    "排队中": "blue",
    "执行中": "blue",
    "等待模型": "wathet",
    "调用工具": "purple",
    "验证中": "turquoise",
    "疑似卡住": "orange",
    "已完成": "green",
    "已失败": "red",
    "已超时": "red",
}

_TERMINAL_EVENTS = {"message.completed", "message.failed", "message.timeout"}
_DEFAULT_FOOTER_FIELDS = ("model", "duration", "status", "tokens", "context")
_ALLOWED_FOOTER_FIELDS = frozenset(_DEFAULT_FOOTER_FIELDS)
_DEFAULT_TITLE = "Hermes 自动任务"


def streaming_enabled() -> bool:
    import os
    raw = os.getenv("HERMES_FEISHU_STREAMING")
    if raw is not None:
        return str(raw).strip().lower() in {"1", "true", "yes", "on"}
    try:
        import yaml
        from hermes_constants import get_hermes_home
        cfg_path = get_hermes_home() / "config.yaml"
        cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
        display = cfg.get("display") or {}
        platform_cfg = (display.get("platforms") or {}).get("feishu") or {}
        if "stream_card" in platform_cfg:
            return str(platform_cfg.get("stream_card")).strip().lower() in {"1", "true", "yes", "on"}
        # Feishu clean cards are the intended UX when streaming/progress is on;
        # no extra env switch should be required after a gateway restart.
        if platform_cfg.get("tool_progress") and platform_cfg.get("tool_progress") != "off":
            return True
        return bool(display.get("streaming") or display.get("interim_assistant_messages"))
    except Exception:
        return False


def _now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _elapsed(seconds: float) -> str:
    seconds = max(0, int(seconds))
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}小时{m}分{s}秒"
    if m:
        return f"{m}分{s}秒"
    return f"{s}秒"


def _fmt_num(value: Any) -> str:
    try:
        return f"{int(value):,}"
    except Exception:
        return "-"


def _safe_positive_int(value: Any) -> int:
    try:
        n = int(value or 0)
    except Exception:
        return 0
    return n if n > 0 else 0


def _sanitize_diagnostic_error(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = _SENSITIVE_RE.sub("[REDACTED]", text)
    text = re.sub(r"(sk-[A-Za-z0-9_-]{8,})", "[REDACTED]", text)
    text = re.sub(r"(cli_[A-Za-z0-9_-]{8,})", "[REDACTED]", text)
    return text[:300]


def _message_from_structured_progress(raw: dict[str, Any]) -> tuple[str, str | None]:
    event = str(raw.get("event") or raw.get("type") or "").strip().lower()
    msg = str(raw.get("message") or raw.get("action") or "").strip()
    tool = str(raw.get("tool") or raw.get("tool_name") or "").strip()
    status: str | None = None
    if event in {"model.waiting", "provider.waiting", "llm.waiting"}:
        status = "等待模型"
        msg = msg or "正在等待模型返回"
    elif event in {"tool.started", "tool.running"}:
        status = "调用工具"
        msg = msg or (f"正在调用工具：{tool}" if tool else "正在调用工具")
    elif event == "tool.completed":
        status = "验证中" if any(k in tool.lower() for k in ("pytest", "test", "check")) else "执行中"
        msg = msg or (f"工具执行完成：{tool}" if tool else "工具执行完成")
    elif event in {"verification.started", "verification.running", "test.running"}:
        status = "验证中"
        msg = msg or "正在验证结果"
    elif event in {"stuck", "stuck.detected", "timeout.warning"}:
        status = "疑似卡住"
        msg = msg or "任务长时间无新进展，疑似卡住"
    elif event == "message.completed":
        status = "已完成"
        msg = msg or "验证通过，任务完成"
    elif event == "message.failed":
        status = "已失败"
        msg = msg or "任务失败，详见日志"
    elif event == "message.timeout":
        status = "已超时"
        msg = msg or "任务超时，详见日志"
    return msg, status


def clean_action_summary(text: Any) -> str:
    raw = str(text or "").strip()
    if not raw:
        return "正在处理任务"
    low = raw.lower()
    if (
        "compression summary failed" in low
        or "上下文压缩失败" in raw
        or "fallback continue" in low
        or ("fallback" in low and "压缩" in raw)
    ):
        return "内部上下文优化已自动跳过，任务继续执行"
    if _INTERNAL_RE.search(raw):
        if "traceback" in low:
            return "步骤执行失败，已记录详细日志"
        if "execute_code" in low or "terminal" in low:
            return "正在执行代码检查"
        if "patch" in low:
            return "正在修改文件"
        return "正在处理任务"
    return sanitize_user_status_message(raw, fallback="正在处理任务")[:300]


def infer_status(action: str) -> str:
    text = action.lower()
    if any(k in text for k in ("验证", "检查完成", "测试", "通过")):
        return "验证中"
    if any(k in text for k in ("等待模型", "模型返回", "fallback")):
        return "等待模型"
    if any(k in text for k in ("执行", "命令", "读取", "修改", "检索", "浏览器")):
        return "调用工具"
    return "执行中"


@dataclass(frozen=True)
class GatewayProgressEvent:
    event: str
    message: str = ""
    sequence: int = 0
    tool: str = ""
    model: Any = None
    input_tokens: Any = None
    output_tokens: Any = None
    context_length: Any = None
    last_prompt_tokens: Any = None

    @classmethod
    def from_raw(cls, raw: Any) -> "GatewayProgressEvent":
        if isinstance(raw, cls):
            return raw
        if isinstance(raw, dict):
            return cls(
                event=str(raw.get("event") or raw.get("type") or "progress"),
                message=str(raw.get("message") or raw.get("action") or ""),
                sequence=_safe_positive_int(raw.get("sequence")),
                tool=str(raw.get("tool") or raw.get("tool_name") or ""),
                model=raw.get("model"),
                input_tokens=raw.get("input_tokens"),
                output_tokens=raw.get("output_tokens"),
                context_length=raw.get("context_length"),
                last_prompt_tokens=raw.get("last_prompt_tokens"),
            )
        return cls(event="progress", message=str(raw or ""), sequence=0)

    @property
    def is_terminal(self) -> bool:
        return self.event.strip().lower() in _TERMINAL_EVENTS

    def as_progress_dict(self) -> dict[str, Any]:
        data = {
            "event": self.event,
            "message": self.message,
            "tool": self.tool,
        }
        if self.model is not None:
            data["model"] = self.model
        if self.input_tokens is not None:
            data["input_tokens"] = self.input_tokens
        if self.output_tokens is not None:
            data["output_tokens"] = self.output_tokens
        if self.context_length is not None:
            data["context_length"] = self.context_length
        if self.last_prompt_tokens is not None:
            data["last_prompt_tokens"] = self.last_prompt_tokens
        return data


@dataclass(frozen=True)
class FeishuCardConfig:
    title: str = _DEFAULT_TITLE
    footer_fields: tuple[str, ...] = _DEFAULT_FOOTER_FIELDS


def resolve_card_config(platform_cfg: dict[str, Any] | None = None) -> FeishuCardConfig:
    cfg = platform_cfg if isinstance(platform_cfg, dict) else {}
    title = str(cfg.get("stream_card_title") or cfg.get("card_title") or cfg.get("title") or "").strip()
    if not title:
        title = _DEFAULT_TITLE
    raw_fields = cfg.get("stream_card_footer_fields") or cfg.get("footer_fields")
    if raw_fields is None:
        fields = _DEFAULT_FOOTER_FIELDS
    elif isinstance(raw_fields, str):
        fields = tuple(x.strip() for x in raw_fields.split(",") if x.strip() in _ALLOWED_FOOTER_FIELDS)
    elif isinstance(raw_fields, (list, tuple)):
        fields = tuple(str(x).strip() for x in raw_fields if str(x).strip() in _ALLOWED_FOOTER_FIELDS)
    else:
        fields = _DEFAULT_FOOTER_FIELDS
    return FeishuCardConfig(title=title[:80], footer_fields=fields or _DEFAULT_FOOTER_FIELDS)


@dataclass
class FeishuCardRuntimeStatus:
    last_card_message_id: str = ""
    last_send_success: bool | None = None
    last_send_error: str = ""
    last_send_at: float = 0.0
    last_edit_success: bool | None = None
    last_edit_error: str = ""
    last_edit_at: float = 0.0
    last_edit_attempts: int = 0

    def record_send(self, *, success: bool, message_id: str = "", error: Any = "") -> None:
        self.last_send_success = bool(success)
        self.last_send_at = time.time()
        if message_id:
            self.last_card_message_id = str(message_id)
        self.last_send_error = "" if success else _sanitize_diagnostic_error(error)

    def record_edit(self, *, success: bool, message_id: str = "", attempts: int = 1, error: Any = "") -> None:
        self.last_edit_success = bool(success)
        self.last_edit_at = time.time()
        self.last_edit_attempts = max(0, int(attempts or 0))
        if message_id:
            self.last_card_message_id = str(message_id)
        self.last_edit_error = "" if success else _sanitize_diagnostic_error(error)

    def snapshot(self) -> dict[str, Any]:
        return {
            "last_card_message_id": self.last_card_message_id,
            "last_send_success": self.last_send_success,
            "last_send_error": self.last_send_error,
            "last_send_at": self.last_send_at,
            "last_edit_success": self.last_edit_success,
            "last_edit_error": self.last_edit_error,
            "last_edit_at": self.last_edit_at,
            "last_edit_attempts": self.last_edit_attempts,
        }


async def edit_card_with_retry(
    adapter: Any,
    *,
    chat_id: str,
    message_id: str,
    card: dict[str, Any],
    runtime_status: FeishuCardRuntimeStatus | None = None,
    max_attempts: int = 3,
    delays: tuple[float, ...] = (1.0, 2.0),
) -> Any:
    attempts = max(1, int(max_attempts or 1))
    last_result: Any = None
    last_error = ""
    for attempt in range(1, attempts + 1):
        try:
            result = await adapter.edit_interactive_card(
                chat_id=chat_id,
                message_id=message_id,
                card=card,
            )
            last_result = result
            if getattr(result, "success", False):
                if runtime_status is not None:
                    runtime_status.record_edit(success=True, message_id=message_id, attempts=attempt)
                return result
            last_error = str(getattr(result, "error", "") or "edit failed")
        except Exception as exc:
            last_error = exc.__class__.__name__
            last_result = type("FeishuCardEditResult", (), {"success": False, "error": last_error})()
        if attempt < attempts:
            delay = delays[min(attempt - 1, len(delays) - 1)] if delays else 0
            if delay > 0:
                await asyncio.sleep(delay)
    if runtime_status is not None:
        runtime_status.record_edit(
            success=False,
            message_id=message_id,
            attempts=attempts,
            error=last_error,
        )
    return last_result


@dataclass
class FeishuTaskCardState:
    title: str = _DEFAULT_TITLE
    total_steps: int = 0
    current_step: int = 0
    status: str = "排队中"
    action: str = "正在处理任务"
    completed: list[str] = field(default_factory=list)
    started_at: float = field(default_factory=time.time)
    last_progress_at: float = field(default_factory=time.time)
    last_render_key: str = ""
    stuck_location: str = ""
    last_known_status: str = "排队中"
    final_summary: str = ""
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    context_length: int = 0
    last_prompt_tokens: int = 0
    footer_fields: tuple[str, ...] = _DEFAULT_FOOTER_FIELDS
    last_sequence: int = -1

    def __post_init__(self) -> None:
        if not self.title:
            self.title = _DEFAULT_TITLE
        self.title = str(self.title)[:80]
        self.footer_fields = tuple(x for x in self.footer_fields if x in _ALLOWED_FOOTER_FIELDS) or _DEFAULT_FOOTER_FIELDS

    def set_runtime_info(
        self,
        *,
        model: Any = None,
        input_tokens: Any = None,
        output_tokens: Any = None,
        context_length: Any = None,
        last_prompt_tokens: Any = None,
    ) -> None:
        if model:
            self.model = str(model)
        if input_tokens is not None:
            self.input_tokens = _safe_positive_int(input_tokens)
        if output_tokens is not None:
            self.output_tokens = _safe_positive_int(output_tokens)
        if context_length is not None:
            self.context_length = _safe_positive_int(context_length)
        if last_prompt_tokens is not None:
            self.last_prompt_tokens = _safe_positive_int(last_prompt_tokens)

    def update_from_progress(self, raw: Any) -> bool:
        event = GatewayProgressEvent.from_raw(raw)
        if self.status in {"已完成", "已失败", "已超时"}:
            return False
        if event.sequence > 0 and event.sequence <= self.last_sequence and not event.is_terminal:
            return False
        if event.sequence > 0:
            self.last_sequence = max(self.last_sequence, event.sequence)
        self.set_runtime_info(
            model=event.model,
            input_tokens=event.input_tokens,
            output_tokens=event.output_tokens,
            context_length=event.context_length,
            last_prompt_tokens=event.last_prompt_tokens,
        )
        msg, explicit_status = _message_from_structured_progress(event.as_progress_dict())
        action = clean_action_summary(msg)
        if not action:
            return False
        if event.event == "message.completed":
            self.complete(action)
            return True
        if event.event == "message.failed":
            self.fail(action)
            return True
        if event.event == "message.timeout":
            self.status = "已超时"
            self.action = action
            self.final_summary = action
            self.last_progress_at = time.time()
            return True
        # Any call here is liveness evidence — refresh the idle timer even if
        # the action text matched the previous tick, so a long-running tool or
        # streaming model response is not mis-flagged as 疑似卡住.
        self.last_progress_at = time.time()
        new_status = explicit_status or infer_status(action)
        if action == self.action and new_status == self.status:
            return False
        self.action = action
        self.status = new_status
        if self.status != "疑似卡住":
            self.last_known_status = self.status
        if not action.startswith("正在") and action not in self.completed:
            self.completed.append(action)
        if self.total_steps == 0:
            self.total_steps = max(1, len(self.completed) + 1)
        self.current_step = min(max(1, len(self.completed) + (0 if self.status == "验证中" else 1)), self.total_steps)
        return True

    def mark_stuck_if_needed(self) -> bool:
        idle = time.time() - self.last_progress_at
        if idle < 60:
            return False
        old = (self.status, self.action)
        self.status = "疑似卡住"
        if not self.stuck_location:
            self.stuck_location = self._guess_stuck_location()
        self.action = f"任务超过 {int(idle)} 秒无新进展，疑似卡住"
        return old != (self.status, self.action)

    def _guess_stuck_location(self) -> str:
        if self.last_known_status in {"等待模型", "调用工具", "验证中"}:
            return self.last_known_status
        if self.status in {"等待模型", "调用工具", "验证中"}:
            return self.status
        text = self.action
        if "模型" in text:
            return "等待模型"
        if any(k in text for k in ("命令", "代码", "工具", "读取", "修改")):
            return "工具调用"
        if "压缩" in text:
            return "上下文压缩"
        return "网关发送"

    def _footer_text(self, now: float) -> str:
        values = {
            "model": f"模型：{self.model or '-'}",
            "duration": f"耗时：{_elapsed(now - self.started_at)}",
            "status": f"状态：{self.status}",
            "tokens": f"Tokens：输入 {_fmt_num(self.input_tokens)} / 输出 {_fmt_num(self.output_tokens)}"
            if self.input_tokens or self.output_tokens else "",
            "context": f"Context：{_fmt_num(self.last_prompt_tokens)} / {_fmt_num(self.context_length)}"
            if self.last_prompt_tokens or self.context_length else "",
        }
        parts = [values[field] for field in self.footer_fields if values.get(field)]
        return " | ".join(parts)

    def complete(self, summary: str = "验证通过，任务完成") -> None:
        self.status = "已完成"
        self.action = clean_action_summary(summary) or "验证通过，任务完成"
        self.final_summary = self.action
        self.last_progress_at = time.time()
        if self.total_steps:
            self.current_step = self.total_steps

    def fail(self, summary: str = "任务失败，详见日志") -> None:
        self.status = "已失败"
        self.action = clean_action_summary(summary) or "任务失败，详见日志"
        self.final_summary = self.action
        self.last_progress_at = time.time()

    def render_card(self, *, include_stop: bool = True) -> dict[str, Any]:
        now = time.time()
        idle = int(now - self.last_progress_at)
        stuck = self.status == "疑似卡住" or idle >= 60
        completed = merge_progress_lines(self.completed[-8:])
        step = f"第 {self.current_step or 1} 步 / 共 {self.total_steps or '?'} 步"
        fields = [
            f"**当前状态：** {self.status}",
            f"**当前步骤：** {step}",
            f"**当前动作：** {self.action}",
            f"**最近一次进展：** {_now_text()}",
            f"**已耗时：** {_elapsed(now - self.started_at)}",
            f"**是否疑似卡住：** {'是' if stuck else '否'}",
            f"**心跳刷新：** {_now_text()}",
        ]
        if stuck:
            fields.append(f"**卡住位置：** {self.stuck_location or self._guess_stuck_location()}")
        if idle >= 120:
            fields.append("**恢复建议：** 停止当前任务 / 继续上一步 / 重新执行当前步骤 / 查看调试日志")
        if completed:
            fields.append("**已完成步骤：**\n" + "\n".join(f"- {x}" for x in completed))
        if self.final_summary:
            fields.append(f"**最终结果：** {self.final_summary}")
        elements: list[dict[str, Any]] = [{"tag": "markdown", "content": "\n\n".join(fields)}]
        elements.append({"tag": "hr"})
        footer = self._footer_text(now)
        if footer:
            elements.append({"tag": "markdown", "content": footer})
        if include_stop and self.status not in {"已完成", "已失败", "已超时"}:
            elements.append({
                "tag": "action",
                "actions": [{
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "停止任务"},
                    "type": "danger",
                    "value": {"hermes_action": "stop_task"},
                }],
            })
        return {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": self.title},
                "template": _STATUS_TEMPLATE.get(self.status, "blue"),
            },
            "elements": elements,
        }

    def render_key(self) -> str:
        return "|".join([
            self.status, str(self.current_step), str(self.total_steps), self.action,
            ";".join(self.completed[-8:]), self.final_summary, self.stuck_location,
            self.last_known_status,
            self.model, str(self.input_tokens), str(self.output_tokens),
            str(self.last_prompt_tokens), str(self.context_length),
            ",".join(self.footer_fields), str(self.last_sequence),
        ])


def resolve_heartbeat_interval(agent_cfg: dict[str, Any] | None = None, env: dict[str, str] | None = None) -> float:
    """Resolve Feishu card heartbeat cadence from config/env.

    ``agent.gateway_notify_interval`` is the normal user-facing knob.  The
    environment override is still honored when explicitly set.  A non-positive
    value disables standalone still-working notifications, but the card keeps a
    safe 60s heartbeat so it remains visibly alive instead of appearing frozen.
    """
    import os

    cfg = agent_cfg if isinstance(agent_cfg, dict) else {}
    try:
        cfg_value = float(cfg.get("gateway_notify_interval", 60) or 60)
    except Exception:
        cfg_value = 60.0
    env_map = env if env is not None else os.environ
    try:
        raw = env_map.get("HERMES_AGENT_NOTIFY_INTERVAL") if env_map is not None else None
        value = float(raw) if raw is not None else cfg_value
    except Exception:
        value = cfg_value
    return max(5.0, value) if value > 0 else 60.0
