// Deep links to a message (issue #2030 stage 3 PR 3F): `<origin>/<path>#<route>?msg=<message_id>`.
// The app uses hash history, so the query lives inside the hash and the same builder serves the live
// session, an archive and a deleted agent's archive (the route path is whatever is open now).

export const MESSAGE_LINK_PARAM = 'msg'

/** Link to `messageId` in the view that is open now. `loc` is injectable for tests. */
export function buildMessageLink(messageId, loc = window.location) {
  const route = (loc.hash || '#/').slice(1).split('?')[0] || '/'
  return `${loc.origin}${loc.pathname}#${route}?${MESSAGE_LINK_PARAM}=${encodeURIComponent(messageId)}`
}

/** Copies text; falls back to a hidden textarea where the async Clipboard API needs a secure context (plain http). */
export async function copyText(text, { nav = navigator, doc = document } = {}) {
  try {
    if (nav?.clipboard?.writeText) {
      await nav.clipboard.writeText(text)
      return true
    }
  } catch {
    // fall through to the legacy path
  }
  try {
    const area = doc.createElement('textarea')
    area.value = text
    area.setAttribute('readonly', '')
    area.style.position = 'fixed'
    area.style.opacity = '0'
    doc.body.appendChild(area)
    area.select()
    const ok = doc.execCommand('copy')
    doc.body.removeChild(area)
    return !!ok
  } catch {
    return false
  }
}
