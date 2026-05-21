/**
 * Time utility functions for handling timestamps from backend
 */

/**
 * Parse timestamp from backend (handles multiple formats)
 * Backend may send: Unix timestamp (seconds), ISO string, or milliseconds
 *
 * @param {number|string} timestamp - Timestamp from backend
 * @returns {Date} Parsed Date object
 */
export function parseTimestamp(timestamp) {
  if (!timestamp) {
    return new Date()
  }

  // If it's a string, try parsing as ISO date
  if (typeof timestamp === 'string') {
    const date = new Date(timestamp)
    return isNaN(date.getTime()) ? new Date() : date
  }

  // If it's a number, check if it's in seconds or milliseconds
  // Unix timestamps in seconds are < 10000000000 (Sep 2001)
  // Unix timestamps in milliseconds are > 10000000000
  if (typeof timestamp === 'number') {
    if (timestamp < 10000000000) {
      // It's in seconds, convert to milliseconds
      return new Date(timestamp * 1000)
    } else {
      // It's already in milliseconds
      return new Date(timestamp)
    }
  }

  // Fallback to current time
  return new Date()
}

/**
 * Format timestamp for display
 *
 * @param {number|string} timestamp - Timestamp from backend
 * @returns {string} Formatted time string
 */
export function formatTimestamp(timestamp) {
  const date = parseTimestamp(timestamp)
  return date.toLocaleTimeString()
}

/**
 * Format timestamp with date
 *
 * @param {number|string} timestamp - Timestamp from backend
 * @returns {string} Formatted date and time string
 */
export function formatFullTimestamp(timestamp) {
  const date = parseTimestamp(timestamp)
  return date.toLocaleString()
}

/**
 * Format timestamp as a calendar-date label using local timezone.
 * Returns "Today" / "Yesterday" for the last two days, otherwise
 * a long absolute date like "Thursday, May 1, 2026".
 */
export function formatDateSeparatorLabel(timestamp) {
  const date = parseTimestamp(timestamp)
  const today = new Date()
  const yesterday = new Date()
  yesterday.setDate(today.getDate() - 1)

  const sameLocalDate = (a, b) => a.toDateString() === b.toDateString()

  if (sameLocalDate(date, today)) return 'Today'
  if (sameLocalDate(date, yesterday)) return 'Yesterday'
  return date.toLocaleDateString(undefined, {
    weekday: 'long', year: 'numeric', month: 'long', day: 'numeric'
  })
}

/**
 * Format a queue item timestamp as "YYYY-MM-DD h:mma" in local time.
 * Hour has no leading zero; am/pm is lowercase.
 *
 * @param {number|string|null} timestamp - Timestamp from backend (Unix seconds or ISO)
 * @returns {string} Formatted string, or empty string for missing timestamp
 */
export function formatQueueTimestamp(timestamp) {
  if (!timestamp) return ''
  const date = parseTimestamp(timestamp)
  if (isNaN(date.getTime())) return ''

  const year = date.getFullYear()
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')
  const hours24 = date.getHours()
  const minutes = String(date.getMinutes()).padStart(2, '0')
  const period = hours24 < 12 ? 'am' : 'pm'
  const hours12 = hours24 % 12 || 12

  return `${year}-${month}-${day} ${hours12}:${minutes}${period}`
}

/**
 * Get relative time (e.g., "2 minutes ago")
 *
 * @param {number|string} timestamp - Timestamp from backend
 * @returns {string} Relative time string
 */
export function getRelativeTime(timestamp) {
  const date = parseTimestamp(timestamp)
  const now = new Date()
  const diffMs = now - date
  const diffSec = Math.floor(diffMs / 1000)
  const diffMin = Math.floor(diffSec / 60)
  const diffHour = Math.floor(diffMin / 60)
  const diffDay = Math.floor(diffHour / 24)

  if (diffSec < 60) return 'just now'
  if (diffMin < 60) return `${diffMin} minute${diffMin !== 1 ? 's' : ''} ago`
  if (diffHour < 24) return `${diffHour} hour${diffHour !== 1 ? 's' : ''} ago`
  if (diffDay < 7) return `${diffDay} day${diffDay !== 1 ? 's' : ''} ago`

  return formatFullTimestamp(timestamp)
}
