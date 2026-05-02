<template>
  <div class="app-section">
    <SettingsToolbar title="MCP Servers" />
    <div class="section-body">
      <McpConfigTab />
    </div>
  </div>
</template>

<script setup>
import McpConfigTab from '@/components/configuration/McpConfigTab.vue'
import SettingsToolbar from '../SettingsToolbar.vue'
</script>

<style scoped>
.app-section {
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
