<template>
  <div class="global-subagent-gutter" aria-hidden="true">
    <div
      v-for="lane in lanes"
      :key="lane.taskId"
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
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useMessageStore } from '@/stores/message'
import { getAgentColor, slugifyAgentName } from '@/composables/useAgentColor'

// Issue #1746 (stage: subagents) follow-up: a persistent gutter OUTSIDE the message flow,
// mounted ONCE per session view (see MessageList.vue) — not nested inside each subagent's own
// card. A subagent leg can run across many unrelated user turns and assistant responses; a
// chip scoped to one card's own small DOM footprint can't stay visible for that whole span
// (confirmed both by the spec's §6 gutter-mechanics intent and by direct user feedback during
// this stage's build). Each active leg gets a lane spanning from its own launch anchor's real
// DOM position down to the current bottom of the conversation, measured directly — this
// codebase has no virtualized row-index system, so real measurement (not CSS containment
// tricks) is what makes an accurate span possible.
const props = defineProps({
  sessionId: {
    type: String,
    default: null
  },
  // Actual DOM elements (not template refs) — passed down once resolved by the parent.
  contentEl: {
    type: Object,
    default: null
  },
  areaEl: {
    type: Object,
    default: null
  }
})

const messageStore = useMessageStore()

const CHIP_SIZE = 22
const CHIP_GAP = 6
const BASE_TOP = 8

// One entry per task_id whose LATEST leg is currently running — a completed/idle leg has no
// lane (matches spec §6 "idle gap = chip absent"), and once the latest leg ends, the lane
// disappears entirely (matches "lives on... until it's completed").
const activeEntries = computed(() => {
  if (!props.sessionId) return []
  const result = []
  for (const entry of messageStore.allTaskLegEntriesForSession(props.sessionId)) {
    const legIndex = entry.legs.length - 1
    const leg = entry.legs[legIndex]
    if (leg && leg.status === 'running') {
      result.push({ taskId: entry.task_id, legIndex, leg })
    }
  }
  return result
})

const offsets = ref(new Map()) // taskId -> { top, height }

function measure() {
  if (!props.contentEl || !props.areaEl) return
  const contentRect = props.contentEl.getBoundingClientRect()
  const scrollTop = props.areaEl.scrollTop
  const contentHeight = props.contentEl.scrollHeight
  const next = new Map()
  for (const { taskId, legIndex } of activeEntries.value) {
    const startEl = document.getElementById(`subagent-anchor-primary-${taskId}-${legIndex}`)
    if (!startEl) continue
    const startRect = startEl.getBoundingClientRect()
    // Scroll-invariant: viewport-relative offset + current scrollTop = offset from content top,
    // regardless of current scroll position. Recomputed only when content size or the active
    // leg set changes (see watchers below) — never on scroll itself, since CSS position:sticky
    // on the chip handles staying pinned while scrolling within [top, top+height] natively.
    const top = (startRect.top - contentRect.top) + scrollTop
    const height = Math.max(0, contentHeight - top)
    next.set(taskId, { top, height })
  }
  offsets.value = next
}

let resizeObserver = null
function setupObserver() {
  if (resizeObserver || !props.contentEl || typeof ResizeObserver === 'undefined') return
  resizeObserver = new ResizeObserver(() => measure())
  resizeObserver.observe(props.contentEl)
}

onMounted(() => {
  setupObserver()
  nextTick(measure)
})
onBeforeUnmount(() => {
  if (resizeObserver) {
    resizeObserver.disconnect()
    resizeObserver = null
  }
  // Release every slot this instance's lanes currently hold — a session switch or unmount
  // must not leave a claimed slot permanently unreleased.
  for (const { taskId } of activeEntries.value) messageStore.releaseGutterSlot(taskId)
})

// contentEl can resolve AFTER this component's own onMounted fires (child components mount
// before their parent's own template refs are necessarily settled) — (re)attach once available.
watch(() => props.contentEl, (el) => {
  if (el) { setupObserver(); nextTick(measure) }
})

// Recompute when the SET of active legs changes (new lane appears/disappears) — content-size
// changes are already covered by the ResizeObserver above.
watch(activeEntries, () => nextTick(measure), { deep: true })

// Slot claim/release, centralized here (one decision point for the whole gutter, instead of
// each anchor instance independently claiming as before).
const claimedSlots = new Set()
watch(activeEntries, (entries) => {
  const currentIds = new Set(entries.map(e => e.taskId))
  for (const taskId of Array.from(claimedSlots)) {
    if (!currentIds.has(taskId)) {
      messageStore.releaseGutterSlot(taskId)
      claimedSlots.delete(taskId)
    }
  }
  for (const taskId of currentIds) {
    if (!claimedSlots.has(taskId)) {
      messageStore.claimGutterSlot(taskId)
      claimedSlots.add(taskId)
    }
  }
}, { deep: true, immediate: true })

const lanes = computed(() => {
  return activeEntries.value.map(({ taskId, legIndex, leg }) => {
    const off = offsets.value.get(taskId)
    if (!off) return null
    const slotIndex = messageStore.claimGutterSlot(taskId) // idempotent — returns the held slot
    const agentColor = getAgentColor(slugifyAgentName(taskId))
    return {
      taskId,
      legIndex,
      top: off.top,
      height: off.height,
      needsAttention: messageStore.hasOpenPermissionForTask(props.sessionId, taskId),
      tooltipLabel: leg.description || 'Subagent',
      chipStyle: {
        top: `${BASE_TOP + slotIndex * (CHIP_SIZE + CHIP_GAP)}px`,
        background: agentColor.bg,
        borderColor: agentColor.border,
        color: agentColor.accent,
      },
    }
  }).filter(Boolean)
})

function onChipClick(lane) {
  messageStore.toggleLegExpanded(lane.taskId, lane.legIndex)
  const el = document.getElementById(`subagent-anchor-primary-${lane.taskId}-${lane.legIndex}`)
  if (el) el.scrollIntoView({ behavior: 'smooth', block: 'center' })
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
