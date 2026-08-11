/**
 * Notification composable — audio alerts and TTS for WebUI events.
 *
 * Uses Web Audio API for synthesized tones and Web Speech API for TTS.
 * Settings are persisted in localStorage with 'webui-notification-' prefix.
 * Singleton AudioContext shared across all callers.
 */

const STORAGE_KEY = 'webui-notification-settings'
const CROSS_TAB_STORAGE_KEY = 'webui-native-notif-lastfired'
const CROSS_TAB_SUPPRESS_WINDOW_MS = 2000

const DEFAULT_SETTINGS = {
  soundEnabled: false,
  ttsEnabled: false,
  volume: 50,
  ttsVoice: '',
  ttsSpeed: 1.0,
  events: {
    permission_prompt: true,
    task_complete: true,
    session_error: true,
    minion_comm: false,
    session_restart_error: true,
  },
  nativeEnabled: false,
  nativeEvents: {
    permission_prompt: true,
    task_complete: true,
    session_error: true,
    minion_comm: false,
    session_restart_error: true,
  },
  // Issue #1725: tray defaults to ON — passive/non-intrusive, unlike sound/native which opt-in
  trayEnabled: true,
  trayEvents: {
    permission_prompt: true,
    task_complete: true,
    session_error: true,
    minion_comm: false,
    session_restart_error: true,
  }
}

// Short titles for native notifications per event type
const NATIVE_TITLES = {
  permission_prompt: 'Permission Required',
  task_complete: 'Task Complete',
  session_error: 'Session Error',
  minion_comm: 'Minion Communication',
  session_restart_error: 'Session Restart Error'
}

// Event-specific TTS message templates
const TTS_TEMPLATES = {
  permission_prompt: (ctx) => {
    const tool = ctx?.toolName || 'a tool'
    return `Permission required for ${tool}`
  },
  task_complete: (ctx) => {
    const name = ctx?.sessionName || 'Session'
    return `${name} has completed processing`
  },
  session_error: (ctx) => {
    const name = ctx?.sessionName || 'Session'
    return `${name} encountered an error`
  },
  minion_comm: (ctx) => {
    const from = ctx?.fromMinion || 'A minion'
    const type = ctx?.commType || 'message'
    return `${from} sent a ${type}`
  },
  session_restart_error: (ctx) => {
    const id = ctx?.sessionId || 'Session'
    return `Restart error for ${id}: ${ctx?.error || 'unknown error'}`
  }
}

/**
 * Resolve the tray entry title/body for an event type, reusing the same
 * copy already shown in native notifications for consistent messaging
 * across all three channels (Issue #1725).
 */
export function getEventLabel(eventType, context = {}) {
  const title = NATIVE_TITLES[eventType] || 'Claude WebUI'
  const templateFn = TTS_TEMPLATES[eventType]
  const body = templateFn ? templateFn(context) : ''
  return { title, body }
}

// Singleton state
let audioCtx = null
let debounceMap = new Map()
let ttsQueue = []
let ttsSpeaking = false
let resumeListenerAdded = false

/**
 * Get or create the shared AudioContext (lazy, requires user gesture).
 */
function getAudioContext() {
  if (!audioCtx) {
    try {
      audioCtx = new (window.AudioContext || window.webkitAudioContext)()
    } catch {
      console.warn('Web Audio API not available')
      return null
    }
  }

  // Resume if suspended (autoplay policy)
  if (audioCtx.state === 'suspended') {
    audioCtx.resume().catch(() => {})
  }

  // One-time document click listener to resume AudioContext
  if (!resumeListenerAdded) {
    resumeListenerAdded = true
    const resume = () => {
      if (audioCtx && audioCtx.state === 'suspended') {
        audioCtx.resume()
      }
      document.removeEventListener('click', resume)
      document.removeEventListener('keydown', resume)
    }
    document.addEventListener('click', resume, { once: true })
    document.addEventListener('keydown', resume, { once: true })
  }

  return audioCtx
}

/**
 * Play a single tone.
 */
function playTone(frequency, duration, waveType, gain) {
  const ctx = getAudioContext()
  if (!ctx) return

  const osc = ctx.createOscillator()
  const gainNode = ctx.createGain()

  osc.type = waveType
  osc.frequency.setValueAtTime(frequency, ctx.currentTime)

  gainNode.gain.setValueAtTime(gain, ctx.currentTime)
  // Fade out to avoid click
  gainNode.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + duration)

  osc.connect(gainNode)
  gainNode.connect(ctx.destination)

  osc.start(ctx.currentTime)
  osc.stop(ctx.currentTime + duration)
}

/**
 * Tone definitions per event type.
 */
const TONE_DEFS = {
  permission_prompt(gain) {
    // Dual-tone attention signal
    playTone(880, 0.2, 'sine', gain)
    playTone(1100, 0.2, 'sine', gain * 0.8)
  },
  task_complete(gain) {
    // Ascending arpeggio (success)
    const ctx = getAudioContext()
    if (!ctx) return
    const notes = [523, 659, 784]
    notes.forEach((freq, i) => {
      const osc = ctx.createOscillator()
      const g = ctx.createGain()
      osc.type = 'sine'
      osc.frequency.setValueAtTime(freq, ctx.currentTime)
      g.gain.setValueAtTime(gain, ctx.currentTime + i * 0.1)
      g.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + i * 0.1 + 0.15)
      osc.connect(g)
      g.connect(ctx.destination)
      osc.start(ctx.currentTime + i * 0.1)
      osc.stop(ctx.currentTime + i * 0.1 + 0.15)
    })
  },
  session_error(gain) {
    // Low square wave warning
    playTone(220, 0.4, 'square', gain * 0.5)
  },
  minion_comm(gain) {
    // Subtle sine ping
    playTone(660, 0.15, 'sine', gain)
  },
  session_restart_error(gain) {
    // Low square wave warning (same as session_error)
    playTone(220, 0.4, 'square', gain * 0.5)
  }
}

/**
 * Read settings from localStorage.
 */
export function getSettings() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (raw) {
      const saved = JSON.parse(raw)
      // Merge with defaults to fill any missing keys
      return {
        ...DEFAULT_SETTINGS,
        ...saved,
        events: { ...DEFAULT_SETTINGS.events, ...(saved.events || {}) },
        nativeEvents: { ...DEFAULT_SETTINGS.nativeEvents, ...(saved.nativeEvents || {}) },
        trayEvents: { ...DEFAULT_SETTINGS.trayEvents, ...(saved.trayEvents || {}) }
      }
    }
  } catch {
    // Corrupted data, return defaults
  }
  return {
    ...DEFAULT_SETTINGS,
    events: { ...DEFAULT_SETTINGS.events },
    nativeEvents: { ...DEFAULT_SETTINGS.nativeEvents },
    trayEvents: { ...DEFAULT_SETTINGS.trayEvents }
  }
}

/**
 * Update settings (partial merge).
 */
export function updateSettings(partial) {
  const current = getSettings()
  const updated = {
    ...current,
    ...partial,
    events: { ...current.events, ...(partial.events || {}) },
    nativeEvents: { ...current.nativeEvents, ...(partial.nativeEvents || {}) },
    trayEvents: { ...current.trayEvents, ...(partial.trayEvents || {}) }
  }
  localStorage.setItem(STORAGE_KEY, JSON.stringify(updated))
  return updated
}

/**
 * Check debounce — returns true if event should be skipped.
 */
function shouldDebounce(eventType) {
  const now = Date.now()
  const last = debounceMap.get(eventType) || 0
  if (now - last < 500) return true
  debounceMap.set(eventType, now)
  return false
}

/**
 * Check if the Notification API is available in this browser.
 */
export function isNativeNotificationAvailable() {
  return typeof Notification !== 'undefined'
}

/**
 * Get the current native notification permission state.
 * @returns {'unsupported' | 'default' | 'granted' | 'denied'}
 */
export function getNativePermission() {
  if (!isNativeNotificationAvailable()) return 'unsupported'
  return Notification.permission
}

/**
 * Request native notification permission. Must be called synchronously
 * from a UI click handler (no await before it) to satisfy browser
 * user-gesture requirements.
 */
export async function requestNativePermission() {
  if (!isNativeNotificationAvailable()) return 'unsupported'
  return Notification.requestPermission()
}

/**
 * Best-effort cross-tab dedup: returns true if this event fired in another
 * tab within the suppression window. Read-then-write, so there's a small
 * race window between tabs.
 */
function shouldSuppressCrossTab(eventType) {
  const now = Date.now()
  try {
    const raw = localStorage.getItem(CROSS_TAB_STORAGE_KEY)
    const lastFired = raw ? JSON.parse(raw) : {}
    const last = lastFired[eventType] || 0
    const suppress = now - last < CROSS_TAB_SUPPRESS_WINDOW_MS
    lastFired[eventType] = now
    localStorage.setItem(CROSS_TAB_STORAGE_KEY, JSON.stringify(lastFired))
    return suppress
  } catch {
    return false
  }
}

/**
 * Fire a native browser/OS notification for an event type.
 */
function fireNativeNotification(eventType, context) {
  if (!isNativeNotificationAvailable()) return
  if (Notification.permission !== 'granted') return
  if (shouldSuppressCrossTab(eventType)) return

  const title = NATIVE_TITLES[eventType] || 'Claude WebUI'
  const templateFn = TTS_TEMPLATES[eventType]
  const body = templateFn ? templateFn(context) : ''

  try {
    new Notification(title, { body, tag: eventType })
  } catch (e) {
    console.warn('Failed to fire native notification:', e)
  }
}

/**
 * Speak text via Web Speech API.
 */
function speak(text, voiceURI, rate) {
  if (!window.speechSynthesis) return

  // Truncate long text
  const truncated = text.length > 120 ? text.substring(0, 117) + '...' : text

  const utterance = new SpeechSynthesisUtterance(truncated)
  utterance.rate = rate || 1.0

  // Set voice if specified
  if (voiceURI) {
    const voices = window.speechSynthesis.getVoices()
    const voice = voices.find(v => v.voiceURI === voiceURI)
    if (voice) utterance.voice = voice
  }

  window.speechSynthesis.speak(utterance)
}

/**
 * Queue TTS and process sequentially.
 */
function queueTTS(text, voiceURI, rate) {
  ttsQueue.push({ text, voiceURI, rate })
  if (!ttsSpeaking) {
    processTTSQueue()
  }
}

function processTTSQueue() {
  if (ttsQueue.length === 0) {
    ttsSpeaking = false
    return
  }

  ttsSpeaking = true
  const { text, voiceURI, rate } = ttsQueue.shift()

  const utterance = new SpeechSynthesisUtterance(
    text.length > 120 ? text.substring(0, 117) + '...' : text
  )
  utterance.rate = rate || 1.0

  if (voiceURI) {
    const voices = window.speechSynthesis.getVoices()
    const voice = voices.find(v => v.voiceURI === voiceURI)
    if (voice) utterance.voice = voice
  }

  utterance.onend = () => processTTSQueue()
  utterance.onerror = () => processTTSQueue()

  window.speechSynthesis.speak(utterance)
}

/**
 * Main notification entry point.
 *
 * @param {string} eventType - One of: permission_prompt, task_complete, session_error, minion_comm
 * @param {object} context - Event-specific context (toolName, sessionName, etc.)
 */
export function notify(eventType, context = {}) {
  const settings = getSettings()

  const soundChannelActive = (settings.soundEnabled || settings.ttsEnabled) && settings.events[eventType]
  const nativeChannelActive = settings.nativeEnabled && settings.nativeEvents[eventType]
  const trayChannelActive = settings.trayEnabled && settings.trayEvents[eventType]

  // Early return if no channel applies to this event
  if (!soundChannelActive && !nativeChannelActive && !trayChannelActive) return

  // Debounce (shared between sound/TTS and native)
  if (shouldDebounce(eventType)) return

  if (soundChannelActive) {
    // Play sound
    if (settings.soundEnabled) {
      const gain = settings.volume / 100
      const toneFn = TONE_DEFS[eventType]
      if (toneFn) {
        try {
          toneFn(gain)
        } catch (e) {
          console.warn('Failed to play notification sound:', e)
        }
      }
    }

    // TTS
    if (settings.ttsEnabled && window.speechSynthesis) {
      const templateFn = TTS_TEMPLATES[eventType]
      if (templateFn) {
        const text = templateFn(context)
        queueTTS(text, settings.ttsVoice, settings.ttsSpeed)
      }
    }
  }

  // Native browser/OS notification channel (independent of sound/TTS)
  if (nativeChannelActive) {
    fireNativeNotification(eventType, context)
  }

  // Tray channel (independent of sound/TTS and native) — lazy import avoids a
  // static cycle with the Pinia store, which isn't needed by non-tray callers.
  if (trayChannelActive) {
    import('@/stores/notificationTray').then(({ useNotificationTrayStore }) => {
      useNotificationTrayStore().addEntry(eventType, context)
    }).catch(e => console.warn('Failed to add tray entry:', e))
  }
}

/**
 * Play a test sound for a specific event type.
 */
export function testSound(eventType = 'task_complete') {
  const settings = getSettings()
  const gain = settings.volume / 100
  const toneFn = TONE_DEFS[eventType]
  if (toneFn) {
    // Ensure AudioContext is created (user gesture context)
    getAudioContext()
    toneFn(gain)
  }
}

/**
 * Speak test text via TTS.
 */
export function testTTS(text = 'This is a test notification') {
  const settings = getSettings()
  speak(text, settings.ttsVoice, settings.ttsSpeed)
}

/**
 * Fire a test native notification. Only fires if permission is currently granted.
 */
export function testNativeNotification() {
  if (getNativePermission() !== 'granted') return
  try {
    new Notification('Claude WebUI', { body: 'This is a test notification', tag: 'test' })
  } catch (e) {
    console.warn('Failed to fire test native notification:', e)
  }
}

/**
 * Get available TTS voices.
 */
export function getVoices() {
  if (!window.speechSynthesis) return []
  return window.speechSynthesis.getVoices()
}

/**
 * Check if TTS is available in this browser.
 */
export function isTTSAvailable() {
  return typeof window !== 'undefined' && 'speechSynthesis' in window
}

/**
 * Composable wrapper (for Vue setup() usage).
 */
export function useNotifications() {
  return {
    notify,
    testSound,
    testTTS,
    getVoices,
    getSettings,
    updateSettings,
    isTTSAvailable,
    isNativeNotificationAvailable,
    getNativePermission,
    requestNativePermission,
    testNativeNotification,
    getEventLabel
  }
}
