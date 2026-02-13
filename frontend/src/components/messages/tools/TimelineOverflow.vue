<template>
  <div
    class="timeline-overflow"
    :title="`${count} earlier tool calls hidden`"
    @click.stop="$emit('toggle')"
  >
    +{{ count }}
  </div>
</template>

<script setup>
defineProps({
  count: { type: Number, required: true }
})

defineEmits(['toggle'])
</script>

<style scoped>
.timeline-overflow {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  height: 20px;
  padding: 0 6px;
  border-radius: 10px;
  background: #475569;
  color: white;
  font-size: 10px;
  font-weight: 600;
  cursor: pointer;
  transition: background 0.15s;
  user-select: none;
}

.timeline-overflow:hover {
  background: #334155;
}
</style>
