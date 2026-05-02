<template>
  <div class="lib-section">
    <SettingsToolbar title="Secrets" />
    <div class="section-body">
      <SecretsTab />
    </div>
  </div>
</template>

<script setup>
import SecretsTab from '@/components/configuration/SecretsTab.vue'
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
