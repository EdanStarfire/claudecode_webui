import { createRouter, createWebHashHistory } from 'vue-router'
import { useSettingsStore } from '../stores/settings'
import NoSessionSelected from '../components/session/NoSessionSelected.vue'
import SessionView from '../components/session/SessionView.vue'
import ProjectOverview from '../components/project/ProjectOverview.vue'
import AuditView from '../components/audit/AuditView.vue'
import AnalyticsView from '../components/analytics/AnalyticsView.vue'
import SettingsLayout from '../components/settings/SettingsLayout.vue'
import ResourceStandaloneView from '../components/common/ResourceStandaloneView.vue'

const routes = [
  {
    path: '/',
    name: 'home',
    component: NoSessionSelected
  },
  {
    path: '/audit',
    name: 'audit',
    component: AuditView
  },
  {
    path: '/analytics',
    name: 'analytics',
    component: AnalyticsView
  },
  {
    path: '/resource-view',
    name: 'resource-view',
    component: ResourceStandaloneView
  },
  {
    path: '/project/:projectId',
    name: 'project',
    component: ProjectOverview,
    props: true
  },
  {
    path: '/session/:sessionId',
    name: 'session',
    component: SessionView,
    props: true
  },
  {
    path: '/session/:sessionId/archive/:archiveId',
    name: 'session-archive',
    component: SessionView,
    props: true
  },
  {
    path: '/archive/agent/:agentId/:archiveId',
    name: 'agent-archive',
    component: SessionView,
    props: route => ({
      sessionId: route.params.agentId,
      archiveId: route.params.archiveId,
      isDeletedAgent: true
    })
  },
  // ---- Settings routes (Phase 0: all mount placeholder SettingsLayout) ----
  {
    path: '/settings/session/:sessionId/:section?',
    name: 'settings-session',
    component: SettingsLayout,
    props: true
  },
  {
    path: '/settings/template/:templateId/:section?',
    name: 'settings-template',
    component: SettingsLayout,
    props: true
  },
  {
    path: '/settings/profile/:profileId/:section?',
    name: 'settings-profile',
    component: SettingsLayout,
    props: true
  },
  {
    path: '/settings/secret/:secretName/:section?',
    name: 'settings-secret',
    component: SettingsLayout,
    props: true
  },
  {
    path: '/settings/schedules',
    name: 'settings-schedules',
    component: SettingsLayout
  },
  {
    path: '/settings/schedule/:scheduleId/:section?',
    name: 'settings-schedule',
    component: SettingsLayout,
    props: true
  },
  {
    path: '/settings/templates',
    name: 'settings-templates',
    component: SettingsLayout
  },
  {
    path: '/settings/profiles',
    name: 'settings-profiles',
    component: SettingsLayout
  },
  {
    path: '/settings/secrets',
    name: 'settings-secrets',
    component: SettingsLayout
  },
  {
    path: '/settings/relays',
    name: 'settings-relays',
    component: SettingsLayout
  },
  {
    path: '/settings/features',
    name: 'settings-features',
    component: SettingsLayout
  },
  {
    path: '/settings/notifications',
    name: 'settings-notifications',
    component: SettingsLayout
  },
  {
    path: '/settings/read-aloud',
    name: 'settings-read-aloud',
    component: SettingsLayout
  },
  {
    path: '/settings/mcp-servers',
    name: 'settings-mcp-servers',
    component: SettingsLayout
  },
  {
    path: '/settings/hooks',
    name: 'settings-hooks',
    component: SettingsLayout
  },
  {
    path: '/settings/pricing',
    name: 'settings-pricing',
    component: SettingsLayout
  },
  {
    path: '/settings/providers',
    name: 'settings-providers',
    component: SettingsLayout
  },
]

const router = createRouter({
  history: createWebHashHistory(),
  routes
})

router.beforeEach((to, from, next) => {
  // A query-only change on the same path (e.g. Library > Relays `?tab=`) does not leave the editing
  // area, so unsaved edits are not at risk and the guard stays out of the way.
  if (from.name && to.path === from.path) {
    next()
    return
  }
  // useSettingsStore() is safe to call outside setup() once Pinia is initialized
  const settingsStore = useSettingsStore()
  const blocked = settingsStore.requestNavigation(to)
  if (blocked) {
    next(false)
  } else {
    next()
  }
})

export default router
