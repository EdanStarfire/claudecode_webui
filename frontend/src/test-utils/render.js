import { mount } from '@vue/test-utils'
import { getQueriesForElement } from '@testing-library/dom'
import { createPinia } from 'pinia'
import { createRouter, createMemoryHistory } from 'vue-router'
import { afterEach } from 'vitest'

const noopComponent = { template: '<div />' }
const _mounted = []

// Cleanup after each test without calling @testing-library/vue's cleanup
// (which has a bug with VTU's attachTo onUnmount callback)
afterEach(() => {
  while (_mounted.length) {
    const { wrapper, container } = _mounted.pop()
    try { wrapper.unmount() } catch (_) { /* ignore */ }
    if (container.parentNode) container.parentNode.removeChild(container)
  }
})

export function renderWithStores(component, { props, slots, routes, initialRoute = '/', stubs } = {}) {
  const pinia = createPinia()
  const router = createRouter({
    history: createMemoryHistory(),
    routes: routes ?? [
      { path: '/', component: noopComponent },
      { path: '/session/:sessionId', component: noopComponent },
      { path: '/project/:projectId', component: noopComponent }
    ]
  })
  router.push(initialRoute)

  const container = document.createElement('div')
  document.body.appendChild(container)

  const wrapper = mount(component, {
    attachTo: container,
    global: {
      plugins: [pinia, router],
      stubs: stubs ?? {}
    },
    props,
    slots
  })

  _mounted.push({ wrapper, container })

  return {
    pinia,
    router,
    container,
    wrapper,
    emitted: name => wrapper.emitted(name),
    ...getQueriesForElement(document.body)
  }
}
