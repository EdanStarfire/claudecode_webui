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
  if (props.maxDepth <= 1) {
    // Single depth: center the line
    return { alignSelf: 'center' }
  }
  // Spread lines across the chip height with padding
  const pad = 8
  const usable = CHIP_HEIGHT - pad * 2
  const offset = pad + (props.depth - 1) / (props.maxDepth - 1) * usable
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
