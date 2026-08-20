<template>
  <div class="global-subagent-gutter" aria-hidden="true">
    <div
      v-for="lane in lanes"
      :key="lane.key"
      class="gutter-lane"
      :style="{ top: `${lane.top}px`, height: `${lane.height}px` }"
    >
      <button
        type="button"
        class="lane-chip"
        :class="{ 'needs-attention': lane.needsAttention }"
        :style="lane.chipStyle"
        :title="lane.tooltipLabel"
        :aria-label="lane.tooltipLabel"
        @click="onChipClick(lane)"
      >
        <span class="chip-glyph">{{ '\u{1F916}' }}</span>
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

const emit = defineEmits(['chip-click'])

const messageStore = useMessageStore()

const CHIP_SIZE = 22
const CHIP_GAP = 6
const BASE_TOP = 8

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

  return withBounds.map(l => {
    const key = `${l.taskId}:${l.legIndex}`
    const slotIndex = slotAssignment.get(key) ?? 0
    const agentColor = getAgentColor(slugifyAgentName(l.taskId))
    const offsetPx = BASE_TOP + slotIndex * (CHIP_SIZE + CHIP_GAP)
    return {
      key,
      taskId: l.taskId,
      legIndex: l.legIndex,
      top: l.top,
      height: l.height,
      needsAttention: messageStore.hasOpenPermissionForTask(props.sessionId, l.taskId),
      tooltipLabel: l.leg.description || 'Subagent',
      chipStyle: {
        // Same offset for both edges — see the component-level comment for why this alone
        // (native CSS, no scroll-direction detection) gives the bidirectional pin behavior.
        top: `${offsetPx}px`,
        bottom: `${offsetPx}px`,
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

.lane-chip.needs-attention {
  box-shadow: 0 0 0 2px var(--bs-warning-border-subtle, #ffc107), 0 1px 2px rgba(0, 0, 0, 0.15);
}

.chip-glyph {
  pointer-events: none;
}
</style>
