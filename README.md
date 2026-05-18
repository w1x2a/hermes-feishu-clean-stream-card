# Hermes Feishu Clean Stream Card Patch

> 为 Hermes Agent 的飞书/Lark Gateway 增加“干净、可更新的长任务进度卡片”。

这个仓库是从本地 Hermes Agent 实例中抽出的 **Feishu/Lark clean stream task card** 补丁包。它的目标是：当用户在飞书里触发一个耗时较长的 Hermes 任务时，不再刷屏输出原始工具调用、调试事件或 provider 内部状态，而是在一张飞书交互卡片里持续展示清晰、可读、经过清洗的任务进度。

## 功能特性

- **飞书长任务进度卡片**：把 Hermes Gateway 的任务生命周期展示成一张可更新卡片。
- **干净的用户可见进度**：显示阶段、动作、耗时、最近进展、完成/失败状态等人类可读信息。
- **原始内部信息清洗**：避免在飞书消息中暴露 `Reasoning`、工具内部参数、traceback、request id、token/key 文本等调试细节。
- **非飞书平台不受影响**：清洗逻辑只针对飞书卡片路径，不全局改变 Telegram/Discord/Weixin 等平台原有进度行为。
- **卡片状态管理**：包含卡片状态对象、最近进展、完成步骤、错误状态、footer 信息等渲染逻辑。
- **集成补丁**：提供 `patches/feishu-card-integration.patch`，用于把卡片模块接入 Hermes Gateway 的 `gateway/run.py` 和 `gateway/platforms/feishu.py`。
- **测试覆盖**：包含飞书卡片核心行为测试，并记录推荐的 Hermes targeted tests。

## 适用场景

适合下面这类 Hermes + Feishu/Lark 使用场景：

1. 用户在飞书 DM 或群聊中让 Hermes 执行长任务；
2. 任务期间 Hermes 会调用多个工具、读写文件、运行命令或等待模型返回；
3. 用户希望看到“还在处理什么”，但不想看到大量原始工具日志；
4. 希望最终消息仍正常发送，同时卡片只作为进度 UI。

## 仓库结构

```text
.
├── gateway/
│   ├── feishu_stream_card.py          # 飞书进度卡片渲染、状态管理、更新辅助逻辑
│   └── progress_sanitizer.py          # 面向用户展示的进度文本清洗器
├── patches/
│   └── feishu-card-integration.patch  # 接入 Hermes Gateway 的补丁
├── tests/
│   └── gateway/
│       └── test_feishu_stream_card.py # 飞书卡片核心测试
├── docs/
│   └── implementation-notes.md        # 实现说明和已知坑
├── README.md
└── .gitignore
```

## 核心文件说明

### `gateway/feishu_stream_card.py`

负责飞书进度卡片的核心逻辑，包括：

- 卡片状态对象；
- 任务阶段、当前动作、最近进展、耗时、完成步骤等字段；
- 成功、失败、超时等终态渲染；
- footer 信息渲染；
- 面向 Feishu/Lark card payload 的结构生成。

### `gateway/progress_sanitizer.py`

负责把 Hermes 内部进度事件转换为用户可读文案，包括：

- 屏蔽或弱化原始工具名和内部事件；
- 过滤 debug/traceback/request id/secret-like 文本；
- 把 provider 等待、工具执行、文件处理等状态转换成更自然的中文进度描述。

### `patches/feishu-card-integration.patch`

这是接入补丁，主要修改：

- `gateway/platforms/feishu.py`
  - 声明/增强飞书消息编辑、交互卡片相关能力；
  - 为卡片发送/更新提供平台侧支持。
- `gateway/run.py`
  - 将飞书平台的进度事件路由到 clean stream card；
  - 避免把飞书 clean-card 模式下的 raw progress 作为普通消息刷屏；
  - 保持最终文本回复与进度卡片的职责分离。

## 安装/集成方式

> 注意：这是一个补丁包，不是独立运行的 Hermes fork。请在 Hermes Agent 源码根目录使用。

假设：

- Hermes Agent 源码在 `/path/to/hermes-agent`；
- 本仓库在 `/path/to/hermes-feishu-clean-stream-card`。

执行：

```bash
cd /path/to/hermes-agent

# 复制新增模块和测试
cp /path/to/hermes-feishu-clean-stream-card/gateway/feishu_stream_card.py gateway/
cp /path/to/hermes-feishu-clean-stream-card/gateway/progress_sanitizer.py gateway/
cp /path/to/hermes-feishu-clean-stream-card/tests/gateway/test_feishu_stream_card.py tests/gateway/

# 应用集成补丁
git apply /path/to/hermes-feishu-clean-stream-card/patches/feishu-card-integration.patch
```

如果你的 Hermes Agent 版本与此补丁生成时的源码差异较大，`git apply` 可能需要人工处理冲突。建议先阅读 patch，再手动迁移对应逻辑。

## 配置示例

在 `~/.hermes/config.yaml` 中启用流式进度和飞书卡片：

```yaml
display:
  streaming: true
  interim_assistant_messages: true
  platforms:
    feishu:
      tool_progress: all
      stream_card: true
```

可选：结合 Hermes Gateway 的任务提醒间隔，让长等待期间卡片也有心跳式更新：

```yaml
agent:
  gateway_notify_interval: 60
  gateway_start_notify_delay: 5
```

## 验证方式

在 Hermes Agent 源码根目录运行：

```bash
PY=.venv/bin/python; test -x "$PY" || PY=venv/bin/python
$PY -m pytest \
  tests/gateway/test_feishu_stream_card.py \
  tests/gateway/test_run_progress_topics.py \
  tests/gateway/test_display_config.py \
  -q

hermes config check
```

本次打包前的本地验证结果：

- `72 passed in 13.55s`
- `hermes config check` 通过
- `patches/feishu-card-integration.patch` 可在当前 Hermes checkout 上应用
- 基础 secret-pattern 扫描通过：`SECRET_SCAN_OK`

## 运行时检查

源码修改和测试通过，不等于正在运行的 Gateway 已加载新代码。修改后需要重启 Hermes Gateway，并用日志确认。

```bash
ps -eo pid,lstart,cmd | grep -E 'hermes gateway' | grep -v grep || true

grep -E 'Feishu stream card|interactive card|response ready|Starting Hermes Gateway|SIGTERM' \
  ~/.hermes/logs/gateway.log | tail -80
```

请区分：

- **源码已修改并测试通过**；
- **运行中的 Gateway 已重启并加载补丁**；
- **飞书实际消息中已经看到卡片更新**。

这三者不是同一件事。

## 设计原则

1. **进度卡片不是最终回复**
   卡片只负责展示任务进度。最终答案仍应作为普通飞书消息发送，不能因为卡片 finalize 就抑制最终回复。

2. **只清洗飞书卡片路径**
   不应为了飞书体验而全局改变其他平台已有的 raw progress 行为。

3. **不泄露内部状态**
   飞书用户可见内容应尽量避免暴露 provider、工具参数、request id、traceback、token/key 等内部细节。

4. **可观测性优先**
   Gateway 日志里应能区分卡片发送成功、更新成功、更新失败、最终回复发送等状态，方便排查“卡片不动”或“最终回复没发”的问题。

## 已知注意事项

- Feishu/Lark 交互卡片的更新接口与普通消息更新接口不同；不同 SDK/API 版本可能需要适配 CardKit 更新方式。
- 如果卡片创建了但不更新，需要检查 Gateway 是否把 start/heartbeat/tool progress 事件真正送入卡片队列。
- 如果最终回复没出现在飞书线程中，要检查是否错误地把“卡片 finalize”当成“最终文本已发送”。
- 公开仓库不包含 `.env`、日志、session、token 或用户数据；只包含源码补丁、测试和文档。

## License

当前补丁来源于本地 Hermes Agent 修改。若要合入上游 Hermes Agent，请按上游项目许可证和贡献规范处理。
