<template>
  <div class="lib-section">
    <SettingsToolbar title="Hooks" />
    <div class="section-body">
      <HookPanel />
    </div>
  </div>
</template>

<script setup>
import HookPanel from '@/components/configuration/HookPanel.vue'
import SettingsToolbar from '../SettingsToolbar.vue'
</script>

<style scoped>
.lib-section {
  display: flex;
  flex-direction: column;
  height: 100%;
  overflow: hidden;
}

.section-body {
  flex: 1;
  padding: 20px;
  overflow-y: auto;
}
</style>
