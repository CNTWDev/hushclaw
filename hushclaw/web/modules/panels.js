/**
 * panels.js — Barrel re-export.
 * All functionality lives in panels/ subdirectory modules.
 * This file exists so existing consumers (websocket.js, events.js)
 * continue to import from "./panels.js" without modification.
 */

// agents panel + tab switching
export {
  switchTab,
  populateAgents, renderAgentsPanel, handleAgentDetail, handleAgentRuntimeStatus, handleAgentTestResult,
} from "./panels/agents.js";

// sessions sidebar + workspace selector + memories panel
export {
  loadSession, renderSessions, renderSessionSearchResults,
  refreshSessionsView, runSessionSearch, scheduleSessionSearch, clearSessionSearch,
  toggleSessionsSidebar, initSessionsSidebarState,
  updateSessionRunIndicator,
  onSessionDeleted, onSessionRenamed, handleSessionWorkspaceMoved,
  renderWorkspaceSelector,
  renderMemories, renderBeliefModels, renderBeliefModelsError, handleBeliefModelDetail,
  renderOpinionThreads, renderOpinionThreadsError, handleOpinionThreadDetail,
  renderProfileFacts, renderProfileFactsError,
  renderMemoryOverview, renderReflections,
  onMemoryDeleted, onProfileFactDeleted, selectedMemoryKinds,
} from "./panels/sessions.js";

// files sidebar
export {
  initFilesSidebar, renderFiles, refreshFilesList, toggleFilesSidebar,
  handleFileIngested, handleFileDeleted,
} from "./panels/files.js";

// skills panel
export {
  handleSkillsList, handleSkillRepos,
  handleSkillInstallProgress, handleSkillInstallResult, handleSkillSaved, handleSkillDeleted,
  handleSkillExportReady, handleSkillImportResult,
  handleSkillDetail, handleSkillsHealth, handleSkillEnabled,
  handleLearningState, installSkillRepo, renderSkillsPanel, refreshSkillsList,
} from "./panels/skills.js";

// app connectors panel
export {
  renderAppConnectorsPanel,
  handleTestAppConnectorResult,
  handleAppInboxEvents,
  handleAppInboxEventUpdated,
  handleAppConnectorDraftPublishProgress,
  handleAppConnectorDraftPublished,
  refreshAppInbox,
} from "./panels/app_connectors.js";

// logs panel
export {
  initLogsPanel, refreshLogs, renderLogs,
} from "./panels/logs.js";
