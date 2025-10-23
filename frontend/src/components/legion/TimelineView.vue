<template>
  <div class="flex-grow-1 d-flex flex-column justify-content-center align-items-center text-center p-5">
    <h2 class="text-muted">🏛 Legion Timeline View</h2>
    <p class="text-secondary">
      Timeline view for Legion {{ legionId }}
    </p>
    <p class="text-muted small">
      (Full implementation in Phase 4)
    </p>
  </div>
</template>

<script setup>
const props = defineProps({
  legionId: {
    type: String,
    required: true
  }
})

// TODO: Phase 4 - Implement Legion timeline functionality
</script>
