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
  renderReflections,
  onMemoryDeleted, selectedMemoryKinds,
} from "./panels/sessions.js";

// files sidebar
export {
  initFilesSidebar, renderFiles, refreshFilesList, toggleFilesSidebar,
} from "./panels/files.js";

// skills panel
export {
  handleSkillsList, handleSkillRepos,
  handleSkillInstallResult, handleSkillSaved, handleSkillDeleted,
  handleSkillExportReady, handleSkillImportResult,
  handleLearningState, installSkillRepo, renderSkillsPanel,
} from "./panels/skills.js";
