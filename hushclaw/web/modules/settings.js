/**
 * settings.js — Barrel re-export.
 * All functionality lives in settings/ subdirectory modules.
 * This file exists so existing consumers (websocket.js, events.js)
 * continue to import from "./settings.js" without modification.
 */

// providers
export { PROVIDERS, providerById, CHANNELS, _isConfigured } from "./settings/providers.js";

// transsion / model tab
export {
  setTxFromConfig, getTxForSave, clearTestTimer,
  handleTestProviderStep, handleTestProviderResult,
  resetTranssionPendingUi,
  handleTransssionCodeSent, handleTransssionAuthed, handleTransssionQuotaResult,
  renderModelTab, handleModelsResponse,
} from "./settings/transsion.js";

// save
export { clearWizardSaveTimer, syncFormToState, validateSettings, saveSettings } from "./settings/save.js";

// system tab
export { renderSystemTab } from "./settings/tab-system.js";

// misc tabs + wizard open/close
export {
  registerSettingsWidget,
  openWizard, closeWizard,
  renderSettingsTabs, renderSettingsModal,
  renderChannelsTab, updateChannelStatusDots,
  renderMemoryTab, renderAppConnectorsTab, renderIntegrationsTab,
  handleTestIntegrationStep, handleTestIntegrationResult, handleTestAppConnectorResult,
} from "./settings/tab-misc.js";

// config handlers + timer reset
export { handleConfigStatus, handleConfigSaved, resetWizardTimers } from "./settings/handlers.js";
