<template>
  <div class="compaction-event-group mb-3">
    <!-- Bootstrap Accordion -->
    <div class="accordion" :id="accordionId">
      <div class="accordion-item border-warning">
        <h2 class="accordion-header" :id="headerId">
          <button
            class="accordion-button collapsed bg-warning bg-opacity-10"
            type="button"
            data-bs-toggle="collapse"
            :data-bs-target="`#${collapseId}`"
            aria-expanded="false"
            :aria-controls="collapseId"
          >
            <div class="d-flex align-items-center w-100">
              <span class="me-2">🗜️</span>
              <strong class="text-warning me-2">Context Compaction Event</strong>
              <span class="badge bg-warning text-dark ms-auto me-2">
                {{ formatTokenCount(preTokens) }}
              </span>
              <small class="text-muted">{{ formattedTimestamp }}</small>
            </div>
          </button>
        </h2>
        <div
          :id="collapseId"
          class="accordion-collapse collapse"
          :aria-labelledby="headerId"
          :data-bs-parent="`#${accordionId}`"
        >
          <div class="accordion-body bg-light">
            <!-- Compaction Metadata -->
            <div class="alert alert-warning mb-3" role="alert">
              <h6 class="alert-heading mb-2">
                <span class="me-2">ℹ️</span>
                Compaction Details
              </h6>
              <div class="compaction-metadata">
                <div class="metadata-row">
                  <strong>Trigger:</strong>
                  <span>{{ compactMetadata.trigger || 'auto' }}</span>
                </div>
                <div class="metadata-row">
                  <strong>Pre-compaction tokens:</strong>
                  <span>{{ formatTokenCount(preTokens) }}</span>
                </div>
                <div v-if="compactMetadata.post_tokens" class="metadata-row">
                  <strong>Post-compaction tokens:</strong>
                  <span>{{ formatTokenCount(compactMetadata.post_tokens) }}</span>
                </div>
              </div>
            </div>

            <!-- Continuation Message Content -->
            <div class="continuation-content">
              <h6 class="text-muted mb-2">
                <span class="me-2">↩️</span>
                Continuation Message
              </h6>
              <div class="continuation-text border rounded p-3 bg-white">
                <pre class="mb-0">{{ continuationContent }}</pre>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { formatTimestamp } from '@/utils/time'

const props = defineProps({
  // The 4 messages in the compaction event:
  // 1. System status=compacting
  // 2. System status=null
  // 3. System compact_boundary
  // 4. User continuation message
  messages: {
    type: Array,
    required: true,
    validator: (messages) => messages.length === 4
  }
})

// Generate unique IDs for Bootstrap accordion
const accordionId = computed(() => {
  const timestamp = props.messages[0]?.timestamp || Date.now()
  return `accordion-compaction-${timestamp}`
})

const headerId = computed(() => `heading-${accordionId.value}`)
const collapseId = computed(() => `collapse-${accordionId.value}`)

// Extract compact metadata from the boundary message (message 3)
const boundaryMessage = computed(() => props.messages[2])

const compactMetadata = computed(() => {
  return boundaryMessage.value?.metadata?.init_data?.compact_metadata || {}
})

const preTokens = computed(() => {
  return compactMetadata.value.pre_tokens || 0
})

// Extract continuation content from the user message (message 4)
const continuationMessage = computed(() => props.messages[3])

const continuationContent = computed(() => {
  return continuationMessage.value?.content || ''
})

// Use timestamp from the start message (message 1)
const formattedTimestamp = computed(() => {
  const timestamp = props.messages[0]?.timestamp
  return timestamp ? formatTimestamp(timestamp) : ''
})

// Format token count with commas
function formatTokenCount(tokens) {
  if (!tokens) return '0 tokens'
  return `${tokens.toLocaleString()} tokens`
}
</script>

<style scoped>
.compaction-event-group {
  margin: 0.5rem 0;
}

/* Override Bootstrap accordion button styling for compaction event */
.accordion-button {
  font-size: 0.95rem;
  padding: 0.75rem 1rem;
}

.accordion-button:not(.collapsed) {
  background-color: rgba(255, 193, 7, 0.1) !important;
  color: inherit;
}

.accordion-button:focus {
  border-color: rgba(255, 193, 7, 0.5);
  box-shadow: 0 0 0 0.25rem rgba(255, 193, 7, 0.25);
}

/* Metadata display */
.compaction-metadata {
  font-size: 0.9rem;
}

.metadata-row {
  display: flex;
  justify-content: space-between;
  padding: 0.25rem 0;
}

.metadata-row strong {
  margin-right: 0.5rem;
}

/* Continuation message display */
.continuation-content {
  margin-top: 1rem;
}

.continuation-text {
  max-height: 400px;
  overflow-y: auto;
}

.continuation-text pre {
  white-space: pre-wrap;
  word-wrap: break-word;
  font-size: 0.85rem;
  line-height: 1.4;
  font-family: 'Courier New', monospace;
}

/* Mobile responsive */
@media (max-width: 768px) {
  .accordion-button {
    font-size: 0.85rem;
    padding: 0.5rem 0.75rem;
  }

  .metadata-row {
    flex-direction: column;
  }

  .continuation-text {
    max-height: 300px;
  }
}
</style>
