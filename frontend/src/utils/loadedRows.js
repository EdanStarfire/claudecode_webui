// The rows of a session's loaded history (issue #2030, stage 3 PR 3C-1): one interval (the live
// suffix) goes through the plain pipeline exactly as before; with a jumped-to island the pipeline
// runs per interval with a `gap` row between. Shared by MessageList.vue and the equivalence tests.
// Called inside the caller's computed, so every store read below stays reactive-tracked.
import { buildDisplayableItems, buildIntervalItems } from '@/utils/displayableItems'
import { gapBetween } from '@/utils/historyIntervals'

export function buildLoadedRows(store, sessionId) {
  const ctx = {
    sessionId,
    legEntries: store.allTaskLegEntriesForSession(sessionId),
    getTaskIdForLaunchToolUse: store.getTaskIdForLaunchToolUse,
    isHookMessageAttached: (messageId) => store.isHookMessageAttached(sessionId, messageId),
  }
  const island = store.getIsland(sessionId)
  if (!island) {
    return buildDisplayableItems({
      ...ctx,
      messages: store.loadedMessages(sessionId),
      toolCalls: store.loadedToolCalls(sessionId),
      openBefore: !store.isHistoryComplete(sessionId),
    })
  }
  const history = store.getHistoryState(sessionId)
  return buildIntervalItems({
    ...ctx,
    intervals: [
      { messages: island.messages, toolCalls: island.toolCalls, hasMoreBefore: island.hasMoreBefore, hasMoreAfter: island.hasMoreAfter },
      {
        messages: store.messagesBySession.get(sessionId) || [],
        toolCalls: store.toolCallsBySession.get(sessionId) || [],
        hasMoreBefore: !!history?.hasMoreBefore,
        hasMoreAfter: false,
      },
    ],
    gap: gapBetween(island, history),
  })
}
