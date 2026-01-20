<template>
  <div class="hierarchy-status-bar border-top p-2 bg-light">
    <div class="text-center text-muted small">
      Read-only view
    </div>
  </div>
</template>

<script setup>
const props = defineProps({
  legionId: {
    type: String,
    required: true
  }
})
</script>

<style scoped>
.hierarchy-status-bar {
  background-color: #f8f9fa;
}
</style>
