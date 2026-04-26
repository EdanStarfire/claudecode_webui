import { createRouter, createWebHashHistory } from 'vue-router'
import NoSessionSelected from '../components/session/NoSessionSelected.vue'
import SessionView from '../components/session/SessionView.vue'
import ProjectOverview from '../components/project/ProjectOverview.vue'
import AuditView from '../components/audit/AuditView.vue'
import AnalyticsView from '../components/analytics/AnalyticsView.vue'

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
  }
]

const router = createRouter({
  history: createWebHashHistory(),
  routes
})

export default router
