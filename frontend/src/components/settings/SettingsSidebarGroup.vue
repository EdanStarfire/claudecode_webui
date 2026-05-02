<template>
  <div class="settings-sidebar-group">
    <div class="group-header">
      <span class="group-label">{{ title }}</span>
    </div>
    <slot />
  </div>
</template>

<script setup>
defineProps({
  title: { type: String, required: true },
})
</script>

<style scoped>
.settings-sidebar-group {
  padding: 4px 0;
}

.group-header {
  padding: 8px 12px 4px;
  overflow: hidden;
}

.group-label {
  display: block;
  font-size: 10px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: #475569;
  white-space: nowrap;
}
</style>
