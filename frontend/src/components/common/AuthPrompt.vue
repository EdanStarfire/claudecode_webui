<template>
  <div class="auth-overlay">
    <div class="auth-card">
      <h4>Authentication Required</h4>
      <p class="text-muted">Enter the auth token displayed in the server console.</p>
      <form @submit.prevent="handleSubmit">
        <div class="mb-3">
          <input
            ref="tokenInput"
            v-model="token"
            type="text"
            class="form-control"
            placeholder="Paste token here"
            :class="{ 'is-invalid': errorMessage }"
            autocomplete="off"
          />
          <div v-if="errorMessage" class="invalid-feedback">{{ errorMessage }}</div>
        </div>
        <button type="submit" class="btn btn-primary w-100" :disabled="submitting || !token.trim()">
          <span v-if="submitting" class="spinner-border spinner-border-sm me-1"></span>
          Authenticate
        </button>
      </form>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, nextTick } from 'vue'
import { apiGet, setAuthToken } from '@/utils/api'

const emit = defineEmits(['authenticated'])

const token = ref('')
const errorMessage = ref('')
const submitting = ref(false)
const tokenInput = ref(null)

onMounted(() => {
  nextTick(() => {
    tokenInput.value?.focus()
  })
})

async function handleSubmit() {
  errorMessage.value = ''
  submitting.value = true
  try {
    setAuthToken(token.value.trim())
    const result = await apiGet('/api/auth/check')
    if (result.authenticated) {
      emit('authenticated')
    } else {
      errorMessage.value = 'Invalid token'
      setAuthToken('')
    }
  } catch {
    errorMessage.value = 'Failed to verify token'
    setAuthToken('')
  } finally {
    submitting.value = false
  }
}
</script>

<style scoped>
.auth-overlay {
  position: fixed;
  inset: 0;
  z-index: 9999;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(0, 0, 0, 0.6);
}

.auth-card {
  background: #fff;
  border-radius: 8px;
  padding: 2rem;
  width: 100%;
  max-width: 400px;
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.3);
}

.auth-card h4 {
  margin-bottom: 0.5rem;
}
</style>
