/**
 * Shared composable for deterministic agent color assignment.
 * Hash-based color from slugified agent name — same name always gets the same color.
 */

const AGENT_COLORS = [
  { bg: 'var(--agent-color-0-bg)',  border: 'var(--agent-color-0-border)',  accent: 'var(--agent-color-0-accent)'  },
  { bg: 'var(--agent-color-1-bg)',  border: 'var(--agent-color-1-border)',  accent: 'var(--agent-color-1-accent)'  },
  { bg: 'var(--agent-color-2-bg)',  border: 'var(--agent-color-2-border)',  accent: 'var(--agent-color-2-accent)'  },
  { bg: 'var(--agent-color-3-bg)',  border: 'var(--agent-color-3-border)',  accent: 'var(--agent-color-3-accent)'  },
  { bg: 'var(--agent-color-4-bg)',  border: 'var(--agent-color-4-border)',  accent: 'var(--agent-color-4-accent)'  },
  { bg: 'var(--agent-color-5-bg)',  border: 'var(--agent-color-5-border)',  accent: 'var(--agent-color-5-accent)'  },
  { bg: 'var(--agent-color-6-bg)',  border: 'var(--agent-color-6-border)',  accent: 'var(--agent-color-6-accent)'  },
  { bg: 'var(--agent-color-7-bg)',  border: 'var(--agent-color-7-border)',  accent: 'var(--agent-color-7-accent)'  },
  { bg: 'var(--agent-color-8-bg)',  border: 'var(--agent-color-8-border)',  accent: 'var(--agent-color-8-accent)'  },
  { bg: 'var(--agent-color-9-bg)',  border: 'var(--agent-color-9-border)',  accent: 'var(--agent-color-9-accent)'  },
  { bg: 'var(--agent-color-10-bg)', border: 'var(--agent-color-10-border)', accent: 'var(--agent-color-10-accent)' },
  { bg: 'var(--agent-color-11-bg)', border: 'var(--agent-color-11-border)', accent: 'var(--agent-color-11-accent)' },
]

const SPECIAL_COLORS = {
  system: { bg: 'var(--agent-color-system-bg)', border: 'var(--agent-color-system-border)', accent: 'var(--agent-color-system-accent)' },
  user:   { bg: 'var(--agent-color-user-bg)',   border: 'var(--agent-color-user-border)',   accent: 'var(--agent-color-user-accent)'   },
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
    // Bit mixing to prevent clustering for similar names
    hash = hash ^ (hash >>> 16)
  }
  const index = Math.abs(hash) % AGENT_COLORS.length
  return AGENT_COLORS[index]
}
