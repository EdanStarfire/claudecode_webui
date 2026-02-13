<template>
  <div class="chip-connector" :style="connectorStyle"></div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  depth: { type: Number, default: 1 },
  maxDepth: { type: Number, default: 1 }
})

// AgentChip is ~44px tall. Spread connector lines vertically across the chip.
// depth 1 at top region, maxDepth at bottom region.
const CHIP_HEIGHT = 44

const connectorStyle = computed(() => {
  // Number of distinct connector levels = maxDepth - 1
  // (root at level 0 has no connector; connectors exist at levels 1..maxDepth-1)
  const connectorLevels = props.maxDepth - 1
  if (connectorLevels <= 1) {
    // Single connector level: center the line
    return { alignSelf: 'center' }
  }
  // Spread lines across the chip height with padding
  // depth 1 → top, depth connectorLevels → bottom
  const pad = 8
  const usable = CHIP_HEIGHT - pad * 2
  const fraction = (props.depth - 1) / (connectorLevels - 1)
  const offset = pad + fraction * usable
  return {
    alignSelf: 'flex-start',
    marginTop: `${Math.round(offset)}px`
  }
})
</script>

<style scoped>
.chip-connector {
  width: 10px;
  height: 2px;
  background: #93c5fd;
  flex-shrink: 0;
}
</style>
