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
