/**
 * Shared composable for deterministic agent color assignment.
 * Hash-based color from slugified agent name — same name always gets the same color.
 */

const AGENT_COLORS = [
  { bg: '#fef3c7', border: '#f59e0b', accent: '#d97706' },  // amber
  { bg: '#fce7f3', border: '#ec4899', accent: '#db2777' },  // pink
  { bg: '#e0e7ff', border: '#6366f1', accent: '#4f46e5' },  // indigo
  { bg: '#d1fae5', border: '#10b981', accent: '#059669' },  // emerald
  { bg: '#ede9fe', border: '#8b5cf6', accent: '#7c3aed' },  // violet
  { bg: '#fee2e2', border: '#ef4444', accent: '#dc2626' },  // red
  { bg: '#cffafe', border: '#06b6d4', accent: '#0891b2' },  // cyan
  { bg: '#fef9c3', border: '#eab308', accent: '#ca8a04' },  // yellow
  { bg: '#e0f2fe', border: '#0ea5e9', accent: '#0284c7' },  // sky
  { bg: '#f3e8ff', border: '#a855f7', accent: '#9333ea' },  // purple
  { bg: '#ffedd5', border: '#f97316', accent: '#ea580c' },  // orange
  { bg: '#dcfce7', border: '#22c55e', accent: '#16a34a' },  // green
]

const SPECIAL_COLORS = {
  system: { bg: '#f1f5f9', border: '#e2e8f0', accent: '#94a3b8' },
  user: { bg: '#eef2ff', border: '#e0e7ff', accent: '#6366f1' },
}

export function slugifyAgentName(name) {
  if (!name) return 'unknown'
  return name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') || 'unknown'
}

export function getAgentColor(nameSlug) {
  if (!nameSlug) return AGENT_COLORS[0]
  if (SPECIAL_COLORS[nameSlug]) return SPECIAL_COLORS[nameSlug]

  // Hash the slug to a palette index
  let hash = 0
  for (let i = 0; i < nameSlug.length; i++) {
    hash = ((hash << 5) - hash + nameSlug.charCodeAt(i)) | 0
  }
  const index = Math.abs(hash) % AGENT_COLORS.length
  return AGENT_COLORS[index]
}
