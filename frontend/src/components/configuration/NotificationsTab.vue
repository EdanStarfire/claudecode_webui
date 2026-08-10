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
        :checked="config.soundEnabled"
        @change="toggle('soundEnabled', $event)"
      >
      <label class="form-check-label" for="soundEnabled">
        Enable Notification Sounds
      </label>
    </div>

    <div v-if="config.soundEnabled" class="ms-3 mb-3">
      <label class="form-label small mb-1">
        Volume: {{ config.volume }}%
      </label>
      <input
        type="range"
        class="form-range"
        min="0"
        max="100"
        step="5"
        :value="config.volume"
        @input="updateField('volume', parseInt($event.target.value))"
      >

      <div class="mt-2 mb-2">
        <label class="form-label small mb-1">Events</label>
        <div v-for="evt in eventOptions" :key="evt.key" class="form-check mb-1">
          <input
            class="form-check-input"
            type="checkbox"
            :id="'evt-' + evt.key"
            :checked="config.events[evt.key]"
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
          :checked="config.ttsEnabled"
          @change="toggle('ttsEnabled', $event)"
        >
        <label class="form-check-label" for="ttsEnabled">
          Enable Text-to-Speech
        </label>
      </div>

      <div v-if="config.ttsEnabled" class="ms-3 mb-3">
        <div class="mb-2">
          <label class="form-label small mb-1">Voice</label>
          <select
            class="form-select form-select-sm"
            :value="config.ttsVoice"
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
            Speed: {{ config.ttsSpeed }}x
          </label>
          <input
            type="range"
            class="form-range"
            min="0.5"
            max="2"
            step="0.25"
            :value="config.ttsSpeed"
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

    <hr class="my-3">

    <!-- Native Notifications Section -->
    <template v-if="nativeAvailable">
      <h6 class="mb-2">Native Notifications</h6>
      <p class="text-muted small mb-3">
        Show browser/OS notifications for important events, even when this tab
        isn't focused. Independent of the sound and text-to-speech settings above.
      </p>

      <div class="form-check form-switch mb-2">
        <input
          class="form-check-input"
          type="checkbox"
          id="nativeEnabled"
          :checked="config.nativeEnabled"
          @change="onToggleNative"
        >
        <label class="form-check-label" for="nativeEnabled">
          Enable Native Notifications
        </label>
      </div>

      <div class="small mb-3">
        <span v-if="nativePermission === 'denied'" class="text-danger">
          Notifications are blocked &mdash; enable them in your browser's site
          settings for this page.
        </span>
        <span v-else-if="nativePermission === 'granted'" class="text-success">
          Notification permission granted.
        </span>
        <span v-else class="text-muted">
          Notification permission not yet granted.
        </span>
      </div>

      <div v-if="config.nativeEnabled" class="ms-3 mb-3">
        <div class="mt-2 mb-2">
          <label class="form-label small mb-1">Events</label>
          <div v-for="evt in nativeEventOptions" :key="evt.key" class="form-check mb-1">
            <input
              class="form-check-input"
              type="checkbox"
              :id="'native-evt-' + evt.key"
              :checked="config.nativeEvents[evt.key]"
              @change="toggleNativeEvent(evt.key, $event)"
            >
            <label class="form-check-label small" :for="'native-evt-' + evt.key">
              {{ evt.label }}
            </label>
          </div>
        </div>

        <button
          class="btn btn-outline-secondary btn-sm"
          :disabled="nativePermission !== 'granted'"
          @click="onTestNativeNotification"
        >
          Test Notification
        </button>
      </div>
    </template>
    <template v-else>
      <h6 class="mb-2">Native Notifications</h6>
      <p class="text-muted small">
        Native notifications are not available in this browser.
      </p>
    </template>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import {
  testSound,
  testTTS,
  getVoices,
  isTTSAvailable,
  isNativeNotificationAvailable,
  getNativePermission,
  requestNativePermission,
  testNativeNotification
} from '@/composables/useNotifications'

const props = defineProps({
  config: { type: Object, default: () => ({}) }
})

const emit = defineEmits(['update:config'])

const voices = ref([])
const ttsAvailable = ref(isTTSAvailable())
const nativeAvailable = ref(isNativeNotificationAvailable())
const nativePermission = ref(getNativePermission())

function toggle(field, event) {
  emit('update:config', { ...props.config, [field]: event.target.checked })
}

function updateField(field, value) {
  emit('update:config', { ...props.config, [field]: value })
}

function toggleEvent(key, event) {
  const events = { ...props.config.events, [key]: event.target.checked }
  emit('update:config', { ...props.config, events })
}

function onTestSound() {
  testSound('task_complete')
}

function onTestTTS() {
  testTTS('This is a test notification')
}

async function onToggleNative(event) {
  const checked = event.target.checked
  if (!checked) {
    emit('update:config', { ...props.config, nativeEnabled: false })
    return
  }

  const result = await requestNativePermission()
  nativePermission.value = getNativePermission()
  if (result === 'granted') {
    emit('update:config', { ...props.config, nativeEnabled: true })
  } else {
    // Permission wasn't granted, so nativeEnabled stays false. Since that value
    // didn't change, Vue won't re-sync :checked — reset the DOM checkbox manually
    // so it doesn't visually diverge from the (unchanged) disabled state.
    event.target.checked = false
  }
}

function toggleNativeEvent(key, event) {
  const nativeEvents = { ...props.config.nativeEvents, [key]: event.target.checked }
  emit('update:config', { ...props.config, nativeEvents })
}

function onTestNativeNotification() {
  testNativeNotification()
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

const nativeEventOptions = [
  { key: 'permission_prompt', label: 'Permission prompts' },
  { key: 'task_complete', label: 'Agent task completion' },
  { key: 'session_error', label: 'Session errors' },
  { key: 'minion_comm', label: 'Minion communications (Legion)' },
  { key: 'session_restart_error', label: 'Session restart errors' }
]

onMounted(() => {
  loadVoices()
  if (window.speechSynthesis) {
    window.speechSynthesis.addEventListener('voiceschanged', loadVoices)
  }
  nativePermission.value = getNativePermission()
})
</script>
