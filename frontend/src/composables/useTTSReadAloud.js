/**
 * TTS Read Aloud composable — reads assistant messages aloud via Web Speech API.
 *
 * Separate from useNotifications (which handles short event announcements).
 * Settings stored in localStorage with 'webui-tts-readaloud-settings' key.
 *
 * Issue #735
 */

import { ref, watch, onUnmounted } from 'vue'
import { useSessionStore } from '@/stores/session'
import { usePollingStore } from '@/stores/polling'
import { useUIStore } from '@/stores/ui'
import { isTTSAvailable, getVoices } from '@/composables/useNotifications'

const SETTINGS_KEY = 'webui-tts-readaloud-settings'

const DEFAULT_SETTINGS = {
  voice: '',
  rate: 1.0
}

// ========== Settings (module-level, shared) ==========

export function getReadAloudSettings() {
  try {
    const raw = localStorage.getItem(SETTINGS_KEY)
    if (raw) {
      return { ...DEFAULT_SETTINGS, ...JSON.parse(raw) }
    }
  } catch {
    // corrupted
  }
  return { ...DEFAULT_SETTINGS }
}

export function updateReadAloudSettings(partial) {
  const current = getReadAloudSettings()
  const updated = { ...current, ...partial }
  localStorage.setItem(SETTINGS_KEY, JSON.stringify(updated))
  return updated
}

export function testReadAloud(text = 'This is a test of the read aloud feature.', overrideSettings = null) {
  if (!isTTSAvailable()) return
  window.speechSynthesis.cancel()
  const settings = overrideSettings || getReadAloudSettings()
  const utterance = new SpeechSynthesisUtterance(text)
  utterance.rate = settings.rate || 1.0
  if (settings.voice) {
    const voices = getVoices()
    const voice = voices.find(v => v.voiceURI === settings.voice)
    if (voice) utterance.voice = voice
  }
  window.speechSynthesis.speak(utterance)
}

// ========== Text normalization ==========

export function normalizeForSpeech(text) {
  if (!text) return ''

  let result = text

  // Remove image syntax ![alt](url)
  result = result.replace(/!\[([^\]]*)\]\([^)]*\)/g, '')

  // Convert links [text](url) to just text
  result = result.replace(/\[([^\]]*)\]\([^)]*\)/g, '$1')

  // Strip heading markers
  result = result.replace(/^#{1,6}\s+/gm, '')

  // Strip bold/italic markers
  result = result.replace(/\*{1,3}([^*]+)\*{1,3}/g, '$1')
  result = result.replace(/_{1,3}([^_]+)_{1,3}/g, '$1')

  // Strip strikethrough
  result = result.replace(/~~([^~]+)~~/g, '$1')

  // Remove HTML tags
  result = result.replace(/<[^>]+>/g, '')

  // Remove mermaid code blocks entirely (diagram source is not readable)
  result = result.replace(/```mermaid\n[\s\S]*?```/g, '')

  // Remove fenced code block markers but keep content
  result = result.replace(/```[\w]*\n?/g, '')

  // Remove inline backticks but keep content
  result = result.replace(/`([^`]+)`/g, '$1')

  // Collapse multiple newlines into a single pause marker
  result = result.replace(/\n{2,}/g, '\n\n')

  // Trim
  result = result.trim()

  return result
}

// ========== Composable ==========

export function useTTSReadAloud() {
  const sessionStore = useSessionStore()
  const wsStore = usePollingStore()
  const uiStore = useUIStore()

  const isPlaying = ref(false)
  const isPaused = ref(false)
  const currentMessageId = ref(null)

  // Internal state
  let messageQueue = []
  let currentUtterances = []
  let stopRequested = false
  let pauseTimerId = null

  function _getVoice() {
    const settings = getReadAloudSettings()
    if (!settings.voice) return null
    const voices = getVoices()
    return voices.find(v => v.voiceURI === settings.voice) || null
  }

  /**
   * Split text into paragraphs for chunked playback.
   * Each paragraph becomes a separate utterance to avoid blocking.
   */
  function _chunkText(text) {
    const paragraphs = text.split(/\n\n+/).filter(p => p.trim().length > 0)
    if (paragraphs.length === 0) return [text]
    return paragraphs
  }

  /**
   * Speak a single chunk of text. Returns a Promise that resolves on end.
   */
  function _speakChunk(text) {
    return new Promise((resolve, reject) => {
      if (stopRequested || !isTTSAvailable()) {
        reject(new Error('stopped'))
        return
      }

      const settings = getReadAloudSettings()
      const utterance = new SpeechSynthesisUtterance(text)
      utterance.rate = settings.rate

      const voice = _getVoice()
      if (voice) utterance.voice = voice

      utterance.onend = () => resolve()
      utterance.onerror = (e) => {
        if (e.error === 'canceled' || e.error === 'interrupted') {
          reject(new Error('stopped'))
        } else {
          resolve() // Skip on other errors, continue to next chunk
        }
      }

      currentUtterances.push(utterance)
      window.speechSynthesis.speak(utterance)
    })
  }

  /**
   * Speak full message text (chunked by paragraphs).
   */
  async function _speakText(text) {
    const normalized = normalizeForSpeech(text)
    if (!normalized) return

    const chunks = _chunkText(normalized)
    for (const chunk of chunks) {
      if (stopRequested) break
      try {
        await _speakChunk(chunk)
      } catch {
        break // stopped
      }
    }
  }

  /**
   * Play from a specific message, then auto-continue forward through assistant messages.
   * Identifies the start message by matching the object reference in allMessages,
   * falling back to timestamp comparison (messages lack unique IDs from the backend).
   */
  async function playMessage(messageRef, allMessages) {
    stop()
    stopRequested = false

    // Find start index — try reference equality first, then timestamp match
    let startIdx = allMessages.indexOf(messageRef)
    if (startIdx < 0 && messageRef.timestamp) {
      startIdx = allMessages.findIndex(m =>
        m.timestamp === messageRef.timestamp && m.type === messageRef.type
      )
    }
    if (startIdx < 0) return

    isPlaying.value = true
    isPaused.value = false

    // Build queue of assistant messages from startIdx forward
    messageQueue = []
    for (let i = startIdx; i < allMessages.length; i++) {
      const msg = allMessages[i]
      if (msg.type === 'assistant') {
        const content = msg.content || ''
        if (content.trim().length > 0 && content !== 'Assistant response') {
          messageQueue.push({
            id: msg.timestamp || `tts-${i}`,
            content: content
          })
        }
      }
    }

    await _processQueue()
  }

  /**
   * Queue a new assistant message for playback (called on new message arrival).
   */
  async function queueNewMessage(message) {
    if (!uiStore.ttsReadAloudEnabled) return
    if (!message || message.type !== 'assistant') return

    const content = message.content || ''
    if (!content.trim() || content === 'Assistant response') return

    const id = message.timestamp || `tts-${Date.now()}`

    // If already playing, append to queue
    if (isPlaying.value) {
      messageQueue.push({ id, content })
      return
    }

    // Start fresh playback
    stopRequested = false
    isPlaying.value = true
    isPaused.value = false
    messageQueue = [{ id, content }]
    await _processQueue()
  }

  async function _processQueue() {
    while (messageQueue.length > 0 && !stopRequested) {
      const item = messageQueue.shift()
      currentMessageId.value = item.id

      await _speakText(item.content)

      if (stopRequested) break

      // Brief pause between messages
      if (messageQueue.length > 0) {
        await new Promise((resolve) => {
          pauseTimerId = setTimeout(resolve, 500)
        })
        pauseTimerId = null
      }
    }

    // Done
    if (!stopRequested) {
      _cleanup()
    }
  }

  function pause() {
    if (!isPlaying.value || isPaused.value) return
    if (isTTSAvailable()) {
      window.speechSynthesis.pause()
    }
    isPaused.value = true
  }

  function resume() {
    if (!isPlaying.value || !isPaused.value) return
    if (isTTSAvailable()) {
      window.speechSynthesis.resume()
    }
    isPaused.value = false
  }

  function stop() {
    stopRequested = true
    messageQueue = []
    if (pauseTimerId) {
      clearTimeout(pauseTimerId)
      pauseTimerId = null
    }
    if (isTTSAvailable()) {
      window.speechSynthesis.cancel()
    }
    currentUtterances = []
    _cleanup()
  }

  function _cleanup() {
    isPlaying.value = false
    isPaused.value = false
    currentMessageId.value = null
    messageQueue = []
    currentUtterances = []
  }

  // Stop on session switch
  watch(() => sessionStore.currentSessionId, () => {
    if (isPlaying.value) stop()
  })

  // Stop on session termination/error
  watch(() => {
    const session = sessionStore.sessions.get(sessionStore.currentSessionId)
    return session?.state
  }, (newState) => {
    if (isPlaying.value && (newState === 'terminated' || newState === 'error')) {
      stop()
    }
  })

  // Stop on WebSocket disconnect
  watch(() => wsStore.sessionConnected, (connected) => {
    if (isPlaying.value && !connected) stop()
  })

  // Cleanup on unmount
  onUnmounted(() => {
    stop()
  })

  return {
    isPlaying,
    isPaused,
    currentMessageId,
    playMessage,
    queueNewMessage,
    pause,
    resume,
    stop
  }
}
