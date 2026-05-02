<template>
  <div class="lib-section">
    <SettingsToolbar title="Profiles" />
    <div class="section-body">
      <ProfileManagerTab />
    </div>
  </div>
</template>

<script setup>
import ProfileManagerTab from '@/components/configuration/ProfileManagerTab.vue'
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
