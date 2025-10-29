import { createRouter, createWebHashHistory } from 'vue-router'
import NoSessionSelected from '../components/session/NoSessionSelected.vue'
import SessionView from '../components/session/SessionView.vue'
import TimelineView from '../components/legion/TimelineView.vue'
import SpyView from '../components/legion/SpyView.vue'
import HordeView from '../components/legion/HordeView.vue'
import ChannelView from '../components/legion/ChannelView.vue'

const routes = [
  {
    path: '/',
    name: 'home',
    component: NoSessionSelected
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
  },
  {
    path: '/spy/:legionId/:minionId',
    name: 'spy',
    component: SpyView,
    props: true
  },
  {
    path: '/horde/:legionId',
    name: 'horde',
    component: HordeView,
    props: true
  },
  {
    path: '/channel/:legionId/:channelId',
    name: 'channel',
    component: ChannelView,
    props: true
  }
]

const router = createRouter({
  history: createWebHashHistory(),
  routes
})

export default router
