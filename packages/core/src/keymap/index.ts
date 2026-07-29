/**
 * The keyboard authority: key specs, the context-key vocabulary, the resolver,
 * host reservations, the capture protocol, and the shell's dispatcher.
 * See docs/architecture/keybindings.mdx.
 */
export {
  formatSpec,
  isModifierEvent,
  KeySpecError,
  labelSpec,
  matchesKeySpec,
  matchesSpec,
  parseSpec,
  specsFromEvent,
  tryParseSpec,
  type Chord,
  type KeyPlatform,
  type KeySpec,
  type LabelOptions,
} from './spec';
export {
  CONTEXT_KEYS,
  evaluateWhen,
  keysUsed,
  testWhen,
  validateWhen,
  WhenError,
  type CaptureMode,
  type ContextKeyDoc,
  type KeyContext,
} from './context';
export {
  bindingsFor,
  explainBinding,
  isSuppressedByCapture,
  pickBinding,
  resolveKey,
  type KeyResolution,
  type LossReason,
  type ResolvedBinding,
} from './resolve';
export {
  checkReserved,
  checkReservedSpec,
  RESERVED,
  type KeyHost,
  type ReservedEntry,
  type ReservedHit,
} from './reserved';
export {
  captureStore,
  getCapture,
  releaseCapture,
  requestCapture,
  useCapture,
  useCaptureState,
  type CaptureHandle,
  type CaptureRequest,
  type CaptureState,
  type EscapePolicy,
} from './capture';
export {
  auditKeymap,
  detectPlatform,
  getKeymap,
  getKeymapOverrides,
  initKeymapHost,
  invalidateBindings,
  isEditableTarget,
  keyContextStore,
  keyHost,
  keymapStore,
  keyPlatform,
  readKeyContext,
  setKeymapOverrides,
  setShellView,
  unreachableDefaults,
  useKeyContext,
  useKeymap,
  type KeymapOverride,
} from './state';
export {
  canHoldEscape,
  DEFAULT_ESCAPE_HOLD_MS,
  installKeymap,
  lockEscape,
  pendingChord,
  unlockEscape,
  type KeymapHooks,
} from './dispatch';
export {
  globalShortcuts,
  installGlobalShortcuts,
  setGlobalShortcuts,
  type GlobalShortcuts,
} from './global';
export {
  disableKeybinding,
  isCommandCustomized,
  loadKeymapOverrides,
  resetAllKeybindings,
  resetKeybindings,
  setKeybinding,
} from './overrides';
