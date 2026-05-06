export const AUDIT_EVENT_TYPES = [
  { value: 'tool_call',  label: 'Tools',     color: 'success'   },
  { value: 'permission', label: 'Perms',     color: 'info'      },
  { value: 'lifecycle',  label: 'Lifecycle', color: 'secondary' },
  { value: 'comm',       label: 'Comms',     color: 'primary'   },
  { value: 'watchdog',   label: 'Watchdog',  color: 'warning'   },
]

const BY_VALUE = Object.fromEntries(AUDIT_EVENT_TYPES.map(e => [e.value, e]))

export function getEventTypeBtnClass(value) {
  return BY_VALUE[value] ? `btn-${BY_VALUE[value].color}` : 'btn-outline-secondary'
}

export function getEventTypeBgClass(value) {
  return BY_VALUE[value] ? `bg-${BY_VALUE[value].color}` : 'bg-light'
}
