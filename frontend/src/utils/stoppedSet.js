/**
 * sessionStorage helper for the Stop All / Resume stopped-session set.
 * Key format: webui:stopped-set:<projectId>
 */

const KEY = (projectId) => `webui:stopped-set:${projectId}`

export function getStoppedSet(projectId) {
  try {
    return JSON.parse(sessionStorage.getItem(KEY(projectId)) || '[]')
  } catch {
    return []
  }
}

export function setStoppedSet(projectId, ids) {
  if (!ids || ids.length === 0) {
    sessionStorage.removeItem(KEY(projectId))
  } else {
    sessionStorage.setItem(KEY(projectId), JSON.stringify(ids))
  }
}

export function addToStoppedSet(projectId, ids) {
  const existing = getStoppedSet(projectId)
  const merged = [...new Set([...existing, ...ids])]
  setStoppedSet(projectId, merged)
}

export function clearStoppedSet(projectId) {
  sessionStorage.removeItem(KEY(projectId))
}

export function pruneStoppedSet(projectId, validSessionIds) {
  const validSet = new Set(validSessionIds)
  const pruned = getStoppedSet(projectId).filter(id => validSet.has(id))
  setStoppedSet(projectId, pruned)
  return pruned
}
