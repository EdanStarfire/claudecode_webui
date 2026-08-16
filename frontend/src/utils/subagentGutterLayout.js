/**
 * Issue #1746 (stage: subagents) follow-up: assigns a stack slot to every subagent gutter lane
 * — including RETIRED (completed/failed/stopped) legs, not just currently-running ones. A
 * completed leg's chip must remain scrollable-into-view for as long as the user is scrolled
 * anywhere within its own [launch, terminal] span, forever (historical record) — so slot
 * assignment can no longer be a live "claim while running, release on complete" registry (the
 * stage's earlier design); it has to be a pure function of every lane's actual pixel span,
 * recomputed fresh whenever those spans change.
 *
 * This is the classic interval-partitioning / "minimum rooms" greedy algorithm: sort lanes by
 * their own start (top), and assign each the lowest-numbered slot whose previous occupant's
 * span has already ended (bottom <= this lane's top). Two lanes whose spans don't overlap can
 * safely share a slot number, since they're never on-screen (or sticky-eligible) at the same
 * scroll position anyway — this is what keeps the slot count bounded even across a long session
 * with many sequential (non-overlapping) subagent launches.
 *
 * @param {Array<{id: string, top: number, bottom: number}>} lanes
 * @returns {Map<string, number>} lane id -> slot index
 */
export function assignGutterSlots(lanes) {
  const sorted = [...lanes].sort((a, b) => a.top - b.top)
  const slotBottoms = [] // slotBottoms[i] = the bottom (px) below which slot i is free again
  const assignment = new Map()

  for (const lane of sorted) {
    let slot = slotBottoms.findIndex(bottom => bottom <= lane.top)
    if (slot === -1) {
      slot = slotBottoms.length
      slotBottoms.push(-Infinity)
    }
    slotBottoms[slot] = lane.bottom
    assignment.set(lane.id, slot)
  }

  return assignment
}
