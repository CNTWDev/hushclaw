# Hermes Agent 最新迭代与 HushClaw 架构升级评审

日期：2026-08-05  
Hermes 基线：`NousResearch/hermes-agent` `v2026.8.3`（v0.20.0）  
HushClaw 基线：`cc9117a`  
评审范围：Hermes v0.16.0–v0.20.0、HushClaw 内核/上下文/记忆/任务/投递/学习/WebUI 真实代码与测试。

## 一、结论先行

HushClaw 当前不是“缺功能”，而是已经进入第二阶段：**需要把已有能力从可用原型升级成可信闭环**。

最值得从 Hermes 最近四个版本学习的，不是语音、更多渠道、MoA 或庞大的插件面，而是这一条主线：

> 从“agent 能调用工具”升级为“agent 能证明任务完成、能恢复失败、能被用户纠偏、能解释自己学到了什么”。

对 HushClaw 最有价值的五项升级，按优先级排序：

1. **完成契约（Completion Contract）+ 证据账本（Evidence Ledger）**：把“完成了”变成可验证状态，而不是模型文本。
2. **补全 TaskRun 与 Delivery Outbox 的可靠性语义**：原子 claim、lease fencing、依赖、模型/工作区覆盖、崩溃恢复、投递重试。
3. **研究引用从提示词约束升级为 claim→source evidence 校验**：高质量个人助手最重要的信任能力之一。
4. **把自动学习改成“候选知识流水线”**：先记录来源、置信度和冲突，再晋升为长期事实；降低记忆污染和后台模型成本。
5. **按运行时状态机拆解大函数**：`event_stream()`、WebSocket dispatch、MemoryStore 已超过“可靠纪律保持小内核”的临界点。

一句更直接的判断：**HushClaw 的架构思想整体是对的，但“文档里的边界”已经领先于“代码里的边界”**。如果继续横向加功能，系统会越来越像一个有很多优秀局部设计、但难以稳定演进的单体 agent platform。

### 决策矩阵

评分 1–5；“架构杠杆”表示该升级能否同时改善多条产品路径。

| 方向 | 个人助手价值 | 架构杠杆 | 投入 | 决策 |
|---|---:|---:|---:|---|
| Completion Contract + Evidence Ledger | 5 | 5 | 中 | 立即做 |
| TaskRun / Outbox 可靠性闭环 | 5 | 5 | 中–高 | 立即做 |
| Grounded Research Evidence Pack | 5 | 4 | 中 | 立即做 |
| Learning Candidate + 用户治理 | 5 | 4 | 中–高 | 紧随 P0 |
| 压缩保真与 evidence-aware context | 4 | 4 | 中 | P1 |
| 工具自恢复与结构化错误 | 4 | 4 | 中 | P1 |
| 审批反馈/拒绝熔断 | 4 | 3 | 小–中 | P1 |
| 真实性能 SLO | 3 | 4 | 小 | 与每个里程碑同步 |
| 全双工语音 / wake word | 3 | 2 | 高 | 延后 |
| MoA / A2A / 更多渠道 | 1–2 | 2 | 高 | 当前不做 |

### 本次治理型落地（2026-08-05）

本报告的高优先级建议已按“少模块、强约束、保兼容”原则落地第一阶段，不是照单扩功能：

- 新增单一职责 `TaskRunStore`，`MemoryStore` 继续作为兼容 facade；任务 SQL 和生命周期规则不再继续堆入超大类。
- TaskRun 已具备原子 claim、依赖门禁、attempt、lease token、heartbeat、过期 fencing；旧 stale run 无法覆盖新 run。
- completion contract 存在任务 metadata 中，run 持久化 response/tool/artifact evidence，并产生 `verified / rejected / failed / stale` 完成状态。默认普通任务只要求非空 response；需要产物的任务可明确要求 artifact。
- scheduler 已实际消费 task workspace、model override、完成条件和证明策略；模型覆盖只作用于当前 run，不修改共享 Agent 配置。
- Delivery Outbox 已补齐 `pending → in_flight → retry → delivered/dead_letter`、指数退避、启动恢复和已启用 connector 的后台重试。
- 修复统一 `connections` 配置与项目级 legacy override 的优先级倒置，避免本地/项目禁用项被用户级连接配置重新打开。
- WebUI 没有新增“可靠性”导航；Task 页直接呈现运行健康、租约/attempt/证明/证据/投递异常。原独立 Insights 被并入 Memory 子导航，减少重叠心智模型。
- 增加并发 claim、依赖、heartbeat、stale fencing、证据拒绝、workspace/model 生效、outbox recovery/dead-letter、配置优先级和 UI 信息架构回归测试。

验证结果：`761 passed`，Python compileall 与变更 JS syntax check 通过；任务创建表单已用无后端、无外部连接器的静态运行态完成浏览器检查。

刻意未在本次继续扩张的内容：通用 Research claim→source verifier、Learning Candidate 队列、完整 runtime state-machine 拆解。它们仍是后续高价值方向，但应各自作为独立治理里程碑，而不是塞进本次可靠性内核。

## 二、Hermes 6 月以来真正发生了什么

### 2.1 v0.16：产品表面与远程运行

v0.16 的主线是 Desktop、远程 Gateway、管理面和安装体验。对 HushClaw 的启发有限，因为 HushClaw 已经有 WebUI、AgentOS 边界和本地优先定位。值得保留的思想只有：

- GUI 应是运行时的观察与控制面，不应复制一套 agent runtime。
- 本地 UI 与远端执行节点之间需要稳定的事件/认证协议。
- 默认技能和默认工具应持续收敛，而不是只增不减。

来源：[Hermes v0.16.0 release](https://github.com/NousResearch/hermes-agent/releases/tag/v2026.6.5)

### 2.2 v0.17：后台子代理、学习可见性、aux 成本治理

v0.17 最有价值的三点：

- 后台子代理返回 handle，完成后把结果重新送回会话，而不是让主 turn 阻塞。
- 学习系统开始被用户观察和管理，而不是完全黑盒。
- curator 不再为每次 routine run 无条件消耗辅助模型预算。

HushClaw 已有 child run、状态观察、后台 work task 的基础，因此重点不是再加一套 delegation，而是把现有后台任务的生命周期做可靠。

来源：[Hermes v0.17.0 release](https://github.com/NousResearch/hermes-agent/releases/tag/v2026.6.19)

### 2.3 v0.18：从“感觉完成”转向“证据完成”

这是最值得 HushClaw 学习的一次迭代：

- `/goal` 引入 completion contract。
- 编码任务记录 verification evidence；文件发生新修改后，旧验证自动变为 stale。
- agent 结束前检查证据，不满足时触发一次有界的 verify nudge。
- `/learn` 与 `/journey` 让用户看到、编辑和删除 agent 学到的内容。

Hermes 的实现有一个重要边界值得照搬：证据账本是**被动记录器**，不擅自决定跑什么命令；completion guard 才决定是否提示补验证。这样可以避免“为了验证而执行未知脚本”的安全问题。

参考：

- [Hermes v0.18.0 release](https://github.com/NousResearch/hermes-agent/releases/tag/v2026.7.1)
- [verification_evidence.py](https://github.com/NousResearch/hermes-agent/blob/v2026.8.3/agent/verification_evidence.py)
- [verification_stop.py](https://github.com/NousResearch/hermes-agent/blob/v2026.8.3/agent/verification_stop.py)

### 2.4 v0.19：性能、投递可靠性、审批反馈

v0.19 的主线不是算法，而是交互质量：

- 首 turn 初始化耗时显著下降，TTFT 成为 release 指标。
- durable delivery ledger 确保生成完成的回答不会因 Gateway 崩溃而消失。
- 子代理运行过程可实时查看。
- 审批不再只有“允许/拒绝”；拒绝原因会反馈给 agent，且有 deny rule 和连续拒绝熔断。

HushClaw 已有 perf envelope、child-run 可视化和 outbox schema，但投递恢复与审批反馈还未完成闭环。

来源：[Hermes v0.19.0 release](https://github.com/NousResearch/hermes-agent/releases/tag/v2026.7.20)

### 2.5 v0.20：工具自恢复、压缩保真、可核验研究、中途纠偏

v0.20 对 HushClaw 的高价值思想有六项：

- 工具失败返回可执行的恢复提示，而不是把原始异常丢给模型猜。
- patch 识别“已经应用”、空白差异和多重匹配；search 失败时给近似路径；终端截断时把完整输出落盘。
- context compression 保证最近 N 条用户消息、主动裁剪大 tool result，并防止被裁剪 skill 形成“幽灵指令”。
- grounded citations 把引用和原文证据绑定，并支持 fact-check。
- mid-turn redirect 允许用户纠偏，不必停止后从头解释。
- Desktop 的 artifact/plugin workbench 成为工作台，而不只是聊天窗口。

HushClaw 已经实现了其中一部分：runtime amendment、artifact、tool output offload、file mutation verifier、session recall、HTML artifact 质量门。下一步应该补的是“恢复语义”和“证据语义”。

来源：[Hermes v0.20.0 release](https://github.com/NousResearch/hermes-agent/releases/tag/v2026.8.3)

## 三、不要把 Hermes 当成架构模板

Hermes 值得学习的是迭代主题和 chokepoint，不是代码体量。

对 v2026.8.3 源码做 AST 粗检，仍可看到约 6,019 行的 `run_conversation()`、约 20,131 行的 `GatewayRunner` 类、约 5,430 行的 `ContextCompressor` 类。Hermes 的高速社区迭代换来了非常大的维护面。

因此 HushClaw 应采用：

- 学 Hermes 的 completion contract、evidence ledger、tool recovery、durable delivery、学习透明度。
- 不学 Hermes 的平台数量、配置表面积和巨型运行时对象。
- 不因“对标 Hermes”而引入完整 Desktop SDK、A2A、MoA、wake word、几十个 provider/backend。

## 四、HushClaw 当前架构判断

### 4.1 架构思想：方向正确

以下设计应该保留：

- `AgentLoop` 作为唯一 canonical event stream，CLI/WebUI/connector 不复制工具循环。
- Kernel / Distro / Shell / Infra 四层边界。
- `AgentOSService` 作为产品入口，外部 conversation address 与内部 session id 分离。
- stable/dynamic prompt、显式 token budget、长期记忆与 session recall 分离。
- ToolRegistry + ToolRuntime + PolicyGate + audit envelope。
- 零 mandatory dependency、可选能力懒加载、本地 SQLite。
- progressive skill disclosure、tool output artifact offload、HTML artifact 安全预览。
- 753 个测试在当前基线全部通过，说明已有边界不是纸面设计。

### 4.2 实现现实：小内核已经变成“大方法内核”

静态结构粗检结果：

| 位置 | 当前规模 | 风险 |
|---|---:|---|
| `AgentLoop.event_stream()` | 约 1,006 行 | turn 状态、provider、审批、工具、持久化、补偿逻辑耦合 |
| `HushClawServer._dispatch_with_principal()` | 约 893 行 | message routing 与业务能力注册集中在一个分支树 |
| `handle_save_config()` | 约 518 行 | 配置迁移、secret、connector、校验混合 |
| `MemoryStore` | 约 3,941 行 / 158 methods | notes/session/task/calendar/learning/files 等 ownership 混合 |
| `AgentOSService` | 约 821 行 / 80 methods | 新 namespaced API 与旧 forwarding API 同时存在 |

这不是“行数原罪”，而是状态机不可局部证明：一个 turn 的中断、压缩、确认、并行工具和持久化之间很难独立测试；一个新 message type 会继续扩大 dispatch；MemoryStore 的并发与事务规则无法按领域收口。

建议不做目录美化式重构，而是按真实状态机拆：

```text
AgentLoop
  └─ TurnRunner
      ├─ TurnContext / TurnState
      ├─ ProviderRound
      ├─ ToolRound
      ├─ CompletionGuard
      ├─ TurnFinalizer
      └─ EventSink

MemoryStore facade（保兼容）
  ├─ SessionRepository
  ├─ MemoryRepository
  ├─ TaskRepository
  ├─ DeliveryRepository
  └─ LearningRepository
```

原则：先抽拥有独立不变量的对象，再缩短函数；不建立通用 service framework。

## 五、现有能力的真实成熟度

| 能力 | 当前状态 | 判断 |
|---|---|---|
| 确定性 session search | discovery/browse/scroll 已落地 | 成熟，可继续产品化 |
| runtime amendment | 多个 safe point 检查，前端可重新启动修正后的 run | 基本可用；属于“安全点 supersede”，不是真正修改同一次模型流 |
| 文件写后校验 | hash/size/existence + Python AST/JSON/node check | 有价值，但只证明落盘与基础语法，不证明任务完成 |
| tool output budget | 大输出落 artifact，可由 `read_artifact` 取回 | 成熟，建议作为所有大证据的统一载体 |
| promptware-lite | tool output、session recall 有 wrapper 和 pattern scan | 半闭环；长期 memory/referenced content 的处理不完全一致 |
| TaskRun | tasks/runs/TTL/stale/error fingerprint/schema/UI 基础存在 | 半闭环，当前不应默认开启 worker |
| Delivery outbox | enqueue/idempotency/terminal status 存在 | 名为 durable，实为一次性记录；无恢复 worker/退避/dead letter |
| 学习系统 | profile/fact/opinion/reflection/belief 多路抽取 | 能力强，但成本、来源治理、冲突和持久调度不足 |
| research runtime | batch search/read/dedup/cache | 检索成熟；引用真实性仍主要由 skill prompt 约束 |
| 性能观测 | turn perf 字段和 startup script 存在 | 基础良好；缺真实 SLO、历史基线和 CI regression gate |

## 六、必须优先升级的能力

### P0-1：Completion Contract + Evidence Ledger

这是最高价值升级，且不应只服务编码任务。

建议引入：

```text
TaskContract
  objective
  acceptance_criteria[]
  required_evidence[]
  completion_policy = advisory | strict

EvidenceEvent
  run_id / task_id / session_id
  kind = test | file | delivery | calendar | research | external_action
  subject / scope
  status = passed | failed | stale | unknown
  source_tool / artifact_id / external_id
  created_at / invalidated_at
```

运行规则：

1. 工具成功只产生 evidence，不直接等于任务成功。
2. 文件再次修改后，与该文件/工作区相关的旧测试证据变 stale。
3. agent 准备结束时，`CompletionGuard` 对 contract 做确定性检查。
4. 缺证据时最多追加一次有界 nudge；禁止无限“再验证一次”循环。
5. 未声明的 shell verify command 不自动运行；个人模式默认 advisory。

用户价值：减少“说完成但没完成”、错误日程、未投递消息、空文件、未经验证的报告，是个人助手信任感的核心。

### P0-2：修正 TaskRun 的并发与字段空转

当前代码已经存储 `dependencies`、`workspace`、`model_override`、`claim_expires_at`、`error_fingerprint`，但 worker 路径没有完整消费这些语义：

- scheduler 执行时未把 task 的 `workspace` 传入 `AgentOSMessageRequest`。
- `model_override` 没有应用到该 run。
- dependencies 没有在 claim 前检查。
- `claim_task()` 是先 SELECT 再 UPDATE，多个 worker 可能同时 claim。
- lease 过期只标 stale；旧 worker 没有 fencing token，之后仍可能完成并覆盖新 attempt。
- error fingerprint 被记录，但尚未用于重复失败/respawn guard。

建议顺序：

1. 原子 `UPDATE ... WHERE status IN (...) RETURNING` claim。
2. 给 run 增加 `attempt`/`lease_token`；heartbeat 和 finish 都带 token。
3. finish 时必须满足 `status=running AND lease_token=?`，旧 worker 无权落终态。
4. claim 前确定性检查 dependencies。
5. 真正应用 workspace/model/agent override。
6. 同 fingerprint 连续失败 N 次转 blocked，等待用户处理。
7. completion contract 通过后才允许 succeeded。

### P0-3：让 Delivery Outbox 真正 durable

当前 `next_attempt_at` 已进 schema，但没有 pending/retry worker；失败后直接 `failed`，进程重启也不会恢复。

建议状态机：

```text
pending -> inflight -> delivered
                  \-> retry_wait -> inflight
                               \-> dead_letter
```

必须具备：

- 启动时扫描 pending/inflight-timeout/retry_wait。
- 指数退避 + jitter + 最大尝试次数。
- provider/account 级并发限制。
- idempotency key 贯穿 adapter；无法幂等的平台至少记录 ambiguity。
- final response/event 先持久化，再入 outbox，再发送。
- delivered receipt 成为 completion evidence。

### P0-4：Grounded Research Evidence Pack

当前 deep-research skill 规定“不要编造引用”，但这是软约束。建议让 `research_web` 输出机器可用的 evidence pack：

```text
SourceSnapshot(url, title, fetched_at, content_hash, artifact_id)
EvidenceSpan(source_id, start/end 或 quote_hash, excerpt)
Claim(text, evidence_span_ids[], confidence, contradiction_ids[])
```

输出前执行：

- URL 必须来自实际 retrieval result。
- 引用短句必须能在 snapshot 中归一化匹配。
- 每个高置信事实至少有一个 evidence span。
- 无法验证的内容降级为 inference/uncertain，不允许伪装成事实。
- snapshot 作为 artifact 保存，避免网页变化后无法复核。

这比继续增加搜索 provider 更能提升报告质量。

## 七、高价值 P1 升级

### P1-1：把自动学习改成候选知识流水线

当前普通 turn 可能触发 profile、fact、opinion 三路辅助模型调用，另有 title、reflection、belief consolidation。部分任务通过 `asyncio.create_task()` 发出，没有 durable queue、统一预算或进程重启恢复。

建议改为：

```text
Interaction
  -> deterministic signal capture
  -> learning_candidate（带 source_message_id / extractor / confidence）
  -> dedup + contradiction check
  -> batch consolidation
  -> promoted memory/profile/opinion
```

关键规则：

- “用户明确说出的稳定偏好”可自动晋升；模型推断默认是 candidate。
- 每条长期事实必须可回到原消息。
- 同一事实冲突时保留演化，不覆盖旧值。
- 给用户展示“最近学到的内容”，支持 approve/edit/delete/never learn this。
- 每 session/每天有 aux token 和并发预算；优先批处理。
- 学习 job 持久化，失败可重试，关闭进程不丢任务。

### P1-2：压缩从“缩短文本”升级为“保护任务状态”

HushClaw 已有 tool-result pruning、最近 user turn 保留和 working state reinjection。建议补：

- 单独配置 `min_tail_user_messages`，与通用 keep turns 解耦。
- summary 使用结构化 schema：Goal / Decisions / Constraints / Completed / Open Loops / Evidence / Active Skills。
- tool result 被裁剪时保留 artifact handle、工具名、状态和关键 metadata。
- 记录 summary 的 source turn 范围，便于用户或 agent 回查。
- skill 被移出活跃上下文时写明确 tombstone，防止 summary 把旧 skill 指令继续当 active instruction。
- 先做自适应 proactive prune；micro-compaction 只在长会话指标证明值得时启用。

### P1-3：工具错误要面向恢复，不只面向日志

统一 `ToolError`：

```text
code / category / retryable / user_action_required
diagnosis / suggested_next_actions[] / artifact_id
```

优先覆盖：

- patch：already-applied、whitespace mismatch、ambiguous matches。
- search/read：零结果时给相近路径/建议 query，不自动扩大到危险范围。
- terminal：截断前先将完整结果落 artifact；返回原始字节/行数。
- auth/rate limit/context too long：保持现有 error taxonomy，并把恢复动作机器化。
- 重复相同失败调用触发 no-progress guard。

### P1-4：审批反馈与熔断，不默认 LLM 自动审批

不建议照搬 Hermes 的“smart approvals default”。对本地个人助手，先做：

- 用户拒绝时可附 reason，并作为下一轮明确控制信号。
- 同一 run 连续 N 次 denied 后终止工具循环。
- 从历史人工批准生成 allowlist **建议**，由用户确认后生效。
- allow rule 绑定 command shape + workspace + principal，不绑定模糊自然语言。
- 审批记录可撤销、可审计。

### P1-5：建立真实性能 SLO

当前 `scripts/bench_startup.py` 只测 import/CLI help；一次本地基线约为：`import hushclaw.loop` 70 ms、CLI help 3 ms。真正影响体验的是：

- cold/warm server ready。
- first turn context assemble。
- provider request 发出前延迟、首 provider event、first visible chunk。
- FTS/session recall P50/P95。
- long transcript WebUI render/scroll。
- 学习后台任务 CPU、并发和 aux token。

建议将 perf envelope 落入可聚合表，并在 CI 做宽松 regression gate，而不是追求单次绝对值。

## 八、架构上应该大胆调整的地方

### 8.1 暂停横向平台扩张两个迭代

在 P0 闭环完成前，不建议优先新增渠道、provider、企业域或通用插件框架。新增表面会放大 outbox、approval、task、identity 尚未闭环的成本。

### 8.2 `AgentLoop` 应从“大过程”变为“显式状态机”

不是把 1,006 行机械切成十个 helper，而是引入可持久/可测试的 `TurnState`：

```text
PREPARING -> MODEL -> TOOLS -> MODEL -> FINALIZING -> COMPLETED
                  \-> WAITING_USER
                  \-> SUPERSEDED
                  \-> FAILED
```

每次 transition 产生统一事件；approval、amendment、timeout、compaction 都是状态转换，不再是散落的 flags 和 break。

### 8.3 MemoryStore 保留 facade，但内部按事务所有权拆仓库

现有 `MemoryStore` 兼容面可以保留，避免大迁移；但新代码禁止继续向 158-method 类直接加方法。先迁移 Task/Delivery/Learning 三块，因为它们最需要独立事务和后台 worker。

### 8.4 AgentOSService 应完成 namespaced API 迁移

当前已经有 `sessions`、`memory`、`tasks` 等 namespaced facade，同时仍保留大量同名 forwarding methods。应给旧入口明确 deprecation window，并用架构测试阻止新代码调用 legacy facade。

### 8.5 文档必须由运行时 truth 反向校验

当前文档已有漂移，例如 `CLAUDE.md` 声称 memory creativity 默认开启，而 schema 实际默认 `memory_decay_rate=0.0`、`retrieval_temperature=0.0`；README 同一页也同时出现 0.002/0.1/0.10 与 0.0 示例。

建议把以下内容改为测试生成/校验：默认值表、内置工具列表、事件类型、extension surface、schema version。架构文档只写决策，不重复易漂移的事实。

## 九、建议路线图

### Milestone A（1–2 周）：可信完成基础

- 定义 TaskContract / EvidenceEvent。
- 先接入 file mutation、test command、artifact validation、delivery receipt。
- 增加 CompletionGuard 和一次性 verify nudge。
- 加 evidence stale/invalidation 测试。

验收：agent 不能在 required evidence 缺失时把 run 标记为 succeeded；普通聊天不受影响。

### Milestone B（1–2 周）：TaskRun + Outbox 可靠性

- 原子 claim、lease token、heartbeat、fenced finish。
- dependencies、workspace/model override 生效。
- outbox retry worker、startup recovery、dead letter。
- 后台结果通过 durable delivery 回到原 conversation。

验收：进程在“模型完成后/发送前”崩溃，重启后只投递一次；stale worker 无法覆盖新 run。

### Milestone C（1–2 周）：研究与学习可信度

- Research Evidence Pack + citation verifier。
- learning_candidate、来源回链、冲突标记、批处理预算。
- WebUI 增加“最近学到”审核入口。

验收：每条高置信研究结论可打开原证据；错误用户画像可一键纠正且不会立即被后台任务写回。

### Milestone D（持续）：运行时拆解与性能门禁

- 抽 TurnState/TurnRunner/Finalizer，不改变 event schema。
- 抽 Task/Delivery/Learning repositories，MemoryStore facade 保兼容。
- dispatch 改成注册表 + 小 handler。
- TTFT、长会话、学习预算建立 regression dashboard。

验收：`event_stream()` 只负责协调，核心 transition 可独立单测；753 个现有测试继续通过。

## 十、不建议当前投入

| 方向 | 原因 |
|---|---|
| 完整 MoA / model council | 成本高，普遍任务收益不如证据与检索闭环 |
| A2A v1.0 | 当前主要用户价值不依赖跨框架 agent federation |
| 全双工语音 + wake word | 有产品吸引力，但会引入音频状态机、隐私与跨平台依赖；排在可靠性之后 |
| 更多消息渠道 | 会放大投递、身份、审批和媒体兼容债务 |
| 通用 Desktop plugin SDK | 现有 WebUI/artifact 已足够验证工作台方向 |
| 完整 Hermes Kanban swarm | HushClaw 先把轻量 TaskRun 做正确，再决定是否需要拓扑和 worktree fleet |
| 默认 LLM 自动审批 | 对个人设备上的 shell/file/external action 风险过高 |

## 十一、最终判断

HushClaw 最有竞争力的部分不是“比 Hermes 更全”，而是：

- 更小、更本地、更可读的 Python runtime。
- 记忆/用户模型/信念演化比一般 agent 更深入。
- 已有 AgentOS、tool policy、artifact、session recall、runtime amendment 等正确基础。

下一阶段产品定位应从：

> 一个会记住你的个人 agent

升级为：

> 一个会记住你、能证明自己做完了、失败后能恢复、并且允许你检查它学到了什么的个人助手。

这条路线比继续堆渠道、工具和“智能体数量”更能形成高质量个人助手的长期壁垒。
