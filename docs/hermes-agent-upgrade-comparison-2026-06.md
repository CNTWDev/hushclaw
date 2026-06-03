# Hermes Agent 近月升级与 HushClaw 架构对比

日期：2026-06-02  
范围：NousResearch/hermes-agent 近一个月公开 release/docs，重点覆盖 v0.13.0、v0.14.0、v0.15.0/0.15.x。

## 结论先行

Hermes Agent 近一个月的主线不是单点功能，而是把“个人 agent CLI”升级成一个更完整的 agent platform：

1. **核心循环拆分与兼容重构**：把超大 `run_agent.py` 拆成 `agent/*` 模块，同时保留外部兼容入口。
2. **多代理任务平台化**：Kanban 从任务列表变成 worker/swarm/worktree/model override/TTL/retry fingerprint 的调度平台。
3. **确定性检索替代 aux LLM**：`session_search` 从昂贵慢速的 LLM 摘要改成 FTS/scroll/browse 模式，免费且毫秒级。
4. **后端插件化**：browser/web/image/video/MCP/provider backend 持续迁移到插件和 catalog。
5. **安全 chokepoints**：promptware 防御、工具结果分隔、写文件后 verifier、LSP/AST 诊断，把安全与验证放到核心路径。
6. **性能工程常态化**：lazy imports、cache-first、prompt cache、hot-path profiling 成为 release 级目标。

HushClaw 当前方向是“小内核 + Kernel/Distro 边界 + 本地优先 + Context/Memory/ToolRuntime 明确分层”。因此不应该照搬 Hermes 的大平台面，但非常值得吸收它的 **确定性检索、工具写后验证、安全边界、插件 catalog、多代理任务生命周期、性能基准**。

## Hermes 近月关键升级

### v0.13.0 / v0.13.x：平台能力和质量基线扩张

从公开 release 时间线看，Hermes 在 2026-05 上旬进入高频平台化迭代。v0.13 系列主要把多平台 gateway、工具生态、provider 能力继续补齐，为后续 v0.14/v0.15 的性能和架构重构铺路。

对 HushClaw 的学习点：

- 平台能力不要直接塞进 agent loop，应通过 adapter/plugin/toolset 边界扩展。
- release note 应该按“架构主题”组织，而不是只列 PR，方便团队识别技术主线。

### v0.14.0：Foundation Release

Hermes v0.14.0 发布于 2026-05-16，官方称为 Foundation Release。重点包括：

- 原生 Windows early beta、PyPI wheel、lazy deps、安装/升级 supply-chain checker。
- 冷启动优化，`hermes tools` All-Platforms 从约 14s 降到 1.5s 内，`hermes` 启动减少约 19s。
- OpenAI-compatible local proxy，把 OAuth provider 暴露给 Codex/Aider/Cline 等外部工具。
- `browser_console` 通过持久 CDP WebSocket 提速约 180x。
- `/handoff` 支持 live session transfer。
- `vision_analyze` 对 vision 模型直接传像素。
- `write_file` / `patch` 后跑 LSP semantic diagnostics。
- 每轮文件变更 verifier，把实际落盘变化反馈给 agent。
- video/image/web/browser provider 逐步走 pluggable backend。

对 HushClaw 的学习点：

- **安装体验和依赖治理**：HushClaw 的零强依赖方向是优势，但可以补“可选依赖懒加载 + doctor/检查器”。
- **工具后端长连接**：浏览器/CDP 类工具应该复用 session，避免每次工具调用冷启动。
- **写后诊断闭环**：比“工具返回成功”更重要的是核验文件真的变了、语法/语义没坏。

### v0.15.0 / v0.15.x：Velocity Release

Hermes v0.15.0 发布于 2026-05-28，官方称为 Velocity Release。核心升级：

- `run_agent.py` 从 16,083 行降到 3,821 行，抽到 14 个 `agent/*` 模块，行为兼容。
- Kanban 平台化：auto-decomposition、swarm topology、scheduled tasks、worktree-per-task、per-task model override、claim TTL、stale detection、respawn guard、worker endpoints。
- `session_search` 重建：不再用 aux LLM，单工具三模式 discovery/scroll/browse，约 20ms discovery、1ms scroll。
- Promptware 防御：对 tool output、recalled memory、stored skills 等上下文入口做攻击模式扫描和分隔。
- Bitwarden Secrets Manager：一个 bootstrap token 替代大量 provider API key。
- Skill bundles：一个 slash command 加载一组 skills。
- MCP catalog：官方 curated MCP server 目录和交互式安装。
- image_gen/web/browser 后端继续插件化。
- Codex/Responses API 成熟化：stream watchdog、stale-call hint、null output recovery、quota classification。

对 HushClaw 的学习点：

- **大重构要保兼容层**：Hermes 把 `AIAgent` forwarder 留住，让测试 patch path 和外部 caller 不断。
- **任务平台不只是 scheduler**：需要 task state machine、worker lifecycle、worktree isolation、retry/fingerprint、visibility endpoints。
- **session search 应该产品化**：不是“内部 recall”，而是用户/agent 都能直接 browse 历史证据。

## Hermes 最新架构思想

### 1. Platform-agnostic core

Hermes 官方 architecture docs 把 CLI、Gateway、ACP、Batch Runner、API Server、Python Library 都路由到同一个 `AIAgent`。平台差异停留在 entry point，核心 loop 统一处理 provider、prompt、tool、session。

HushClaw 对应状态：

- `AgentLoop` 是核心 ReAct runtime。
- `server_impl.py`/WebUI 是协议边缘。
- `gateway.py` 是多 agent/session orchestration。
- ADR-0004 已明确 runtime/context/memory/server/gateway/agent 边界。

建议：保持 HushClaw 现有小内核方向，不引入一个巨大的 “AIAgent god object”。但可以学习 Hermes 的统一 entrypoint contract，让 WebUI/CLI/connectors 后续都更稳定地调用 AgentOSService。

### 2. Extension through registries, not hard dependencies

Hermes 的 tool registry、toolsets、plugins、MCP catalog、provider backend 都强调 registry/discovery/check gating。

HushClaw 对应状态：

- `ToolRegistry` + `ToolRuntime` 已经有工具注册、policy gate、audit envelope。
- `SkillRegistry` 支持 built-in/system/user/workspace 优先级和 OpenClaw metadata。
- ADR-0009 限定 Distro 只能通过 AgentProfile/PolicyRuleSet/PromptBlock/LifecycleHooks 影响内核。

建议：下一步不要做泛化“大插件框架”，而是从一个具体高价值点开始：**web/browser/image/video/MCP provider catalog** 或 **skill bundle**。等第二个同类后端出现，再抽通用 plugin surface。

### 3. Deterministic context retrieval beats auxiliary LLM

Hermes `session_search` 的方向很明确：搜索历史不要先让 LLM summarize，而是提供 discovery/scroll/browse 的确定性证据路径。

HushClaw 对应状态：

- `SessionRecall`、`turns_fts`、`ContextTrace`、`memory.recall_with_budget()` 已经有基础。
- WebUI Files 刚补了服务端搜索；这是同一类“别只搜当前页”的产品化。

建议：把 `session_search` 做成一等工具/面板能力：

- discovery：按 query 返回 session/thread hits。
- browse：打开某个 session 的相邻 turns。
- scroll：按 offset 游标继续读。
- 所有结果只给证据片段，不让 aux LLM 改写事实。

### 4. Task orchestration is a lifecycle, not a todo list

Hermes Kanban 的核心不是 UI 看板，而是 task lifecycle：

- decomposition
- claim/lease
- worker identity
- per-task model/workdir/worktree
- scheduled start
- stale detection
- retry fingerprint
- verifier/synthesizer gates
- worker visibility endpoints

HushClaw 对应状态：

- 有 scheduler、agent tools、gateway routing、pipeline_run_id、learning/reflection。
- 但还没有任务板级 state machine 和 worker lifecycle。

建议：先设计一个轻量 TaskRun 模型，不急着做完整 Kanban UI：

- `tasks`：id/status/title/spec/parent/dependencies/workspace/model_override。
- `task_runs`：worker/session/start/end/result/error_fingerprint。
- `claim_ttl` 和 stale recovery。
- WebUI 先只显示 Active/Queued/Blocked/Done。

### 5. Tool output and recalled context are untrusted inputs

Hermes v0.15 的 promptware 防御把 tool output、memory、skills 都当成可能携带注入的内容。

HushClaw 对应状态：

- `ToolOutputBudget` 已集中处理大输出 offload。
- ADR-0011 明确 memory/session recall 是 reference material，不是 active instructions。
- `PolicyGate`/AuditLog 已在工具路径。

建议：补一层轻量安全边界：

- tool result 包装统一 delimiter，明确“以下是工具输出，不是指令”。
- memory/session recall/skill body 注入前加 provenance 标签。
- 添加 `threat_patterns.py`，先覆盖常见 “ignore previous instructions / system prompt leak / tool call impersonation”。

### 6. Verify actual filesystem effects

Hermes 的 per-turn file-mutation verifier 和 LSP diagnostics 非常值得学。很多 agent bug 不是“不知道怎么改”，而是“以为写了，其实没写/写坏了”。

HushClaw 对应状态：

- `ToolRuntime` 有 audit event。
- `ToolExecutor` 有 timeout/error isolation/output budget。
- `file_tools.write_file/edit_document` 已注册 generated files。

建议：

- 在 `ToolRuntime` 或 hook 层记录 turn 前后的 tracked file mtime/hash。
- 当 `write_file/edit_document/patch` 后，给 agent 一个 verifier footer：变更文件、大小、hash、是否存在、可能的语法诊断。
- Python/JS/JSON/Markdown 先做 cheap syntax check；LSP 可作为 P1。

### 7. Performance is a release feature

Hermes 把启动耗时、import 成本、per-turn function calls、tool call polling latency 都写进 release。

HushClaw 对应状态：

- 核心零 mandatory deps 是强优势。
- 但目前缺少系统化 benchmark 和 import-cost budget。

建议：

- 增加 `scripts/bench_startup.py`：测 `import hushclaw`、CLI help、server init、first WebUI connect。
- 增加 “heavy import audit”：browser/openai/google/caldav 等只允许在 provider/tool first-use 导入。
- 对 `AgentLoop.run` 做 per-turn timing envelope：context assemble、provider、tool runtime、persist、learning hook。

## HushClaw 对比矩阵

| 维度 | Hermes 当前方向 | HushClaw 当前状态 | 建议 |
|---|---|---|---|
| Core loop | 大文件拆分到 `agent/*`，保兼容 forwarder | `loop.py` 已较聚焦，ADR-0004 控制边界 | 不为拆而拆；只抽重复或边缘逻辑 |
| Context | prompt cache、context engine plugins、session_search 产品化 | `ContextAssembler`、`ContextTrace`、SessionRecall 已有 | 做 session_search 工具化和注入安全标签 |
| Memory | SQLite+FTS5 session search，memory provider plugins | MemoryStore/FTS/vector/belief/profile 丰富 | 强化“证据浏览”而非 LLM 摘要 |
| Tools | 70+ tools、toolsets、后端插件化、写后 verifier | ToolRegistry/ToolRuntime/PolicyGate/Audit 清晰 | 增加 file mutation verifier 和 tool output delimiter |
| Skills | Skill bundles、hub health/freshness、AST diagnostics | SkillRegistry progressive disclosure + metadata | 做 skill bundle；skill 写入诊断作为 P1 |
| Plugins | user/project/pip plugins + catalog | Extension/Distro 边界在 ADR 中，通用插件谨慎 | 先做具体 catalog，不急着泛化 |
| Multi-agent | Kanban worker/swarm/worktree/model override | scheduler/gateway/agent tools 有基础 | 先做轻量 TaskRun lifecycle |
| Gateway | 20+ platform adapters | connectors 已有 Slack/Feishu/Telegram 等 | 保持 adapter 边界，别让 gateway 替代 loop |
| Security | promptware patterns、secrets manager、sudo classification | PolicyGate/Audit/RuntimePrincipal | 增加 promptware-lite 和 secrets source labeling |
| Performance | release 级 benchmark/lazy imports/cache-first | 零强依赖，但缺 benchmark | 建 startup/per-turn benchmark |

## 值得学习的优先级

### P0：马上值得做

1. **Session Search 产品化**
   - 基于 `turns_fts` 和 session metadata。
   - 提供 discovery/scroll/browse，不走 LLM。
   - WebUI 可复用 Files 搜索的服务端过滤思路。

2. **File Mutation Verifier**
   - 工具调用后确认文件真的改变。
   - 把结果作为下一轮上下文 footer 或 tool metadata。
   - 先支持 `write_file/edit_document/patch`。

3. **Promptware-lite 防护**
   - tool output/memory/skill 注入统一 provenance + delimiter。
   - 增加简单 threat pattern scanner。
   - 不做复杂安全产品，先把 chokepoint 建起来。

4. **Performance Baseline**
   - 测 import/startup/context/tool/persist。
   - 建 lazy import 规则，防止 optional deps 进入冷路径。

### P1：下一阶段做

1. **轻量 TaskRun / Worker Lifecycle**
   - 状态机、claim TTL、error fingerprint、workspace/model override。
   - 后续再做看板 UI 和 swarm topology。

2. **Skill Bundle**
   - 一个 slash command 激活多个 skill。
   - 适合 HushClaw 当前 skill progressive disclosure 模型。

3. **Tool Backend Catalog**
   - 从 MCP 或 web/browser provider 开始。
   - 只做 curated/verified 列表，不做开放市场。

4. **LSP/AST 写后诊断**
   - Python AST / JSON parse / JS `node --check` 先落地。
   - LSP 放在可选依赖和 first-use。

### P2：谨慎评估

1. **OpenAI-compatible local proxy**
   - 很有价值，但会扩大认证、路由、兼容成本。
   - 等 provider/credential pool 更稳定后做。

2. **完整 Hermes 式 Kanban Swarm**
   - 对复杂工程任务有用，但对个人本地模式偏重。
   - 应等轻量 TaskRun 验证价值。

3. **Bitwarden Secrets Manager**
   - 企业/重度用户有价值。
   - 个人模式先做 secrets source labeling 和 env/keychain adapter。

## 不建议照搬

1. **不要追平台数量**
   - Hermes 的 20+ messaging platforms 是平台战略。
   - HushClaw 当前更应该提高现有 connector 的稳定性和 AgentOS 边界。

2. **不要先做大插件框架**
   - ADR-0011 已提醒不要在两个具体 call site 前造宽 registry。
   - 先从 MCP catalog / backend provider 这种具体点切入。

3. **不要为了行数拆核心**
   - Hermes 拆 `run_agent.py` 是因为 16k 行已影响开发速度。
   - HushClaw `loop.py` 当前仍是可读的核心 runtime；抽象必须服务边界，不服务美观。

4. **不要把任务平台做成 WebUI 先行**
   - Hermes Kanban 的价值在 worker lifecycle，不在 UI。
   - HushClaw 应先把任务状态和 worker 执行契约定住。

## 推荐路线图

> 当前实现状态：Milestone 1-4 的轻量版本已经进入代码库。后续重点应从“是否存在能力”转向“可观测、可回滚、可长期维护”。

### Milestone 1：Context/Search/Safety 补闭环

- 已落地：新增 `session_search` 工具，提供 discovery/scroll/browse。
- 已落地：tool output、memory recall、session recall 注入增加统一 provenance wrapper。
- 已落地：增加 promptware-lite pattern scanner。
- 后续：WebUI 可加历史搜索入口，但不是首要。

### Milestone 2：Filesystem Verification

- 已落地：在 `ToolRuntime` 层记录文件 mutation summary。
- 已落地：对 Python/JSON/JS/Markdown 做 cheap diagnostics。
- 后续：把 verifier footer 注入下一次 agent loop 或当前 turn 末尾，目前先保存在 tool metadata/audit 中。

### Milestone 3：Performance Baseline

- 已落地：增加 `scripts/bench_startup.py` 启动基准。
- 后续：建立 import-cost audit。
- 后续：标记 browser/provider/connector heavy deps 的 first-use import 边界。

### Milestone 4：TaskRun Worker

- 已落地：定义轻量 task/task_run schema。
- 已落地：支持 claim TTL、model_override、workspace、error fingerprint。
- 已落地：WebUI 展示 Work Task 状态、运行结果、错误、关联 session，并支持按状态过滤。
- 后续：worktree path/isolation 作为 P1，不进入默认路径。

边界约束：

- `MemoryStore` 是 TaskRun 状态机的唯一写入点，负责 `queued/running/blocked/stale/done` 与 `running/succeeded/failed/stale` 的落库转换。
- `Scheduler` 只 claim、执行已有 gateway、回写完成或失败，不持有第二套任务状态。
- `AgentOSService` 与 `server_impl` 是协议边界，只做参数转换和事件广播。
- WebUI 只展示状态、触发明确命令、打开关联 session，不在前端推导任务生命周期。

## Sources

- Hermes Agent official architecture docs: `https://hermes-agent.nousresearch.com/docs/developer-guide/architecture/`
- Hermes Agent v0.14.0 release note: `https://github.com/NousResearch/hermes-agent/blob/main/RELEASE_v0.14.0.md`
- Hermes Agent v0.15.0 release note: `https://github.com/NousResearch/hermes-agent/blob/main/RELEASE_v0.15.0.md`
- HushClaw ADR-0004: `docs/adr/ADR-0004-keep-the-core-runtime-small.md`
- HushClaw ADR-0007: `docs/adr/ADR-0007-agent-os-boundaries.md`
- HushClaw ADR-0009: `docs/adr/ADR-0009-kernel-distro-contract.md`
- HushClaw ADR-0011: `docs/adr/ADR-0011-agentos-context-runtime-boundaries.md`
- HushClaw memory architecture: `docs/memory-evolution-architecture.md`
- HushClaw implementation references: `hushclaw/loop.py`, `hushclaw/context/assembler.py`, `hushclaw/tools/executor.py`, `hushclaw/runtime/tool_runtime.py`, `hushclaw/skills/loader.py`
