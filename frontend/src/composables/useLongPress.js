import { ref } from 'vue'

/**
 * Composable for touch-based long-press detection.
 *
 * @param {Function} callback - Called when long-press fires
 * @param {Object} options - { delay: 500, moveThreshold: 10 }
 * @returns {{ onTouchStart: Function, onTouchEnd: Function, onTouchCancel: Function, fired: import('vue').Ref<boolean> }}
 */
export function useLongPress(callback, options = {}) {
  const { delay = 500, moveThreshold = 10 } = options

  const fired = ref(false)
  let timer = null
  let startX = 0
  let startY = 0

  function clear() {
    if (timer !== null) {
      clearTimeout(timer)
      timer = null
    }
  }

  function onTouchStart(e) {
    fired.value = false
    const touch = e.touches[0]
    startX = touch.clientX
    startY = touch.clientY

    clear()
    timer = setTimeout(() => {
      fired.value = true
      timer = null
      callback()
    }, delay)
  }

  function onTouchMove(e) {
    if (timer === null) return
    const touch = e.touches[0]
    const dx = touch.clientX - startX
    const dy = touch.clientY - startY
    if (Math.sqrt(dx * dx + dy * dy) > moveThreshold) {
      clear()
    }
  }

  function onTouchEnd() {
    clear()
  }

  function onTouchCancel() {
    clear()
  }

  return { onTouchStart, onTouchMove, onTouchEnd, onTouchCancel, fired }
}
