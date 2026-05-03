<template>
  <div class="settings-sidebar-group" :class="{ 'is-tinted': tinted }">
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
  tinted:     { type: Boolean, default: false },
})
</script>

<style scoped>
.settings-sidebar-group {
  padding: 4px 0;
}

.settings-sidebar-group.is-tinted {
  background: var(--mode-tint);
  margin: 4px 8px;
  border-radius: 8px;
  border: 1px solid var(--mode-border, transparent);
  padding: 4px 0 6px;
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

.is-tinted .group-label {
  color: var(--mode-fg);
}

/* Collapsed rail: show short label only */
.group-label-full  { display: block; }
.group-label-short { display: none; }

@container settings-area (max-width: 599px) {
  .group-label-full  { display: none; }
  .group-label-short { display: block; }

  .settings-sidebar-group.is-tinted {
    margin: 4px 4px;
  }
}
</style>
