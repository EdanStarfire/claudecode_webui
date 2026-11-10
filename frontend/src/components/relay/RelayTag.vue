<template>
  <span class="relay-tag" :class="`relay-tag--${tone}`" :title="title || undefined"><slot /></span>
</template>

<script setup>
// Tiny quiet tag (SourceMarker style). `amber` is used only for states that need the owner.
defineProps({
  tone: { type: String, default: 'neutral', validator: v => ['muted', 'neutral', 'amber'].includes(v) },
  title: { type: String, default: '' },
})
</script>

<style scoped>
.relay-tag {
  display: inline-block; padding: 0 5px; border-radius: 3px; font-size: 10px; line-height: 16px;
  color: var(--bs-tertiary-color, #6b7280); border: 1px solid var(--bs-border-color, rgba(0,0,0,0.12));
  white-space: nowrap; user-select: none; vertical-align: baseline;
}
.relay-tag--muted { opacity: 0.8; }
.relay-tag--amber { color: #d29922; border-color: #d2992266; }
</style>
