<template>
  <div class="spy-view d-flex flex-column overflow-hidden">
    <!--
      Spy mode is just viewing a single minion session.
      We reuse the SessionView component directly.
    -->
    <SessionView :session-id="minionId" :is-spy-mode="true" />
  </div>
</template>

<script setup>
import SessionView from '../session/SessionView.vue'

const props = defineProps({
  legionId: {
    type: String,
    required: true
  },
  minionId: {
    type: String,
    required: true
  }
})
</script>

<style scoped>
.spy-view {
  height: 100%;
  min-height: 0; /* Critical for flex children to shrink */
}
</style>
