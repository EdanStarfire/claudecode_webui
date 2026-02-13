<template>
  <div class="timeline-segment" :style="segmentStyle"></div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  leftColor: { type: String, default: '#e2e8f0' },
  rightColor: { type: String, default: '#e2e8f0' }
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
  align-self: center;
}
</style>
