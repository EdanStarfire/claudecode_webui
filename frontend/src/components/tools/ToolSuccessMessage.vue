<template>
  <div class="tool-success">
    <span class="success-icon">{{ icon }}</span>
    <strong>{{ message }}</strong>
    <span v-if="detail" class="success-detail">{{ detail }}</span>
  </div>
</template>

<script setup>
defineProps({
  message: { type: String, required: true },
  icon: { type: String, default: '✅' },
  detail: { type: String, default: null }
})
</script>

<style scoped>
.tool-success {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  background: var(--tool-success-bg, #dcfce7);
  border: 1px solid var(--tool-success-border, #86efac);
  border-radius: var(--tool-radius, 4px);
  padding: var(--tool-padding, 6px 8px);
  color: var(--tool-success-text, #166534);
  font-size: var(--tool-font-size, 13px);
}

.success-icon {
  font-size: 14px;
  flex-shrink: 0;
}

.success-detail {
  opacity: 0.8;
  font-size: var(--tool-code-font-size, 11px);
}
</style>
