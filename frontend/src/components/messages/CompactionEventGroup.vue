<template>
  <div class="message-row message-row-compaction" :key="accordionId">
    <div class="message-speaker">
      <span class="speaker-label">system</span>
    </div>
    <div class="message-content-column">
      <!-- Bootstrap Accordion -->
      <div class="accordion compaction-accordion" :id="accordionId">
        <div class="accordion-item">
          <h2 class="accordion-header" :id="headerId">
            <button
              class="accordion-button collapsed"
              type="button"
              :aria-expanded="false"
              :aria-controls="collapseId"
              @click="toggleCollapse"
            >
              <div class="d-flex align-items-center w-100">
                <span class="me-2">🗜️</span>
                <strong class="me-2">Context Compaction Event</strong>
                <span class="badge bg-warning text-dark ms-auto me-2">
                  {{ formatTokenCount(preTokens) }}
                </span>
                <small class="text-muted">{{ formattedTimestamp }}</small>
              </div>
            </button>
          </h2>
        <div
          ref="collapseElement"
          :id="collapseId"
          class="accordion-collapse collapse"
          :aria-labelledby="headerId"
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
  </div>
</template>

<script setup>
import { computed, ref, onMounted, onBeforeUnmount } from 'vue'
import { formatTimestamp } from '@/utils/time'
import { Collapse } from 'bootstrap'

const props = defineProps({
  // The messages in the compaction event (4 or 5):
  // Pattern 1 (4 messages):
  //   1. System status=compacting
  //   2. System status=null
  //   3. System compact_boundary
  //   4. User continuation message
  // Pattern 2 (5 messages - with init):
  //   1. System status=compacting
  //   2. System status=null
  //   3. System init (new session after compaction)
  //   4. System compact_boundary
  //   5. User continuation message
  messages: {
    type: Array,
    required: true,
    validator: (messages) => messages.length === 4 || messages.length === 5
  }
})

// Generate unique IDs for Bootstrap accordion
const accordionId = computed(() => {
  const timestamp = props.messages[0]?.timestamp || Date.now()
  return `accordion-compaction-${timestamp}`
})

const headerId = computed(() => `heading-${accordionId.value}`)
const collapseId = computed(() => `collapse-${accordionId.value}`)

// Ref for the collapse element
const collapseElement = ref(null)
let bsCollapse = null

// Initialize Bootstrap Collapse instance
onMounted(() => {
  if (collapseElement.value) {
    bsCollapse = new Collapse(collapseElement.value, {
      toggle: false // Don't auto-toggle on init
    })
  }
})

// Clean up Bootstrap instance
onBeforeUnmount(() => {
  if (bsCollapse) {
    bsCollapse.dispose()
  }
})

// Toggle collapse state
function toggleCollapse() {
  if (bsCollapse) {
    bsCollapse.toggle()
  }
}

// Determine if this is a 5-message pattern (with init)
const hasInitMessage = computed(() => {
  return props.messages.length === 5 &&
    props.messages[2]?.type === 'system' &&
    props.messages[2]?.metadata?.subtype === 'init'
})

// Extract compact metadata from the boundary message
// - Message 2 (index 2) if 4-message pattern
// - Message 3 (index 3) if 5-message pattern (with init)
const boundaryMessage = computed(() => {
  return hasInitMessage.value ? props.messages[3] : props.messages[2]
})

const compactMetadata = computed(() => {
  return boundaryMessage.value?.metadata?.init_data?.compact_metadata || {}
})

const preTokens = computed(() => {
  return compactMetadata.value.pre_tokens || 0
})

// Extract continuation content from the user message
// - Message 3 (index 3) if 4-message pattern
// - Message 4 (index 4) if 5-message pattern (with init)
const continuationMessage = computed(() => {
  return hasInitMessage.value ? props.messages[4] : props.messages[3]
})

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
/* Message row layout */
.message-row {
  display: flex;
  width: 100%;
  min-height: 1.2rem;
  padding: 0.2rem 0;
  line-height: 1.2rem;
  margin: 0;
}

.message-row-compaction {
  background-color: #fffbea; /* Light yellow */
  padding: 0.2rem 0;
  margin: 0;
}

.message-speaker {
  width: 8em;
  padding: 0 1rem;
  flex-shrink: 0;
  text-align: right;
  font-weight: 500;
  color: #495057;
}

.speaker-label {
  font-size: 0.9rem;
  text-transform: lowercase;
}

.message-content-column {
  flex: 1;
  padding: 0 1rem 0 0.5rem;
  overflow-wrap: break-word;
}

/* Compaction accordion styling */
.compaction-accordion {
  margin: 0;
}

.compaction-accordion .accordion-item {
  border: none;
  background-color: transparent;
}

/* Override Bootstrap accordion button styling for compaction event */
.compaction-accordion .accordion-button {
  font-size: 0.9rem;
  padding: 0;
  background-color: transparent;
  border: none;
  box-shadow: none;
}

.compaction-accordion .accordion-button:not(.collapsed) {
  background-color: transparent;
  color: inherit;
  box-shadow: none;
}

.compaction-accordion .accordion-button:focus {
  border: none;
  box-shadow: none;
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
