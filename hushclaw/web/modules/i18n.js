/**
 * i18n.js — Chinese/English language switching.
 * Mirrors the theme.js pattern: localStorage persistence, auto-detect from navigator.language.
 * Default: follows system locale (zh* → Chinese, everything else → English).
 */

export const LOCALES = ["en", "zh"];
export const LOCALE_STORAGE_KEY = "hushclaw.ui.locale";

const LANGS = {
  en: {
    // Navigation tabs
    tab_chat:      "Chat",
    tab_agents:    "Agents",
    tab_skills:    "Skills",
    tab_memories:  "Memories",
    tab_tasks:     "Tasks",
    tab_calendar:  "Calendar",
    tab_settings:  "Settings",
    // Tab descriptions
    desc_chat:     "Chat with AI agents · manage conversation history",
    desc_agents:   "Create and configure multi-agent teams",
    desc_skills:   "Install skill packs to extend AI capabilities",
    desc_memories: "Browse and manage the AI's persistent memory",
    desc_tasks:    "Todos and scheduled recurring tasks",
    desc_calendar: "Calendar — create, view and manage events",
    desc_settings: "Configure AI provider, model, and system settings",
    // Sidebar
    sessions:        "Sessions",
    files:           "Files",
    search_sessions: "Search sessions…",
    // Memories panel
    mem_kb:          "Knowledge Base",
    mem_profile:     "Profile",
    mem_beliefs:     "Beliefs",
    mem_reflections: "Reflections",
    mem_search:      "Search memories…",
    mem_clean:       "Clean+Compact",
    // Chat input area
    input_placeholder: "Message… Use @agent to switch agents; /skills to list skills, /<skill> to run one; supports attachments and image paste.",
    new_topic: "New Topic",
    export:    "Export",
    // Agents panel
    refresh:       "Refresh",
    new_agent:     "+ New Agent",
    run_hierarchy: "Run Hierarchy",
    advanced:      "Advanced",
    // Tasks panel
    todos:          "Todos",
    sched_tasks:    "Scheduled Tasks",
    add_todo:       "+ Add",
    add_sched:      "+ Schedule",
    run_once:       "Run once",
    todo_title_ph:  "Todo title…",
    sched_task_ph:  "Task name…",
    sched_prompt_ph:"Prompt / task description…",
    cron_ph:        "cron expr: 0 9 * * *",
    // Calendar toolbar
    new_event_btn: "+ New Event",
    cal_today:     "Today",
    cal_month:     "Month",
    cal_agenda:    "Agenda",
    cal_sync:      "↻ Sync",
    cal_resync:    "⟳ Re-sync",
    cal_prev:      "‹",
    cal_next:      "›",
    cal_weekdays:  ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"],
    // Calendar modal
    new_event:      "New Event",
    edit_event:     "Edit Event",
    all_day:        "All day",
    ev_start:       "Start",
    ev_end:         "End",
    ev_color:       "Color:",
    save:           "Save",
    cancel:         "Cancel",
    delete:         "Delete",
    ev_title_ph:    "Event title *",
    ev_location_ph: "Location",
    ev_desc_ph:     "Description",
    // Settings tabs
    stab_model:        "Model",
    stab_system:       "System",
    stab_memory:       "Memory",
    stab_channels:     "Channels",
    stab_integrations: "Integrations",
    // Sessions
    no_sessions: "No sessions",
    turns:       "turns",
    // Skills
    skills_refresh: "Refresh",
  },

  zh: {
    tab_chat:      "聊天",
    tab_agents:    "智能体",
    tab_skills:    "技能",
    tab_memories:  "记忆",
    tab_tasks:     "任务",
    tab_calendar:  "日历",
    tab_settings:  "设置",
    desc_chat:     "与 AI 智能体对话 · 管理对话历史",
    desc_agents:   "创建和配置多智能体团队",
    desc_skills:   "安装技能包扩展 AI 能力",
    desc_memories: "浏览和管理 AI 的持久记忆",
    desc_tasks:    "待办事项与定时任务",
    desc_calendar: "日历 — 创建、查看和管理事件",
    desc_settings: "配置 AI 提供商、模型和系统设置",
    sessions:        "会话",
    files:           "文件",
    search_sessions: "搜索会话…",
    mem_kb:          "知识库",
    mem_profile:     "用户画像",
    mem_beliefs:     "信念",
    mem_reflections: "复盘",
    mem_search:      "搜索记忆…",
    mem_clean:       "压缩记忆",
    input_placeholder: "输入消息… 使用 @agent 切换智能体；/skills 列出技能，/<技能> 执行；支持附件和图片粘贴。",
    new_topic: "新话题",
    export:    "导出",
    refresh:       "刷新",
    new_agent:     "+ 新建智能体",
    run_hierarchy: "运行层级",
    advanced:      "高级",
    todos:          "待办",
    sched_tasks:    "定时任务",
    add_todo:       "+ 添加",
    add_sched:      "+ 创建计划",
    run_once:       "执行一次",
    todo_title_ph:  "待办标题…",
    sched_task_ph:  "任务名称…",
    sched_prompt_ph:"提示词 / 任务描述…",
    cron_ph:        "cron 表达式：0 9 * * *",
    new_event_btn: "+ 新建事件",
    cal_today:     "今天",
    cal_month:     "月视图",
    cal_agenda:    "日程",
    cal_sync:      "↻ 同步",
    cal_resync:    "⟳ 重新同步",
    cal_prev:      "‹",
    cal_next:      "›",
    cal_weekdays:  ["日", "一", "二", "三", "四", "五", "六"],
    new_event:      "新建事件",
    edit_event:     "编辑事件",
    all_day:        "全天",
    ev_start:       "开始",
    ev_end:         "结束",
    ev_color:       "颜色：",
    save:           "保存",
    cancel:         "取消",
    delete:         "删除",
    ev_title_ph:    "事件标题 *",
    ev_location_ph: "地点",
    ev_desc_ph:     "描述",
    stab_model:        "模型",
    stab_system:       "系统",
    stab_memory:       "记忆",
    stab_channels:     "频道",
    stab_integrations: "集成",
    no_sessions: "暂无会话",
    turns:       "轮",
    skills_refresh: "刷新",
  },
};

function _detect() {
  try {
    const saved = localStorage.getItem(LOCALE_STORAGE_KEY);
    if (LOCALES.includes(saved)) return saved;
  } catch { /* ignore */ }
  return (navigator.language || "en").toLowerCase().startsWith("zh") ? "zh" : "en";
}

export let currentLocale = _detect();

/** Translate a key for the current locale, falling back to English then the key itself. */
export function t(key) {
  return (LANGS[currentLocale]?.[key] ?? LANGS.en[key]) ?? key;
}

/** Switch locale, persist to localStorage, apply to DOM, dispatch "locale-changed". */
export function setLocale(lang) {
  if (!LOCALES.includes(lang)) lang = "en";
  currentLocale = lang;
  try { localStorage.setItem(LOCALE_STORAGE_KEY, lang); } catch { /* ignore */ }
  document.documentElement.lang = lang === "zh" ? "zh-CN" : "en";
  applyLocale();
  document.dispatchEvent(new CustomEvent("locale-changed", { detail: { locale: lang } }));
}

/** Apply current locale to all annotated DOM elements (idempotent). */
export function applyLocale() {
  // Static text nodes
  document.querySelectorAll("[data-i18n]").forEach(el => {
    const v = t(el.dataset.i18n);
    if (v !== undefined) el.textContent = v;
  });
  // Placeholder attributes
  document.querySelectorAll("[data-i18n-ph]").forEach(el => {
    const v = t(el.dataset.i18nPh);
    if (v !== undefined) el.placeholder = v;
  });
  // data-desc + title attributes on tab buttons
  document.querySelectorAll("[data-i18n-desc]").forEach(el => {
    const v = t(el.dataset.i18nDesc);
    if (v !== undefined) { el.dataset.desc = v; el.title = v; }
  });
  // Calendar weekday row
  const wkdays = document.querySelectorAll(".cal-wkday-label");
  const days = t("cal_weekdays");
  if (Array.isArray(days)) {
    wkdays.forEach((el, i) => { if (days[i] !== undefined) el.textContent = days[i]; });
  }
  // Toggle button label: show the OTHER language (the one you'd switch to)
  const btn = document.getElementById("lang-toggle");
  if (btn) btn.textContent = currentLocale === "zh" ? "EN" : "中";
}

/** Detect locale and apply on page load. */
export function initLocale() {
  document.documentElement.lang = currentLocale === "zh" ? "zh-CN" : "en";
  applyLocale();
}
