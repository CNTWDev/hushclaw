# Memory & Evolution System Architecture

```mermaid
flowchart TD
    %% ─────────────────────────────────────────────
    %% USER TURN ENTRY
    %% ─────────────────────────────────────────────
    U([User Input]) --> HOOK_INIT

    subgraph LOOP["AgentLoop  (loop.py)"]
        HOOK_INIT["🪝 pre_session_init\n→ LearningController resets _pending"]
        BUILD["_build_context()"]
        HOOK_LLM_PRE["🪝 pre_llm_call"]
        LLM["provider.complete()"]
        TOOLS["Tool Execution\n(parallel-safe ‖ serial)"]
        HOOK_TOOL_PRE["🪝 pre_tool_call"]
        HOOK_TOOL_POST["🪝 post_tool_call\n→ append to _pending[tool_trace]"]
        PERSIST["Save turns → SQLite + .jsonl"]
        HOOK_TURN["🪝 post_turn_persist\n(sync, before done event)"]
        BG["asyncio.create_task\n_background_finalize()"]
        DONE([Done event → User])
    end

    HOOK_INIT --> BUILD
    BUILD --> HOOK_LLM_PRE --> LLM
    LLM -->|tool_use| HOOK_TOOL_PRE --> TOOLS --> HOOK_TOOL_POST --> LLM
    LLM -->|end_turn| PERSIST --> HOOK_TURN --> DONE
    HOOK_TURN --> BG

    %% ─────────────────────────────────────────────
    %% CONTEXT ENGINE
    %% ─────────────────────────────────────────────
    subgraph CTX["DefaultContextEngine  (context/engine.py)"]
        direction TB
        STABLE["Stable Prefix\n• system_prompt\n• AGENTS.md / instructions\n• SOUL.md (workspace identity)"]
        DYNAMIC["Dynamic Suffix\n• today's date\n• USER.md\n• user profile snapshot (TTL 30s)\n• belief models (query-routed)\n• working_state.md (mtime-gated)\n• recalled memories\n• serendipity sample"]
        AFTER["after_turn()\nRegex auto-extract (0 LLM calls)\n• interest / preference / belief / decision\n• correction signal detection\n→ SQLite-only notes (_auto_extract)"]
        COMPACT["compact()\n• lossless: archive old turns → memory\n• prune_tool_results: zero out old tool msgs"]
    end

    BUILD --> STABLE
    BUILD --> DYNAMIC
    BG --> AFTER

    %% ─────────────────────────────────────────────
    %% MEMORY STORE
    %% ─────────────────────────────────────────────
    subgraph MEM["MemoryStore  (memory/store.py)"]
        direction TB
        RECALL["recall_with_budget()\nFTS-first shortcut (≥0.8) → vector fallback\nScore gate + time-decay + softmax sample\nSession cache TTL 30s"]
        BELIEF_RENDER["render_belief_models()\nQuery-aware domain routing\n(domain parts → signals → summary scoring)"]
        PROFILE_RENDER["user_profile.render_profile_context()"]
        WS_LOAD["load_session_working_state()"]
    end

    DYNAMIC --> RECALL
    DYNAMIC --> BELIEF_RENDER
    DYNAMIC --> PROFILE_RENDER
    DYNAMIC --> WS_LOAD

    %% ─────────────────────────────────────────────
    %% STORAGE LAYER
    %% ─────────────────────────────────────────────
    subgraph DB["SQLite  (memory/db.py)"]
        direction LR
        T_NOTES["notes\n(note_id, scope, note_type,\nmemory_kind, tags)"]
        T_BODIES["note_bodies\n(body text)"]
        T_FTS["notes_fts\n(FTS5 BM25)"]
        T_VEC["embeddings\n(local TF-IDF / Ollama / OpenAI)"]
        T_TURNS["turns\n(session, role, content,\ntokens, workspace)"]
        T_SESSIONS["sessions\n(metadata, compaction_count,\nparent_session_id)"]
        T_LINEAGE["session_lineage\n(compaction events)"]
        T_TURNS_FTS["turns_fts\n(cross-session search)"]
        T_PROFILE["user_profile_facts\n(category, key, value_json,\nconfidence)"]
        T_BELIEF["belief_models\n(domain, scope, entries,\nsummary, trajectory, signals, dirty)"]
        T_REFLECT["reflections\n(task_fingerprint, success,\nlesson, strategy_hint)"]
        T_SKILL["skill_outcomes\n(skill_name, quality_score)"]
    end

    RECALL --> T_FTS
    RECALL --> T_VEC
    RECALL --> T_NOTES
    BELIEF_RENDER --> T_BELIEF
    PROFILE_RENDER --> T_PROFILE
    WS_LOAD -->|filesystem| FS_WS["sessions/{id}/working_state.md"]
    PERSIST --> T_TURNS
    PERSIST --> T_SESSIONS

    %% ─────────────────────────────────────────────
    %% LEARNING / EVOLUTION PIPELINE
    %% ─────────────────────────────────────────────
    subgraph LEARN["LearningController  (learning/controller.py)"]
        direction TB
        LC_TRACE["Collect _pending per session\n(tool_trace, errors, used_skills, corrections)"]
        LC_FP["fingerprint_task()\nweb_research / code_fix / skill_workflow\ndeliverable / memory_management / general"]
        LC_REFLECT["reflect_trace()  ← 0 LLM calls\n• success / failure_mode / lesson\n• strategy_hint (tool flow)\n• profile_updates (regex)"]
        LC_PERSIST["_persist_reflection()\n• record_reflection()\n• user_profile.upsert_fact()\n• record_skill_outcome()\n• _maybe_auto_patch_skill()"]
        LC_BELIEF["_maybe_consolidate_belief_models()\nRate-limited (45s), in-flight dedup\nBatch 3 dirty models → LLM call\n→ save_belief_model_consolidation()\n  (summary, trajectory, signals, dirty=0)"]
    end

    HOOK_TURN --> LC_TRACE
    LC_TRACE --> LC_FP --> LC_REFLECT --> LC_PERSIST
    HOOK_TURN --> LC_BELIEF

    LC_PERSIST --> T_REFLECT
    LC_PERSIST --> T_PROFILE
    LC_PERSIST --> T_SKILL
    LC_BELIEF --> T_BELIEF

    AFTER --> T_NOTES
    AFTER --> T_FTS
    AFTER --> T_VEC

    %% ─────────────────────────────────────────────
    %% BELIEF MODEL CONSOLIDATION (LLM path)
    %% ─────────────────────────────────────────────
    subgraph BCONS["Belief Consolidation  (async, background)"]
        BC_DIRTY["list_dirty_belief_models(scopes)"]
        BC_LLM["cheap_model.complete()\nBELIEF_MODEL_CONSOLIDATION prompts\n→ JSON: domain, summary, trajectory, signals"]
        BC_SAVE["save_belief_model_consolidation()\ndirty=0"]
    end

    LC_BELIEF --> BC_DIRTY --> BC_LLM --> BC_SAVE --> T_BELIEF

    %% ─────────────────────────────────────────────
    %% MEMORY KINDS
    %% ─────────────────────────────────────────────
    subgraph KINDS["Memory Kinds  (memory/kinds.py)"]
        direction LR
        K1["user_model\n(profile, preferences)"]
        K2["project_knowledge\n(facts, conventions)"]
        K3["decision\n(choices, conclusions)"]
        K4["session_memory\n(internal, not recalled)"]
        K5["telemetry\n(corrections, signals)"]
    end

    T_NOTES -.->|memory_kind| KINDS
    RECALL -.->|only user_model\nproject_knowledge\ndecision| K1
    RECALL -.-> K2
    RECALL -.-> K3

    %% ─────────────────────────────────────────────
    %% COMPACTION
    %% ─────────────────────────────────────────────
    subgraph COMP["Compaction  (context/engine.py)"]
        CP1["Flush working_state.md\n(Goal / Progress / Open Loops\n/ Recent Tool Outputs)"]
        CP2["Archive old turns → memory\n(lossless strategy)"]
        CP3["Reinject working_state.md\nafter compaction"]
        CP4["Record session_lineage"]
    end

    COMPACT --> CP1 --> CP2 --> CP3
    CP2 --> T_NOTES
    CP3 --> FS_WS
    COMPACT --> CP4 --> T_LINEAGE

    %% ─────────────────────────────────────────────
    %% HOOK BUS
    %% ─────────────────────────────────────────────
    subgraph HOOKS["HookBus  (runtime/hooks.py)"]
        HB["Best-effort async dispatcher\nFailures isolated & logged\nEvents:\n• pre_session_init\n• pre/post_llm_call\n• pre/post_tool_call\n• pre/post_compact\n• post_turn_persist"]
    end

    LOOP -.->|emit| HOOKS
    HOOKS -.->|on()| LEARN

    %% ─────────────────────────────────────────────
    %% STYLES
    %% ─────────────────────────────────────────────
    classDef storage fill:#1e3a5f,stroke:#4a9eff,color:#e8f4ff
    classDef engine fill:#1a3a2a,stroke:#4aff8a,color:#e8fff0
    classDef learning fill:#3a1a2a,stroke:#ff4a8a,color:#ffe8f0
    classDef hook fill:#3a3a1a,stroke:#ffcc4a,color:#fffce8
    classDef io fill:#2a1a3a,stroke:#cc4aff,color:#f5e8ff

    class T_NOTES,T_BODIES,T_FTS,T_VEC,T_TURNS,T_SESSIONS,T_LINEAGE,T_TURNS_FTS,T_PROFILE,T_BELIEF,T_REFLECT,T_SKILL storage
    class STABLE,DYNAMIC,AFTER,COMPACT,RECALL,BELIEF_RENDER,PROFILE_RENDER,WS_LOAD engine
    class LC_TRACE,LC_FP,LC_REFLECT,LC_PERSIST,LC_BELIEF,BC_DIRTY,BC_LLM,BC_SAVE learning
    class HOOK_INIT,HOOK_LLM_PRE,HOOK_TOOL_PRE,HOOK_TOOL_POST,HOOK_TURN,HB hook
    class U,DONE io
```

---

## 核心数据流说明

### 写入路径（每轮对话）

```
用户输入
  → pre_session_init  → LearningController 重置 _pending
  → assemble()        → 组装 stable + dynamic 系统提示
  → provider.complete → ReAct 工具循环
  → post_tool_call    → _pending[tool_trace] 追加
  → 持久化 turns      → SQLite
  → post_turn_persist → LearningController 同步弹出 _pending
                        ├─ reflect_trace()       → reflections + profile + skill_outcomes
                        └─ consolidate_beliefs() → belief_models (async LLM)
  → after_turn()      → 正则自动提取 → notes (SQLite-only)
```

### 读取路径（每轮 assemble）

```
assemble()
  ├─ SOUL.md / AGENTS.md   (mtime 缓存)
  ├─ USER.md               (mtime 缓存)
  ├─ user_profile          (TTL 30s 缓存)
  ├─ belief_models         (query 路由评分: domain > signals > summary)
  ├─ working_state.md      (mtime-gated per session)
  └─ recall_with_budget()
       ├─ FTS5 BM25 (权重 0.6)
       ├─ 向量余弦相似度 (权重 0.4, FTS ≥ 0.8 时跳过)
       ├─ 时间衰减 + softmax 采样
       └─ serendipity 随机采样 (可配置预算)
```

### 信念模型进化路径

```
remember(note_type="belief"|"interest")
  → belief_models 表 upsert (dirty=1)
  → post_turn_persist 触发 _maybe_consolidate_belief_models()
      → list_dirty_belief_models(scopes, limit=3)
      → cheap_model.complete(BELIEF_MODEL_CONSOLIDATION prompts)
      → save_belief_model_consolidation() → dirty=0
  → assemble() 时 render_belief_models() 注入提示词
```

### 压缩（Compaction）路径

```
needs_compaction() → True
  → flush working_state.md (Goal / Progress / Open Loops / Recent Tool Outputs)
  → 归档旧 turns → notes (lossless) 或 prune tool results
  → 重新注入 working_state.md
  → 记录 session_lineage
```
