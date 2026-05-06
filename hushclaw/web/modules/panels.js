/**
 * panels.js — Barrel re-export.
 * All functionality lives in panels/ subdirectory modules.
 * This file exists so existing consumers (websocket.js, events.js)
 * continue to import from "./panels.js" without modification.
 */

// agents panel + tab switching
export {
  switchTab,
  populateAgents, renderAgentsPanel, handleAgentDetail,
} from "./panels/agents.js";

// sessions sidebar + workspace selector + memories panel
export {
  loadSession, renderSessions, renderSessionSearchResults,
  refreshSessionsView, runSessionSearch, clearSessionSearch,
  toggleSessionsSidebar, initSessionsSidebarState,
  onSessionDeleted, handleSessionWorkspaceMoved,
  renderWorkspaceSelector,
  renderMemories, renderProfileSnapshot, renderBeliefModels, renderProfileFacts,
  renderMemoryOverview, renderReflections,
  onMemoryDeleted, selectedMemoryKinds,
} from "./panels/sessions.js";

// files sidebar
export {
  initFilesSidebar, renderFiles, refreshFilesList, toggleFilesSidebar,
  handleFileIngested, handleFileDeleted,
} from "./panels/files.js";

// skills panel
export {
  handleSkillsList, handleSkillRepos,
  handleSkillInstallResult, handleSkillSaved, handleSkillDeleted,
  handleSkillExportReady, handleSkillImportResult,
  handleLearningState, installSkillRepo, renderSkillsPanel,
} from "./panels/skills.js";

// html preview panel
export {
  initHtmlPreview, updateHtmlPreview, finalizeHtmlPreview, hideHtmlPreview,
} from "./panels/html_preview.js";
