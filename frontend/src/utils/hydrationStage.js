// Issue #2035: shared stage→label mapping for session data-hydration visibility,
// used identically by InputArea.vue's placeholder and MessageList.vue's empty state.

export const HYDRATION_STAGE_LABELS = {
  loading_history: 'Loading conversation history...',
  loading_resources: 'Loading resources...',
  loading_extras: 'Almost ready...',
  connecting_poll: 'Connecting...',
}

// Declared explicitly (not derived from HYDRATION_STAGE_LABELS's keys) so a future
// stage added to the label map for display purposes only doesn't silently start
// rendering a loading spinner too.
export const HYDRATION_LOADING_STAGES = [
  'loading_history',
  'loading_resources',
  'loading_extras',
  'connecting_poll',
]
