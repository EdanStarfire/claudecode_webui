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
const CHIP_HEIGHT = 44
const PAD = 12
const USABLE = CHIP_HEIGHT - PAD * 2 // 28px usable range

const connectorStyle = computed(() => {
  const levels = props.maxDepth

  let offset
  if (levels <= 1) {
    offset = CHIP_HEIGHT / 2
  } else {
    const fraction = (props.depth - 1) / (levels - 1)
    offset = PAD + fraction * USABLE
  }

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
