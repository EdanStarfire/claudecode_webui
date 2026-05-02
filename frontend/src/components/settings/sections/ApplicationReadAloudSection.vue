<template>
  <div class="app-section">
    <SettingsToolbar
      title="Read Aloud"
      :show-save-cancel="dirty"
      @save="save"
      @cancel="cancel"
    />
    <div class="section-body">
      <ReadAloudTab
        :config="config"
        @update:config="onUpdate"
      />
    </div>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'
import { getReadAloudSettings, updateReadAloudSettings } from '@/composables/useTTSReadAloud'
import ReadAloudTab from '@/components/configuration/ReadAloudTab.vue'
import SettingsToolbar from '../SettingsToolbar.vue'

const config = ref(getReadAloudSettings())
const originalConfig = ref(JSON.parse(JSON.stringify(config.value)))

const dirty = computed(() =>
  JSON.stringify(config.value) !== JSON.stringify(originalConfig.value)
)

function onUpdate(newConfig) {
  config.value = newConfig
}

function save() {
  updateReadAloudSettings(config.value)
  originalConfig.value = JSON.parse(JSON.stringify(config.value))
}

function cancel() {
  config.value = JSON.parse(JSON.stringify(originalConfig.value))
}
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
