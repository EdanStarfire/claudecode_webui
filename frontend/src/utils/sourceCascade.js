/**
 * Resolves the source of a field value in the S→T→P→EMPTY cascade.
 *
 * @param {string} fieldKey - The field to resolve
 * @param {Object} context
 * @param {Object|null} context.sessionValue - Value explicitly set on this object (null/undefined = not set)
 * @param {Object|null} context.templateValue - Value from the bound template (null/undefined = not set)
 * @param {string|null} context.templateName - Bound template display name (null = no template)
 * @param {Object|null} context.profileValue - Value from the bound profile (null/undefined = not set)
 * @param {string|null} context.profileName - Bound profile display name (null = no profile)
 * @returns {{ kind: 'S'|'T'|'P'|'EMPTY', templateName?: string, profileName?: string }}
 */
export function resolveFieldSource(fieldKey, context) {
  const { sessionValue, templateValue, templateName, profileValue, profileName } = context

  if (_hasValue(sessionValue)) {
    return { kind: 'S' }
  }

  if (_hasValue(templateValue) && templateName) {
    return { kind: 'T', templateName }
  }

  if (_hasValue(profileValue) && profileName) {
    return { kind: 'P', profileName }
  }

  return { kind: 'EMPTY' }
}

function _hasValue(v) {
  return v !== null && v !== undefined && v !== ''
}
