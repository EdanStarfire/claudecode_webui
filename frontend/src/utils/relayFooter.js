// Mirror of backend/relay/footer.py (DEFAULT_FOOTER_TEMPLATE, FOOTER_MAX_LENGTH); a backend test
// pins the template string so the two cannot drift.
export const BUILT_IN_FOOTER_TEMPLATE = 'Sent by Claude agent {session_name} ({session_handle}) via Claude WebUI'
export const FOOTER_MAX_LENGTH = 500

/** Renders known variables for a preview; unknown context leaves the variable untouched. */
export function renderFooterPreview(template, { name, handle } = {}) {
  return String(template ?? '')
    .replaceAll('{session_name}', name || '{session_name}')
    .replaceAll('{session_handle}', handle || '{session_handle}')
    .replaceAll('{session_link}', '')
}
