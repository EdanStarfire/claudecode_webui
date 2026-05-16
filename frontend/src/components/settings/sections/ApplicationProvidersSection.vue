<template>
  <div class="app-section">
    <SettingsToolbar title="Providers" />
    <div class="section-body">
      <ProvidersTab />
    </div>
  </div>
</template>

<script setup>
import ProvidersTab from '@/components/configuration/ProvidersTab.vue'
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
