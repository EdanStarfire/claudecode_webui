<template>
  <div class="timeline-segment" :class="{ 'segment-compact': compact }" :style="segmentStyle"></div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  leftColor: { type: String, default: '#e2e8f0' },
  rightColor: { type: String, default: '#e2e8f0' },
  compact: { type: Boolean, default: false }
})

const segmentStyle = computed(() => {
  if (props.leftColor === props.rightColor) {
    return { background: props.leftColor }
  }
  return {
    background: `linear-gradient(to right, ${props.leftColor}, ${props.rightColor})`
  }
})
</script>

<style scoped>
.timeline-segment {
  flex: 1;
  height: 2px;
  min-width: 16px;
  margin-top: 5px;
  align-self: flex-start;
}

.segment-compact {
  flex: 0 0 8px;
  min-width: 8px;
}
</style>
