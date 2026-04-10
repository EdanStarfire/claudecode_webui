import { computed } from 'vue'

/**
 * Shared color map for session display states.
 * Exported for components that need the raw map (e.g., MinionTreeNode CSS class mapping).
 */
export const STATE_COLOR_MAP = {
  'created': '#d3d3d3',
  'starting': '#90ee90',
  'active': '#90ee90',
  'running': '#90ee90',
  'processing': '#dda0dd',
  'paused': '#d3d3d3',
  'pending-prompt': '#ffc107',
  'terminated': '#d3d3d3',
  'error': '#ffb3b3',
  'failed': '#ffb3b3',
  'unknown': '#d3d3d3'
}

/**
 * Get display state for a session object (non-reactive).
 * Processing state overrides session state.
 * Special case: paused + processing = 'pending-prompt' (waiting for permission).
 *
 * @param {Object|null} session
 * @returns {string}
 */
export function getDisplayState(session) {
  if (!session) return 'unknown'
  if (session.state === 'paused' && session.is_processing) {
    return 'pending-prompt'
  }
  return session.is_processing ? 'processing' : (session.state || 'unknown')
}

/**
 * Get status bar color for a session object (non-reactive).
 *
 * @param {Object|null} session
 * @returns {string} CSS color string
 */
export function getStatusColor(session) {
  const state = getDisplayState(session)
  return STATE_COLOR_MAP[state] || '#d3d3d3'
}

/**
 * Whether the status indicator should be animated for a session (non-reactive).
 * Animated states: starting, processing, pending-prompt.
 *
 * @param {Object|null} session
 * @returns {boolean}
 */
export function isAnimatedState(session) {
  const state = getDisplayState(session)
  return state === 'starting' || state === 'processing' || state === 'pending-prompt'
}

/**
 * Reactive composable for session state predicates and display helpers.
 * Follows the pattern of useToolStatus.js: reactive composable + non-reactive helpers.
 *
 * @param {import('vue').Ref<Object>} sessionRef - reactive ref or computed to a session object
 * @returns {{ isPaused, isStarting, isActive, isCreated, isTerminated, isError, isRunning, isPendingPrompt, displayState, statusColor, isAnimated }}
 */
export function useSessionState(sessionRef) {
  const isPaused = computed(() => sessionRef.value?.state === 'paused')
  const isStarting = computed(() => sessionRef.value?.state === 'starting')
  const isActive = computed(() => sessionRef.value?.state === 'active')
  const isCreated = computed(() => sessionRef.value?.state === 'created')
  const isTerminated = computed(() => sessionRef.value?.state === 'terminated')
  const isError = computed(() => sessionRef.value?.state === 'error')
  const isRunning = computed(() => {
    const state = sessionRef.value?.state
    return state === 'active' || state === 'starting'
  })
  const isPendingPrompt = computed(() => {
    const s = sessionRef.value
    return s?.state === 'paused' && s?.is_processing
  })
  const displayState = computed(() => getDisplayState(sessionRef.value))
  const statusColor = computed(() => getStatusColor(sessionRef.value))
  const isAnimated = computed(() => isAnimatedState(sessionRef.value))

  return {
    isPaused,
    isStarting,
    isActive,
    isCreated,
    isTerminated,
    isError,
    isRunning,
    isPendingPrompt,
    displayState,
    statusColor,
    isAnimated,
  }
}
