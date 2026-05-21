import { ref } from 'vue'

export const fullViewSource = ref(null)
export const fullViewDiagramId = ref(null)

export function openFullView(source, id) {
  fullViewSource.value = source
  fullViewDiagramId.value = id
}
