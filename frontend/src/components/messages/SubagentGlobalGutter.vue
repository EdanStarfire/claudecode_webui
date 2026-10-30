<template>
  <div class="global-subagent-gutter" aria-hidden="true">
    <div
      v-for="lane in lanes"
      :key="lane.key"
      class="gutter-lane"
      :class="{ 'lane-line': lane.launchedEarlier || lane.crossesGap }"
      :style="{ top: `${lane.top}px`, height: `${lane.height}px`, '--lane-color': lane.chipStyle.color }"
    >
      <button
        type="button"
        class="lane-chip"
        :class="{ 'needs-attention': lane.needsAttention, 'launched-earlier': lane.launchedEarlier }"
        :style="lane.chipStyle"
        :title="lane.launchedEarlier ? `${lane.tooltipLabel} — launched earlier, jump to launch` : lane.tooltipLabel"
        :aria-label="lane.launchedEarlier ? `${lane.tooltipLabel}: launched earlier — jump` : lane.tooltipLabel"
        :data-testid="lane.launchedEarlier ? 'lane-launched-earlier' : undefined"
        @click="onChipClick(lane)"
      >
        <span class="chip-glyph">{{ '\u{1F916}' }}</span>
        <span v-if="lane.launchedEarlier" class="chip-earlier" aria-hidden="true">&uarr;</span>
        <span v-if="lane.moreCount" class="chip-more" :title="`${lane.moreCount} more agents launched earlier`">+{{ lane.moreCount }}</span>
      </button>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useMessageStore } from '@/stores/message'
import { getAgentColor, slugifyAgentName } from '@/composables/useAgentColor'
import { assignGutterSlots } from '@/utils/subagentGutterLayout'

// Issue #1746 (stage: subagents) follow-up: a persistent gutter OUTSIDE the message flow,
// mounted ONCE per session view (see MessageList.vue) — not nested inside each subagent's own
// card. Direct user clarification (after watching the mockup): a RETIRED (completed/failed/
// stopped) leg's chip must stay scrollable-into-view forever, not just while running — whenever
// the user is scrolled anywhere within [launch, terminal] for that specific leg, its chip
// should be visible, pinning to the viewport TOP while scrolling forward through time (until
// the terminal row clears the top) and to the viewport BOTTOM while scrolling backward (until
// the launch row clears the bottom) — both directions handled natively by CSS position:sticky
// with BOTH `top` and `bottom` set on the same element, no scroll-direction JS needed. Multiple
// overlapping lanes (concurrently active OR concurrently *visible in history*) stack via
// assignGutterSlots (utils/subagentGutterLayout.js) — a pure interval-partitioning pass over
// every lane's actual pixel span, not a live claim/release registry (a registry can't express
// "this pair of now-finished legs happened to overlap in time, so their chips must still stack
// distinctly whenever scrolled back to that region").
const props = defineProps({
  sessionId: {
    type: String,
    default: null
  },
  // Issue #1748 (stage: offset-model): Map<`${taskId}:${legIndex}`, {top, bottom, height}>,
  // computed by MessageList.vue from the virtualizer's own offset model — replaces the old
  // getBoundingClientRect()-based measure() below. See MessageList.vue's `laneOffsets` computed
  // for why the resolution logic lives there rather than here (it needs to read the virtualizer
  // instance from within a reactive scope that reliably re-triggers on its internal updates).
  laneOffsets: {
    type: Map,
    default: () => new Map()
  }
})

const emit = defineEmits(['chip-click', 'jump-launch'])

const messageStore = useMessageStore()

const CHIP_SIZE = 22
const CHIP_GAP = 6
const BASE_TOP = 8
// Issue #2030 (stage 3 PR 3C-2): "launched earlier" chips have a 44x44 touch target. Chips that
// stack on one spot (clamped legs share their interval's top) must not have hit areas that cover
// each other, so while two or more are shown the whole stack uses a 44 + 2 px pitch; at most
// MAX_EARLIER_CHIPS are drawn, the rest is counted in a "+k" label on the last one.
const EARLIER_PITCH = 46
const MAX_EARLIER_CHIPS = 5

// One entry per (task_id, legIndex) — EVERY known leg, running or retired.
const allLanes = computed(() => {
  if (!props.sessionId) return []
  const result = []
  for (const entry of messageStore.allTaskLegEntriesForSession(props.sessionId)) {
    entry.legs.forEach((leg, legIndex) => {
      result.push({ taskId: entry.task_id, legIndex, leg })
    })
  }
  return result
})

const lanes = computed(() => {
  const withBounds = allLanes.value
    .map(l => {
      const off = props.laneOffsets.get(`${l.taskId}:${l.legIndex}`)
      return off ? { ...l, ...off } : null
    })
    .filter(Boolean)

  const slotAssignment = assignGutterSlots(
    withBounds.map(l => ({ id: `${l.taskId}:${l.legIndex}`, top: l.top, bottom: l.bottom }))
  )

  // Which clamped lanes are drawn: at most MAX_EARLIER_CHIPS, those needing attention and running ones first.
  const earlier = withBounds.filter(l => l.launchedEarlier)
  const rank = (l) => (messageStore.hasOpenPermissionForTask(props.sessionId, l.taskId) ? 2 : 0) + (l.leg.status === 'running' ? 1 : 0)
  const shown = [...earlier]
    .sort((a, b) => rank(b) - rank(a) || (slotAssignment.get(`${a.taskId}:${a.legIndex}`) ?? 0) - (slotAssignment.get(`${b.taskId}:${b.legIndex}`) ?? 0))
    .slice(0, MAX_EARLIER_CHIPS)
  const hidden = new Set(earlier.filter(l => !shown.includes(l)).map(l => `${l.taskId}:${l.legIndex}`))
  const visible = withBounds.filter(l => !hidden.has(`${l.taskId}:${l.legIndex}`))
  const lastShown = [...shown].sort((a, b) => (slotAssignment.get(`${a.taskId}:${a.legIndex}`) ?? 0) - (slotAssignment.get(`${b.taskId}:${b.legIndex}`) ?? 0)).pop()

  // A slot is as tall as its tallest chip: clamped chips (44x44 hit area) need EARLIER_PITCH while two or
  // more are stacked, every other chip keeps the ordinary pitch, so lanes whose launch row is loaded are
  // untouched when no clamped lane sits above them.
  const slotPitch = []
  for (const l of visible) {
    const slot = slotAssignment.get(`${l.taskId}:${l.legIndex}`) ?? 0
    const height = l.launchedEarlier && shown.length >= 2 ? EARLIER_PITCH : CHIP_SIZE + CHIP_GAP
    slotPitch[slot] = Math.max(slotPitch[slot] || 0, height)
  }
  const slotOffset = []
  let running = BASE_TOP
  for (let slot = 0; slot < slotPitch.length; slot++) {
    slotOffset[slot] = running
    running += slotPitch[slot] || CHIP_SIZE + CHIP_GAP
  }

  return visible.map(l => {
    const key = `${l.taskId}:${l.legIndex}`
    const slotIndex = slotAssignment.get(key) ?? 0
    const agentColor = getAgentColor(slugifyAgentName(l.taskId))
    const offsetPx = slotOffset[slotIndex]
    return {
      moreCount: l === lastShown ? hidden.size : 0,
      key,
      taskId: l.taskId,
      legIndex: l.legIndex,
      top: l.top,
      height: l.height,
      launchedEarlier: !!l.launchedEarlier,
      crossesGap: !!l.crossesGap,
      needsAttention: messageStore.hasOpenPermissionForTask(props.sessionId, l.taskId),
      tooltipLabel: l.leg.description || 'Subagent',
      chipStyle: {
        // Same offset for both edges — see the component-level comment for why this alone
        // (native CSS, no scroll-direction detection) gives the bidirectional pin behavior.
        top: `${offsetPx}px`,
        bottom: `${offsetPx}px`,
        // A retired clamped lane's chip slides out under the running ones at the viewport edge, never over them.
        ...(l.launchedEarlier ? { zIndex: l.leg.status === 'running' ? 6 : 4 } : {}),
        background: agentColor.bg,
        borderColor: agentColor.border,
        color: agentColor.accent,
      },
    }
  })
})

// Issue #1748 (stage: offset-model): navigation itself is delegated to MessageList.vue (which
// owns the virtualizer and the shared useVirtualNavigation helper — see its onGutterChipClick)
// rather than done here via a direct document.getElementById/scrollIntoView call, per plan §5.5's
// "unify the three navigation sites" goal.
function onChipClick(lane) {
  // Issue #2030 (stage 3 PR 3C-2): a lane clamped to the top of a loaded interval has no launch row to
  // expand or scroll to; its chip jumps to the launching message instead.
  if (lane.launchedEarlier) {
    emit('jump-launch', lane)
    return
  }
  messageStore.toggleLegExpanded(lane.taskId, lane.legIndex)
  emit('chip-click', lane)
}
</script>

<style scoped>
/* Issue #1746 (stage: subagents) follow-up: absolutely positioned against .messages-content
   (a normal-flow-established box, so height:100% here correctly matches actual content height)
   — pointer-events disabled on wrapper layers so it never blocks clicking through to message
   content beneath it, re-enabled only on the chip button itself. */
.global-subagent-gutter {
  position: absolute;
  top: 0;
  left: 0;
  width: 26px;
  height: 100%;
  pointer-events: none;
}

.gutter-lane {
  position: absolute;
  left: 0;
  width: 26px;
  pointer-events: none;
}

/* Issue #2030 (stage 3 PR 3C-2): a lane that is clamped to the loaded history, or spans the gap row
   between an island and the live end, is drawn as a thin line so its continuity is visible. Ordinary
   lanes stay chip-only, as before. */
.gutter-lane.lane-line::before {
  content: '';
  position: absolute;
  top: 0;
  bottom: 0;
  left: 50%;
  width: 0;
  border-left: 2px dashed var(--lane-color);
  opacity: 0.55;
  transform: translateX(-50%);
}

.lane-chip {
  position: sticky;
  width: 22px;
  height: 22px;
  margin: 0 auto;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 50%;
  border: 2px solid;
  z-index: 5;
  cursor: pointer;
  pointer-events: auto;
  padding: 0;
  font-size: 11px;
  line-height: 1;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.15);
}

/* Launched before the loaded history: dashed border + a small up-arrow. The hit area grows to 44x44
   (a pseudo-element, so the 22 px glyph and the 26 px gutter are unchanged). */
.lane-chip.launched-earlier {
  border-style: dashed;
  overflow: visible;
}

.lane-chip.launched-earlier::after {
  content: '';
  position: absolute;
  /* extends right, not left: the gutter sits at the viewport edge */
  inset: -13px -22px -13px -4px;
}

.chip-earlier {
  position: absolute;
  top: -7px;
  right: -5px;
  font-size: 10px;
  font-weight: 700;
  line-height: 1;
  color: var(--bs-body-color);
  background: var(--bs-body-bg);
  border-radius: 50%;
  pointer-events: none;
}

.chip-more {
  position: absolute;
  top: 100%;
  left: 50%;
  transform: translateX(-50%);
  margin-top: 3px;
  font-size: 10px;
  line-height: 1;
  white-space: nowrap;
  color: var(--bs-body-color);
  pointer-events: none;
}

.lane-chip.needs-attention {
  box-shadow: 0 0 0 2px var(--bs-warning-border-subtle, #ffc107), 0 1px 2px rgba(0, 0, 0, 0.15);
}

.chip-glyph {
  pointer-events: none;
}
</style>
