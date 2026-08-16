<template>
  <div class="subagent-anchor-group">
    <div class="gutter-col">
      <button
        type="button"
        class="lane-chip"
        :class="{ 'needs-attention': needsAttention, 'lane-chip-pending': slotIndex === null }"
        :style="chipStyle"
        :title="tooltipLabel"
        :aria-label="tooltipLabel"
        @click="$emit('toggle')"
      >
        <span class="chip-glyph">{{ glyph }}</span>
      </button>
    </div>
    <div class="rows-col">
      <slot />
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  agentColor: {
    type: Object,
    required: true
  },
  needsAttention: {
    type: Boolean,
    default: false
  },
  tooltipLabel: {
    type: String,
    default: ''
  },
  glyph: {
    type: String,
    default: '\u{1F916}'
  },
  // Issue #1746 (stage: subagents): dynamic sticky-offset slot claimed from the shared
  // message-store registry (message.js claimGutterSlot/releaseGutterSlot) — null while the
  // leg isn't concurrently active (idle gap / not yet claimed), in which case the chip parks
  // at the default top offset.
  slotIndex: {
    type: Number,
    default: null
  }
})

defineEmits(['toggle'])

const CHIP_SIZE = 22
const CHIP_GAP = 6
const BASE_TOP = 8

const chipStyle = computed(() => {
  const offset = BASE_TOP + (props.slotIndex ?? 0) * (CHIP_SIZE + CHIP_GAP)
  return {
    top: `${offset}px`,
    background: props.agentColor.bg,
    borderColor: props.agentColor.border,
    color: props.agentColor.accent,
  }
})
</script>

<style scoped>
/* Issue #1746 (stage: subagents): 26px gutter + 1fr content, per spec §6. Flush at the same
   left edge as the rest of the message content in every AssistantMessage instance — this is
   what keeps chips from independent SubagentTimeline instances visually column-aligned even
   though they're separate DOM subtrees (see plan discussion / #1746 review thread). */
.subagent-anchor-group {
  display: grid;
  grid-template-columns: 26px 1fr;
  column-gap: 4px;
  /* stretch (not start): the gutter column must match the row's actual height (driven by
     .rows-col's content) or the sticky chip's containing block is just its own tiny box,
     with no scroll range to remain "stuck" through. Note this is still a KNOWN LIMITATION for
     the common collapsed case (a leg's own anchor rows are only ~50-60px tall, so the chip's
     sticky range is small until the transcript is expanded) — an earlier attempt to widen this
     range with an artificial spacer element was reverted because it pushed a large visible
     blank gap into the conversation for the entire duration a subagent runs, which is worse
     than the original small-range issue. Flagged as an open follow-up, not solved here. */
  align-items: stretch;
}

.gutter-col {
  position: relative;
  align-self: stretch;
}

/* position: sticky containment is this element's own parent box (.gutter-col, whose height
   tracks .rows-col via the shared grid row) — so the chip is naturally "stuck" only while
   scrolled within THIS leg's own row range, and stops being sticky (scrolls away normally)
   once the leg's rows have fully scrolled past. That's the "idle gap = chip absent" behavior
   from spec §6, achieved without any global row-index bookkeeping. */
.lane-chip {
  position: sticky;
  width: 22px;
  height: 22px;
  /* Centering via margin auto, not flex justify-content — flex centering fails for a sticky
     child per spec §6's own prototyping notes. */
  margin: 0 auto;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 50%;
  border: 2px solid;
  z-index: 5;
  cursor: pointer;
  padding: 0;
  font-size: 11px;
  line-height: 1;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.15);
}

.lane-chip.needs-attention {
  box-shadow: 0 0 0 2px var(--bs-warning-border-subtle, #ffc107), 0 1px 2px rgba(0, 0, 0, 0.15);
}

/* Issue #1746 (stage: subagents) review fix: before task_started resolves (no real slot
   claimed yet), don't compete for slot 0's sticky offset with an already-active leg — render
   statically in normal flow for this brief window instead of sticky. */
.lane-chip-pending {
  position: static;
  margin: 0 auto;
}

.chip-glyph {
  pointer-events: none;
}

.rows-col {
  min-width: 0;
}
</style>
