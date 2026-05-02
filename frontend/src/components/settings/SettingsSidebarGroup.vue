<template>
  <div class="settings-sidebar-group">
    <div class="group-header">
      <span class="group-label group-label-full">{{ title }}</span>
      <span class="group-label group-label-short">{{ shortTitle || title }}</span>
    </div>
    <slot />
  </div>
</template>

<script setup>
defineProps({
  title:      { type: String, required: true },
  shortTitle: { type: String, default: '' },
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
  color: var(--bs-tertiary-color);
  white-space: nowrap;
}

/* Collapsed rail: show short label only */
.group-label-full  { display: block; }
.group-label-short { display: none; }

@container settings-area (max-width: 599px) {
  .group-label-full  { display: none; }
  .group-label-short { display: block; }
}
</style>
