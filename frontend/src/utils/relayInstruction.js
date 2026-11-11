// Parse the text the session receives for a Slack self-DM instruction (issue #2103, inbound 3d-ii).
// Mirrors backend `relay/inbound/dispatch.py::format_instruction`; `utils/__tests__/relayInstruction.test.js`
// pins the format. Strict on purpose: the FIRST opener, a closer carrying the SAME id (a nonce the backend
// guarantees is absent from the body; `[OWNER` / `[END OWNER` inside the body are neutralised there), and the
// fixed header / thread line around it. Anything else returns null and the caller renders the message as before.
// Display only: the message content is never changed.

const HEADER = /^📥 Slack instruction from the owner[^\n]*\n\n/u
const OPENER = /^\[OWNER INSTRUCTION id=([A-Za-z0-9]{8,64}) [^\]\n]*\]\n/
const THREAD_TAIL = /^\n\n---\nSlack thread: channel=(\S+) thread_ts=(\S+?)\.(?: [^\n]*)?$/

/** `{text, thread: {channel, thread_ts}, preface}` or null when `content` is not exactly that shape. */
export function parseInstruction(content) {
  if (typeof content !== 'string') return null
  const header = HEADER.exec(content)
  if (!header) return null
  let rest = content.slice(header[0].length)
  const opener = OPENER.exec(rest)
  if (!opener) return null
  const nonce = opener[1]
  rest = rest.slice(opener[0].length)
  const closer = `\n[END OWNER INSTRUCTION id=${nonce}]`
  const end = rest.indexOf(closer)
  if (end < 0) return null
  const text = rest.slice(0, end)
  const tail = THREAD_TAIL.exec(rest.slice(end + closer.length))
  if (!tail) return null
  return {
    text,
    thread: { channel: tail[1], thread_ts: tail[2] },
    preface: header[0].trim(),
  }
}
