import { defineComarkPlugin } from 'comark'
import { visit } from 'comark/utils'

// comark's own task-list plugin synthesizes checkbox nodes via its
// mdc_inline_component token path, which never sets the `$` prop that
// attribsToComarkAttrs() stamps on every node parsed from literal HTML text.
// That absence is what lets us tell a genuine synthesized checkbox apart from
// a hand-typed <input> forging the same class/type, which always carries `$`
// and must keep falling through to the security plugin's blockedTags removal.
function isTaskListCheckbox(node) {
  if (!Array.isArray(node) || node[0] !== 'input') return false
  const props = node[1] || {}
  if (props.$) return false
  if (props.type !== 'checkbox') return false
  const classes = typeof props.class === 'string' ? props.class.split(/\s+/) : []
  return classes.includes('task-list-item-checkbox')
}

export const taskListCheckboxPlugin = defineComarkPlugin(() => ({
  name: 'task-list-checkbox',
  post(state) {
    visit(
      state.tree,
      isTaskListCheckbox,
      (node) => {
        const checked = node[1][':checked'] === 'true'
        return ['span', {
          class: `task-list-checkbox${checked ? ' task-list-checkbox-checked' : ''}`,
          role: 'img',
          'aria-label': checked ? 'checked' : 'unchecked',
        }]
      }
    )
  },
}))
