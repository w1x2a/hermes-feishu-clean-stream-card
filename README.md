# Hermes Feishu Clean Stream Card Patch

这是从本地 Hermes Agent 实例中抽出的 **飞书/Lark 长任务清洁进度卡片**补丁包，用于把 Gateway 的原始工具/调试进度消息整理成一个可编辑的飞书任务卡片。

## 包含内容

- `gateway/feishu_stream_card.py`：飞书进度卡片渲染、状态管理、CardKit 更新辅助逻辑。
- `gateway/progress_sanitizer.py`：面向用户展示的进度文本清洗器，避免泄露原始工具/调试信息。
- `patches/feishu-card-integration.patch`：对 Hermes 主仓库现有文件的集成补丁，当前覆盖：
  - `gateway/platforms/feishu.py`
  - `gateway/run.py`
- `tests/gateway/test_feishu_stream_card.py`：卡片渲染/状态/清洗相关测试。

## 适用场景

用户在飞书中触发 Hermes 长任务时，希望看到：

- 一个持续更新的任务卡片，而不是多条原始工具调用气泡；
- 清洗后的阶段、动作、耗时、最近进展；
- 最终完成/失败状态；
- 尽量不暴露 `Reasoning`、工具内部参数、traceback、请求 ID、密钥等内部细节。

## 集成方式

在 Hermes Agent 源码根目录执行：

```bash
cp -r gateway tests /path/to/hermes-agent/
cd /path/to/hermes-agent
git apply /path/to/this-repo/patches/feishu-card-integration.patch
```

然后检查配置中是否启用飞书进度卡片，例如：

```yaml
display:
  streaming: true
  interim_assistant_messages: true
  platforms:
    feishu:
      tool_progress: all
      stream_card: true
```

## 验证

在 Hermes Agent 源码根目录运行：

```bash
PY=.venv/bin/python; test -x "$PY" || PY=venv/bin/python
$PY -m pytest tests/gateway/test_feishu_stream_card.py tests/gateway/test_run_progress_topics.py tests/gateway/test_display_config.py -q
hermes config check
```

## 运行时注意

源码/测试通过不代表当前运行中的 Gateway 已加载补丁。修改后需要重启 Gateway，并用日志确认：

```bash
ps -eo pid,lstart,cmd | grep -E 'hermes gateway' | grep -v grep || true
grep -E 'Feishu stream card|interactive card|response ready|Starting Hermes Gateway|SIGTERM' ~/.hermes/logs/gateway.log | tail -80
```

## 安全说明

本仓库只打包飞书卡片相关源码、测试与补丁；不应包含 `.env`、token、日志、会话记录或用户数据。发布前已进行基础 secret-pattern 扫描。
