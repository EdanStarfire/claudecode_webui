<template>
  <Teleport to="body">
    <div v-if="visible" class="dirty-guard-backdrop" @click.self="emit('cancel')">
      <div class="dirty-guard-modal" role="dialog" aria-modal="true" aria-labelledby="dgm-title">
        <h2 id="dgm-title" class="dirty-guard-title">Unsaved changes</h2>
        <p class="dirty-guard-body">
          You have unsaved changes. What would you like to do?
        </p>
        <div class="dirty-guard-actions">
          <button class="dgm-btn dgm-btn--apply" @click="emit('apply')">Apply</button>
          <button class="dgm-btn dgm-btn--discard" @click="emit('discard')">Discard</button>
          <button class="dgm-btn dgm-btn--cancel" @click="emit('cancel')">Cancel</button>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<script setup>
defineProps({
  visible: {
    type: Boolean,
    default: false,
  },
})

const emit = defineEmits(['apply', 'discard', 'cancel'])
</script>

<style scoped>
.dirty-guard-backdrop {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.6);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 9999;
}

.dirty-guard-modal {
  background: #1e293b;
  border: 1px solid #334155;
  border-radius: 8px;
  padding: 24px;
  width: 380px;
  max-width: 90vw;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.5);
}

.dirty-guard-title {
  font-size: 16px;
  font-weight: 600;
  color: #f1f5f9;
  margin: 0 0 8px;
}

.dirty-guard-body {
  font-size: 14px;
  color: #94a3b8;
  margin: 0 0 20px;
  line-height: 1.5;
}

.dirty-guard-actions {
  display: flex;
  gap: 8px;
  justify-content: flex-end;
}

.dgm-btn {
  padding: 6px 16px;
  border-radius: 6px;
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  border: 1px solid transparent;
  transition: all 0.15s;
}

.dgm-btn--apply {
  background: #6366f1;
  color: #fff;
  border-color: #6366f1;
}

.dgm-btn--apply:hover {
  background: #4f46e5;
}

.dgm-btn--discard {
  background: transparent;
  color: #f87171;
  border-color: #ef4444;
}

.dgm-btn--discard:hover {
  background: #ef444420;
}

.dgm-btn--cancel {
  background: transparent;
  color: #94a3b8;
  border-color: #334155;
}

.dgm-btn--cancel:hover {
  background: #334155;
  color: #e2e8f0;
}
</style>
