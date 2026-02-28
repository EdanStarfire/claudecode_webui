import { createRouter, createWebHashHistory } from 'vue-router'
import NoSessionSelected from '../components/session/NoSessionSelected.vue'
import SessionView from '../components/session/SessionView.vue'
import TimelineView from '../components/legion/TimelineView.vue'
import ProjectOverview from '../components/project/ProjectOverview.vue'

const routes = [
  {
    path: '/',
    name: 'home',
    component: NoSessionSelected
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
  {
    path: '/timeline/:legionId',
    name: 'timeline',
    component: TimelineView,
    props: true
  }
]

const router = createRouter({
  history: createWebHashHistory(),
  routes
})

export default router
