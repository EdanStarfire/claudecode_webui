/**
 * Compute display flags from the rendering context.
 * Called once by the parent (ASP or PMT), passed flat to FieldSection/FieldRenderer.
 */
export function computeDisplayFlags(context) {
  // context: 'advanced-settings' | 'profile-manager'
  return {
    showBadges: context === 'advanced-settings',
    showIncludeToggle: context === 'profile-manager',
  }
}
