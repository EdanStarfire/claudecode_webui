<template>
  <div class="readaloud-tab">
    <template v-if="ttsAvailable">
      <h6 class="mb-2">Read Aloud</h6>
      <p class="text-muted small mb-3">
        Read assistant messages aloud using the browser's speech synthesis.
        Enable the toggle in the session status bar to auto-read new messages,
        or click the play icon on any assistant message.
      </p>

      <div class="mb-3">
        <label class="form-label small mb-1">Voice</label>
        <select
          class="form-select form-select-sm"
          :value="config.voice"
          @change="updateField('voice', $event.target.value)"
        >
          <option value="">Browser default</option>
          <option
            v-for="voice in voices"
            :key="voice.voiceURI"
            :value="voice.voiceURI"
          >
            {{ voice.name }} ({{ voice.lang }})
          </option>
        </select>
      </div>

      <div class="mb-3">
        <label class="form-label small mb-1">
          Speed: {{ config.rate || 1.0 }}x
        </label>
        <input
          type="range"
          class="form-range"
          min="0.5"
          max="2"
          step="0.25"
          :value="config.rate || 1.0"
          @input="updateField('rate', parseFloat($event.target.value))"
        >
      </div>

      <button
        class="btn btn-outline-secondary btn-sm"
        @click="onTest"
      >
        Test Read Aloud
      </button>
    </template>
    <template v-else>
      <p class="text-muted small">
        Text-to-Speech is not available in this browser.
      </p>
    </template>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { getVoices, isTTSAvailable } from '@/composables/useNotifications'
import { testReadAloud } from '@/composables/useTTSReadAloud'

const props = defineProps({
  config: { type: Object, default: () => ({}) }
})

const emit = defineEmits(['update:config'])

const voices = ref([])
const ttsAvailable = ref(isTTSAvailable())

function updateField(field, value) {
  emit('update:config', { ...props.config, [field]: value })
}

function onTest() {
  testReadAloud('This is a test of the read aloud feature.', props.config)
}

function loadVoices() {
  voices.value = getVoices()
}

onMounted(() => {
  loadVoices()
  if (window.speechSynthesis) {
    window.speechSynthesis.addEventListener('voiceschanged', loadVoices)
  }
})
</script>
