<template>
  <div class="modal-overlay" @click.self="$emit('close')">
    <div class="modal-content">
      <div class="modal-header">
        <h3>Create Schedule</h3>
        <button class="close-btn" @click="$emit('close')">&times;</button>
      </div>

      <form @submit.prevent="submit">
        <!-- Minion selector -->
        <div class="form-group">
          <label>Agent</label>
          <select v-model="form.minion_id" required>
            <option value="" disabled>Select an agent...</option>
            <option
              v-for="session in projectSessions"
              :key="session.session_id"
              :value="session.session_id"
            >{{ session.name || session.session_id.substring(0, 8) }}</option>
          </select>
        </div>

        <!-- Name -->
        <div class="form-group">
          <label>Schedule Name</label>
          <input v-model="form.name" type="text" placeholder="Daily status report" required />
        </div>

        <!-- Cron expression -->
        <div class="form-group">
          <label>Cron Expression</label>
          <input
            v-model="form.cron_expression"
            type="text"
            placeholder="0 8 * * 1-5"
            required
          />
          <div class="cron-preview" :class="{ error: cronError }">
            {{ cronPreview }}
          </div>
          <div class="cron-presets">
            <button type="button" @click="setCron('0 * * * *')">Every hour</button>
            <button type="button" @click="setCron('0 8 * * 1-5')">Weekdays 8am</button>
            <button type="button" @click="setCron('0 9 * * *')">Daily 9am</button>
            <button type="button" @click="setCron('0 0 * * 0')">Weekly Sun</button>
          </div>
        </div>

        <!-- Prompt -->
        <div class="form-group">
          <label>Prompt</label>
          <textarea
            v-model="form.prompt"
            rows="4"
            placeholder="What should the agent do when this schedule fires?"
            required
          ></textarea>
        </div>

        <!-- Reset session toggle -->
        <div class="form-group toggle-group">
          <label class="toggle-label">
            <input type="checkbox" v-model="form.reset_session" />
            <span>Reset session before each execution</span>
          </label>
          <div class="toggle-hint">When enabled, the agent's session is reset for a clean context each time the schedule fires.</div>
        </div>

        <!-- Error -->
        <div v-if="error" class="error-msg">{{ error }}</div>

        <!-- Actions -->
        <div class="modal-actions">
          <button type="button" class="btn-secondary" @click="$emit('close')">Cancel</button>
          <button type="submit" class="btn-primary" :disabled="submitting || cronError">
            {{ submitting ? 'Creating...' : 'Create Schedule' }}
          </button>
        </div>
      </form>
    </div>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'
import { useSessionStore } from '@/stores/session'
import { useScheduleStore } from '@/stores/schedule'
import cronstrue from 'cronstrue'

const props = defineProps({
  legionId: { type: String, required: true },
})

const emit = defineEmits(['close', 'created'])

const sessionStore = useSessionStore()
const scheduleStore = useScheduleStore()

const submitting = ref(false)
const error = ref('')

const form = ref({
  minion_id: '',
  name: '',
  cron_expression: '',
  prompt: '',
  reset_session: false,
})

const projectSessions = computed(() => {
  return sessionStore.sessionsInProject(props.legionId).value
})

const cronPreview = computed(() => {
  if (!form.value.cron_expression) return 'Enter a cron expression (min hour dom mon dow)'
  try {
    return cronstrue.toString(form.value.cron_expression)
  } catch {
    return 'Invalid cron expression'
  }
})

const cronError = computed(() => {
  if (!form.value.cron_expression) return false
  try {
    cronstrue.toString(form.value.cron_expression)
    return false
  } catch {
    return true
  }
})

function setCron(expr) {
  form.value.cron_expression = expr
}

async function submit() {
  if (submitting.value || cronError.value) return
  error.value = ''
  submitting.value = true

  try {
    const schedule = await scheduleStore.createSchedule(props.legionId, {
      minion_id: form.value.minion_id,
      name: form.value.name,
      cron_expression: form.value.cron_expression,
      prompt: form.value.prompt,
      reset_session: form.value.reset_session,
    })
    emit('created', schedule)
  } catch (e) {
    error.value = e.message || 'Failed to create schedule'
  } finally {
    submitting.value = false
  }
}
</script>

<style scoped>
.modal-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.4);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1050;
}

.modal-content {
  background: #fff;
  border-radius: 12px;
  width: 440px;
  max-width: 95vw;
  max-height: 85vh;
  overflow-y: auto;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
}

.modal-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 16px 20px;
  border-bottom: 1px solid #e2e8f0;
}

.modal-header h3 {
  margin: 0;
  font-size: 16px;
  color: #1e293b;
}

.close-btn {
  background: none;
  border: none;
  font-size: 20px;
  color: #94a3b8;
  cursor: pointer;
  padding: 0 4px;
}

form {
  padding: 16px 20px;
}

.form-group {
  margin-bottom: 14px;
}

.form-group label {
  display: block;
  font-size: 12px;
  font-weight: 600;
  color: #334155;
  margin-bottom: 4px;
}

.form-group input,
.form-group select,
.form-group textarea {
  width: 100%;
  font-size: 13px;
  padding: 8px 10px;
  border: 1px solid #e2e8f0;
  border-radius: 6px;
  background: #fff;
  color: #1e293b;
  box-sizing: border-box;
}

.form-group textarea {
  resize: vertical;
  font-family: inherit;
}

.cron-preview {
  font-size: 11px;
  color: #64748b;
  margin-top: 4px;
  padding: 4px 6px;
  background: #f1f5f9;
  border-radius: 4px;
}

.cron-preview.error {
  color: #dc2626;
  background: #fef2f2;
}

.cron-presets {
  display: flex;
  gap: 4px;
  flex-wrap: wrap;
  margin-top: 6px;
}

.cron-presets button {
  font-size: 10px;
  padding: 3px 8px;
  border: 1px solid #e2e8f0;
  border-radius: 12px;
  background: #f8fafc;
  color: #475569;
  cursor: pointer;
}

.cron-presets button:hover {
  background: #e2e8f0;
}

.toggle-group {
  margin-bottom: 14px;
}

.toggle-label {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  color: #334155;
  cursor: pointer;
}

.toggle-label input[type="checkbox"] {
  width: auto;
  margin: 0;
}

.toggle-hint {
  font-size: 11px;
  color: #94a3b8;
  margin-top: 2px;
  padding-left: 24px;
}

.error-msg {
  color: #dc2626;
  font-size: 12px;
  margin-bottom: 10px;
  padding: 6px 8px;
  background: #fef2f2;
  border-radius: 4px;
}

.modal-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  padding-top: 8px;
}

.btn-secondary {
  font-size: 13px;
  padding: 8px 16px;
  border: 1px solid #e2e8f0;
  border-radius: 6px;
  background: #fff;
  color: #475569;
  cursor: pointer;
}

.btn-primary {
  font-size: 13px;
  padding: 8px 16px;
  border: none;
  border-radius: 6px;
  background: #6366f1;
  color: #fff;
  cursor: pointer;
}

.btn-primary:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.btn-primary:hover:not(:disabled) {
  background: #4f46e5;
}
</style>
