import { createRouter, createWebHashHistory } from 'vue-router'
import { useSettingsStore } from '../stores/settings'
import NoSessionSelected from '../components/session/NoSessionSelected.vue'
import SessionView from '../components/session/SessionView.vue'
import ProjectOverview from '../components/project/ProjectOverview.vue'
import AuditView from '../components/audit/AuditView.vue'
import AnalyticsView from '../components/analytics/AnalyticsView.vue'
import SettingsLayout from '../components/settings/SettingsLayout.vue'

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

router.beforeEach((to, _from, next) => {
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
