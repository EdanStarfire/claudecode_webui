import { ref, onMounted, onUnmounted } from 'vue'

/**
 * Composable for WebSocket connections
 * Provides a reusable WebSocket hook with auto-reconnect
 *
 * Note: For main UI/Session/Legion WebSockets, use the WebSocket store instead
 * This composable is for custom/one-off WebSocket needs
 */
export function useWebSocket(url, options = {}) {
  const socket = ref(null)
  const connected = ref(false)
  const retryCount = ref(0)
  const maxRetries = options.maxRetries || 5

  let reconnectTimeout = null

  function connect() {
    if (socket.value) return

    console.log(`[useWebSocket] Connecting to ${url}`)
    socket.value = new WebSocket(url)

    socket.value.onopen = () => {
      connected.value = true
      retryCount.value = 0
      console.log(`[useWebSocket] Connected to ${url}`)
      options.onOpen?.()
    }

    socket.value.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data)
        options.onMessage?.(data)
      } catch (error) {
        console.error('[useWebSocket] Failed to parse message:', error)
      }
    }

    socket.value.onclose = () => {
      connected.value = false
      socket.value = null
      console.log(`[useWebSocket] Disconnected from ${url}`)

      options.onClose?.()

      // Auto-reconnect if enabled and not exceeded max retries
      if (options.autoReconnect !== false && retryCount.value < maxRetries) {
        retryCount.value++
        const delay = Math.min(2000 * retryCount.value, 30000)
        console.log(`[useWebSocket] Reconnecting in ${delay}ms (attempt ${retryCount.value})`)
        reconnectTimeout = setTimeout(connect, delay)
      }
    }

    socket.value.onerror = (error) => {
      console.error(`[useWebSocket] Error on ${url}:`, error)
      options.onError?.(error)
    }
  }

  function disconnect() {
    if (reconnectTimeout) {
      clearTimeout(reconnectTimeout)
      reconnectTimeout = null
    }

    if (socket.value) {
      socket.value.close()
      socket.value = null
      connected.value = false
    }
  }

  function send(data) {
    if (connected.value && socket.value) {
      socket.value.send(JSON.stringify(data))
    } else {
      console.warn('[useWebSocket] Cannot send: not connected')
    }
  }

  // Auto-connect on mount if enabled
  if (options.autoConnect !== false) {
    onMounted(() => {
      connect()
    })
  }

  // Auto-disconnect on unmount
  onUnmounted(() => {
    disconnect()
  })

  return {
    socket,
    connected,
    retryCount,
    connect,
    disconnect,
    send
  }
}
