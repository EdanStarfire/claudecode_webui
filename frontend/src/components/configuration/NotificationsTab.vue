<template>
  <div class="notifications-tab">
    <!-- Notification Sounds Section -->
    <h6 class="mb-2">Notification Sounds</h6>
    <p class="text-muted small mb-3">
      Play short audio tones for important events like permission prompts,
      task completions, and errors. Uses synthesized tones &mdash; no audio files needed.
    </p>

    <div class="form-check form-switch mb-3">
      <input
        class="form-check-input"
        type="checkbox"
        id="soundEnabled"
        :checked="settings.soundEnabled"
        @change="toggle('soundEnabled', $event)"
      >
      <label class="form-check-label" for="soundEnabled">
        Enable Notification Sounds
      </label>
    </div>

    <div v-if="settings.soundEnabled" class="ms-3 mb-3">
      <label class="form-label small mb-1">
        Volume: {{ settings.volume }}%
      </label>
      <input
        type="range"
        class="form-range"
        min="0"
        max="100"
        step="5"
        :value="settings.volume"
        @input="updateField('volume', parseInt($event.target.value))"
      >

      <div class="mt-2 mb-2">
        <label class="form-label small mb-1">Events</label>
        <div v-for="evt in eventOptions" :key="evt.key" class="form-check mb-1">
          <input
            class="form-check-input"
            type="checkbox"
            :id="'evt-' + evt.key"
            :checked="settings.events[evt.key]"
            @change="toggleEvent(evt.key, $event)"
          >
          <label class="form-check-label small" :for="'evt-' + evt.key">
            {{ evt.label }}
          </label>
        </div>
      </div>

      <button
        class="btn btn-outline-secondary btn-sm"
        @click="onTestSound"
      >
        Test Sound
      </button>
    </div>

    <hr class="my-3">

    <!-- Text-to-Speech Section -->
    <template v-if="ttsAvailable">
      <h6 class="mb-2">Text-to-Speech</h6>
      <p class="text-muted small mb-3">
        Announce events using the browser's speech synthesis.
        Requires notification sounds to be enabled for the event.
      </p>

      <div class="form-check form-switch mb-3">
        <input
          class="form-check-input"
          type="checkbox"
          id="ttsEnabled"
          :checked="settings.ttsEnabled"
          @change="toggle('ttsEnabled', $event)"
        >
        <label class="form-check-label" for="ttsEnabled">
          Enable Text-to-Speech
        </label>
      </div>

      <div v-if="settings.ttsEnabled" class="ms-3 mb-3">
        <div class="mb-2">
          <label class="form-label small mb-1">Voice</label>
          <select
            class="form-select form-select-sm"
            :value="settings.ttsVoice"
            @change="updateField('ttsVoice', $event.target.value)"
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

        <div class="mb-2">
          <label class="form-label small mb-1">
            Speed: {{ settings.ttsSpeed }}x
          </label>
          <input
            type="range"
            class="form-range"
            min="0.5"
            max="2"
            step="0.25"
            :value="settings.ttsSpeed"
            @input="updateField('ttsSpeed', parseFloat($event.target.value))"
          >
        </div>

        <button
          class="btn btn-outline-secondary btn-sm"
          @click="onTestTTS"
        >
          Test TTS
        </button>
      </div>
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
import {
  getSettings,
  updateSettings,
  testSound,
  testTTS,
  getVoices,
  isTTSAvailable
} from '@/composables/useNotifications'

const settings = ref(getSettings())
const voices = ref([])
const ttsAvailable = ref(isTTSAvailable())

function toggle(field, event) {
  settings.value = updateSettings({ [field]: event.target.checked })
}

function updateField(field, value) {
  settings.value = updateSettings({ [field]: value })
}

function toggleEvent(key, event) {
  const events = { ...settings.value.events, [key]: event.target.checked }
  settings.value = updateSettings({ events })
}

function onTestSound() {
  testSound('task_complete')
}

function onTestTTS() {
  testTTS('This is a test notification')
}

function loadVoices() {
  voices.value = getVoices()
}

const eventOptions = [
  { key: 'permission_prompt', label: 'Permission prompts' },
  { key: 'task_complete', label: 'Agent task completion' },
  { key: 'session_error', label: 'Session errors' },
  { key: 'minion_comm', label: 'Minion communications (Legion)' }
]

onMounted(() => {
  loadVoices()
  // Voices may load asynchronously
  if (window.speechSynthesis) {
    window.speechSynthesis.addEventListener('voiceschanged', loadVoices)
  }
})
</script>
