# ADR-0010: Federated Knowledge Architecture for Multi-User Collaboration

**Status:** Accepted  
**Date:** 2026-05-12  
**Supersedes:** N/A  
**Related:** ADR-0008 (Distro Boundary), ADR-0009 (Kernel-Distro Contract)

---

## Context

ADR-0009 定义了个人版 (`PersonalDistro`) 的最小内核模型。  
随着用户开始询问「如何支持组织/多人协同模式」，我们面临一个核心架构抉择：

**方案 A（统一存储多租户）：** 把 `org_id`、`workspace_members` 表穿透到底层 `notes` 表，
在同一个 SQLite/Postgres 数据库里用 scope + org_id 做租户隔离。

**方案 B（联邦模型）：** 个人版保持纯粹，每个人的 HushClaw 是独立实例，
多人协同通过 Connector 协议层在外面组合，共享知识放在独立部署的 Knowledge Hub 服务。

---

## Decision

**选择方案 B：联邦模型（Federated, Protocol-Based）。**

---

## Rationale

### 1. 个人版核心不变性

`SINGLE_USER_PRINCIPAL` 语义上就是「永远只有一个 owner」。  
如果引入 `org_id`：
- `recall()` 的「全局」变成「这个 org 的全局」，语义污染
- MemoryStore 每个查询都要带 org_id filter
- 个人部署永远带着永远不会被激活的多租户代码

### 2. 隐私边界即物理边界

联邦模型中隐私保证来自物理隔离：
- `~/.hushclaw/memory.db` 只属于一个 human，Hub 永远看不到
- 用户**主动** promote 才会让内容离开本地
- 不存在「忘记加 visibility=private 导致泄露」的风险

### 3. 团队能力是加法，不是改造

个人版不需要知道组织的存在：
- schema 不变
- 查询不变  
- 代码不变

KnowledgeConnector 是可选组件，个人用户零感知。

---

## Architecture

```
Personal HushClaw (Alice)                  Team Knowledge Hub
├── ~/.hushclaw/memory.db ◄── 私有, Hub 不可见
├── AgentLoop
│    └── ToolRegistry
│         ├── recall          ← 只查本地 DB（默认）
│         └── recall(include_shared=True)  ← 可选查询 Hub
└── KnowledgeConnector ──────────────────► GET /knowledge/search
                        ──────────────────► POST /knowledge/share  (授权)

Personal HushClaw (Bob)
└── KnowledgeConnector ──────────────────► 同一个 Hub
```

Hub 可以是：
- `hushclaw serve --distro team`（最轻量，复用现有内核）
- 专用 FastAPI 服务
- 任何实现 `/knowledge/*` 端点的服务

---

## Hub API Contract

```
GET  /knowledge/search?q=<query>&scope=<scope>&limit=<n>
     → {"results": [{"note_id": str, "title": str, "body": str, "scope": str}]}

POST /knowledge/share
     {content, title, tags, scope, source_principal_id}
     Authorization: Bearer <token>
     → {"note_id": str}

GET  /knowledge/policy
     → {"policies": [{"scope": str, "readable_by": list[str], "writable_by": list[str]}]}
```

---

## Knowledge Flow Protocols

**个人 → 团队（主动上升）：**  
用户或工具调用 `os_api.promote_to_hub(note_id, scope="team:backend")`  
→ KnowledgeConnector.write_shared → Hub 验证 token → 写入 Hub DB  
→ 本地记录保留（不删除），Hub 返回 hub_note_id

**团队 → 个人（被动拉取）：**  
`recall(query, include_shared=True)` → KnowledgeConnector.read_shared  
→ Hub 结果追加到本地结果，标记 `[Hub:]` 前缀  
→ Hub 故障时降级为纯本地结果

**管理员 → 全员（主动下放）：**  
管理员向 Hub POST 组织规范 → 各成员 KnowledgeConnector 定期拉取 sync_policy  
→ 写入本地只读 scope（note_type="policy"）

---

## Implementation

### 新增文件

- `hushclaw/connectors/knowledge.py` — `KnowledgeConnector`（stdlib only，无额外依赖）
- `docs/adr/ADR-0010-federated-knowledge.md`（本文档）

### 修改文件

| 文件 | 修改内容 |
|------|---------|
| `hushclaw/config/schema.py` | 新增 `KnowledgeHubConfig` dataclass，加入 `ConnectorsConfig.knowledge_hub` |
| `hushclaw/connectors/manager.py` | `_build()` 中实例化 `KnowledgeConnector`；`start()`/`stop()` 纳入生命周期 |
| `hushclaw/gateway.py` | `_knowledge_hub: Any = None` 字段；`set_knowledge_hub(hub)` 方法；`_get_or_create_loop()` 注入 |
| `hushclaw/tools/runtime_context.py` | `knowledge_hub: Any = None` typed field；映射到 `_knowledge_hub` |
| `hushclaw/tools/builtins/memory_tools.py` | `recall` 工具增加 `include_shared: bool = False` 和 `_knowledge_hub` 参数 |
| `hushclaw/os_api.py` | `promote_to_hub(note_id, ...)` 异步方法 |
| `hushclaw/server/http_mixin.py` | `_background_startup()` 在 connectors 启动后调用 `gateway.set_knowledge_hub()` |

### 不修改（零污染保证）

- `hushclaw/memory/store.py` — schema 不变
- `hushclaw/memory/db.py` — 无新表
- `hushclaw/runtime/principal.py` — org_id 保持声明性字段，不做强制
- `hushclaw/distro/personal.py` — 完全不变
- `hushclaw/loop.py`, `hushclaw/agent.py` — 完全不变

---

## Configuration

```toml
# ~/.hushclaw.toml or hushclaw.toml
[connectors.knowledge_hub]
enabled = true
url = "https://hub.example.com"      # Hub base URL
token = "sk-team-xxx"                # Bearer token
team_scope = "team:backend"          # Default scope for read_shared
cache_ttl_seconds = 60               # Local cache TTL for hub searches
auto_include = false                 # If true, inject hub results into recall automatically
```

---

## Future Work (P1/P2)

- **TeamDistro**: `hushclaw serve --distro team` 作为 Hub 部署，`on_startup()` 注册 `/knowledge/*` 路由
- **MCP 兼容层**: Hub 暴露 MCP 工具端点（`search_knowledge`, `share_knowledge`），使任意 MCP 客户端可接入
- **只读下放 scope**: 管理员 broadcast 的内容写入本地 `note_type="policy"` scope，工具 recall 可见但用户无法删改
- **Postgres Hub**: TeamDistro 声明 `storage_profile="postgres"`，Hub 的 `memory.db` 换成 Postgres

---

## Consequences

**正面：**
- 个人版 schema/查询/代码完全不变，升级路径零成本
- 隐私边界由物理部署边界保证，无需依赖代码正确性
- Hub 可以是任意服务（甚至不是 HushClaw），协议解耦
- 故障降级自然：Hub 断线，个人 recall 正常工作

**权衡：**
- 跨人全文检索需要经过 Hub 的 HTTP 往返（~50-100ms 额外延迟）
- Hub 是单独的运维单元，需要独立部署和 token 管理
- 知识共享是用户主动操作，不会自动发生（这也是设计意图）
