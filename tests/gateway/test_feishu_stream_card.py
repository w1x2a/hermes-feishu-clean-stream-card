import json
from pathlib import Path

import pytest

from gateway.feishu_stream_card import (
    FeishuCardRuntimeStatus,
    FeishuTaskCardState,
    GatewayProgressEvent,
    clean_action_summary,
    edit_card_with_retry,
    resolve_card_config,
    resolve_heartbeat_interval,
    streaming_enabled,
)


def test_clean_action_summary_removes_internal_event_words(monkeypatch):
    monkeypatch.delenv("HERMES_FEISHU_STREAMING", raising=False)
    assert clean_action_summary("tool completed: execute_code") == "正在执行代码检查"
    assert clean_action_summary("Reasoning: hidden") == "正在处理任务"
    assert clean_action_summary("上下文压缩失败：API key 无效") == "内部上下文优化已自动跳过，任务继续执行"
    assert clean_action_summary("Context compression summary failed (404). fallback continue") == "内部上下文优化已自动跳过，任务继续执行"


def test_card_state_renders_clean_task_card(monkeypatch):
    monkeypatch.setenv("HERMES_FEISHU_STREAMING", "true")
    state = FeishuTaskCardState(title="任务 A", total_steps=3)
    assert streaming_enabled() is True
    assert state.update_from_progress("正在执行代码检查") is True
    assert state.update_from_progress("已修改 gateway/run.py，共 3 处") is True
    card = state.render_card()
    text = json.dumps(card, ensure_ascii=False)
    assert "任务 A" in text
    assert "当前状态" in text
    assert "执行中" in text or "调用工具" in text
    assert "execute_code" not in text
    assert "Reasoning" not in text


def test_card_state_can_render_without_stop_button_for_progress_updates(monkeypatch):
    monkeypatch.setenv("HERMES_FEISHU_STREAMING", "true")
    state = FeishuTaskCardState(title="任务 A", total_steps=3)
    card = state.render_card(include_stop=False)
    text = json.dumps(card, ensure_ascii=False)
    assert "停止任务" not in text
    assert "action" not in text


def test_card_state_marks_stuck_and_finalizes(monkeypatch):
    monkeypatch.setenv("HERMES_FEISHU_STREAMING", "true")
    state = FeishuTaskCardState(title="任务 B", total_steps=3)
    state.update_from_progress("等待模型返回")
    state.last_progress_at -= 70
    state.mark_stuck_if_needed()
    card = state.render_card()
    text = json.dumps(card, ensure_ascii=False)
    assert "疑似卡住" in text
    assert "卡住位置" in text
    state.complete("验证通过，任务完成")
    card2 = state.render_card(include_stop=False)
    text2 = json.dumps(card2, ensure_ascii=False)
    assert "已完成" in text2
    assert "停止任务" not in text2


def test_resolve_heartbeat_interval_uses_gateway_notify_interval(monkeypatch):
    monkeypatch.delenv("HERMES_AGENT_NOTIFY_INTERVAL", raising=False)
    assert resolve_heartbeat_interval({"gateway_notify_interval": 12}, env={}) == 12
    assert resolve_heartbeat_interval({"gateway_notify_interval": 1}, env={}) == 5
    assert resolve_heartbeat_interval({"gateway_notify_interval": 60}, env={"HERMES_AGENT_NOTIFY_INTERVAL": "7"}) == 7
    assert resolve_heartbeat_interval({"gateway_notify_interval": 0}, env={}) == 60


def test_card_state_footer_renders_model_elapsed_status_and_token_context(monkeypatch):
    monkeypatch.setenv("HERMES_FEISHU_STREAMING", "true")
    state = FeishuTaskCardState(title="任务 C", total_steps=2)
    state.set_runtime_info(
        model="gpt-5.5",
        input_tokens=1234,
        output_tokens=56,
        context_length=200000,
        last_prompt_tokens=18000,
    )
    state.started_at -= 65
    state.update_from_progress("等待模型返回")
    card = state.render_card(include_stop=False)
    text = json.dumps(card, ensure_ascii=False)
    assert "模型：gpt-5.5" in text
    assert "状态：等待模型" in text
    assert "耗时：1分5秒" in text or "耗时：1分" in text
    assert "Tokens：输入 1,234 / 输出 56" in text
    assert "Context：18,000 / 200,000" in text


def test_card_state_accepts_structured_activity_and_detects_fine_statuses(monkeypatch):
    monkeypatch.setenv("HERMES_FEISHU_STREAMING", "true")
    state = FeishuTaskCardState(title="任务 D", total_steps=2)
    assert state.update_from_progress({
        "event": "model.waiting",
        "message": "第 1/90 轮，正在等模型返回，不是卡死",
        "model": "gpt-5.5",
    }) is True
    assert state.status == "等待模型"
    assert state.model == "gpt-5.5"
    assert state.update_from_progress({"event": "tool.started", "tool": "terminal"}) is True
    assert state.status == "调用工具"
    assert state.update_from_progress({"event": "verification.started", "message": "正在运行 targeted tests"}) is True
    assert state.status == "验证中"
    state.last_progress_at -= 70
    assert state.mark_stuck_if_needed() is True
    assert state.status == "疑似卡住"
    assert state.stuck_location == "验证中"


def test_gateway_progress_event_rejects_stale_non_terminal_events(monkeypatch):
    monkeypatch.setenv("HERMES_FEISHU_STREAMING", "true")
    state = FeishuTaskCardState(title="任务 E", total_steps=3)
    assert state.update_from_progress(GatewayProgressEvent(sequence=2, event="tool.started", message="正在调用工具：terminal", tool="terminal")) is True
    assert state.status == "调用工具"
    assert state.update_from_progress(GatewayProgressEvent(sequence=1, event="model.waiting", message="正在等待模型返回")) is False
    assert state.status == "调用工具"
    assert state.last_sequence == 2


def test_gateway_progress_event_terminal_events_can_finalize_after_newer_progress(monkeypatch):
    monkeypatch.setenv("HERMES_FEISHU_STREAMING", "true")
    state = FeishuTaskCardState(title="任务 F", total_steps=3)
    assert state.update_from_progress(GatewayProgressEvent(sequence=10, event="tool.started", message="正在调用工具：terminal", tool="terminal")) is True
    assert state.update_from_progress(GatewayProgressEvent(sequence=9, event="message.completed", message="验证通过，任务完成")) is True
    assert state.status == "已完成"
    assert state.final_summary == "验证通过，任务完成"
    assert state.update_from_progress(GatewayProgressEvent(sequence=11, event="tool.started", message="这个事件不应覆盖终态")) is False
    assert state.status == "已完成"


def test_resolve_card_config_supports_footer_fields_and_title():
    cfg = resolve_card_config({
        "title": "自定义标题",
        "footer_fields": ["duration", "model"],
    })
    assert cfg.title == "自定义标题"
    assert cfg.footer_fields == ("duration", "model")

    fallback = resolve_card_config({"title": "", "footer_fields": ["unknown", "context"]})
    assert fallback.title == "Hermes 自动任务"
    assert fallback.footer_fields == ("context",)


def test_card_state_uses_configurable_footer_fields(monkeypatch):
    monkeypatch.setenv("HERMES_FEISHU_STREAMING", "true")
    state = FeishuTaskCardState(title="任务 G", total_steps=2, footer_fields=("duration", "model"))
    state.set_runtime_info(
        model="gpt-5.5",
        input_tokens=1234,
        output_tokens=56,
        context_length=200000,
        last_prompt_tokens=18000,
    )
    card = state.render_card(include_stop=False)
    text = json.dumps(card, ensure_ascii=False)
    assert "模型：gpt-5.5" in text
    assert "耗时：" in text
    assert "Tokens：" not in text
    assert "Context：" not in text


class _FakeEditResult:
    def __init__(self, success, error=""):
        self.success = success
        self.error = error


class _FlakyAdapter:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    async def edit_interactive_card(self, **kwargs):
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


@pytest.mark.asyncio
async def test_edit_card_with_retry_retries_terminal_edit_until_success():
    adapter = _FlakyAdapter([
        _FakeEditResult(False, "temporary"),
        RuntimeError("network"),
        _FakeEditResult(True),
    ])
    runtime = FeishuCardRuntimeStatus()
    result = await edit_card_with_retry(
        adapter,
        chat_id="oc_xxx",
        message_id="card_xxx",
        card={"config": {}},
        runtime_status=runtime,
        max_attempts=3,
        delays=(0, 0),
    )
    assert result.success is True
    assert adapter.calls == 3
    assert runtime.last_edit_success is True
    assert runtime.last_edit_attempts == 3
    assert runtime.last_card_message_id == "card_xxx"


@pytest.mark.asyncio
async def test_edit_card_with_retry_records_failure_after_all_attempts():
    adapter = _FlakyAdapter([
        _FakeEditResult(False, "temporary"),
        _FakeEditResult(False, "still bad"),
    ])
    runtime = FeishuCardRuntimeStatus()
    result = await edit_card_with_retry(
        adapter,
        chat_id="oc_xxx",
        message_id="card_xxx",
        card={"config": {}},
        runtime_status=runtime,
        max_attempts=2,
        delays=(0,),
    )
    assert result.success is False
    assert adapter.calls == 2
    assert runtime.last_edit_success is False
    assert runtime.last_edit_error == "still bad"
    assert runtime.last_edit_attempts == 2


def test_card_runtime_status_records_send_results_without_sensitive_payload():
    runtime = FeishuCardRuntimeStatus()
    runtime.record_send(success=True, message_id="card_123")
    assert runtime.last_send_success is True
    assert runtime.last_card_message_id == "card_123"
    runtime.record_edit(success=False, message_id="card_123", attempts=1, error="api_key=SECRET request_id=abc")
    snapshot = runtime.snapshot()
    assert snapshot["last_edit_success"] is False
    assert snapshot["last_card_message_id"] == "card_123"
    assert "SECRET" not in json.dumps(snapshot, ensure_ascii=False)
    assert "api_key" not in json.dumps(snapshot, ensure_ascii=False).lower()


def test_gateway_run_wires_feishu_clean_card_runtime():
    run_py = Path(__file__).resolve().parents[2] / "gateway" / "run.py"
    source = run_py.read_text(encoding="utf-8")
    assert "FeishuTaskCardState" in source
    assert "send_interactive_card" in source
    assert "edit_card_with_retry" in source
    assert "Feishu stream card sent" in source
