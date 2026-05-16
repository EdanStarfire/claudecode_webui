<template>
  <div class="providers-tab">

    <ProviderPendingBanner v-if="store.pendingChanges" />

    <ProviderStatusCard />

    <div class="d-flex justify-content-between align-items-center mb-2">
      <h6 class="mb-0">Catalog entries</h6>
      <button class="btn btn-primary btn-sm" @click="openCreate">+ Add Provider</button>
    </div>

    <div v-if="store.loading && !store.loaded" class="text-muted small py-3">Loading…</div>

    <div v-else-if="store.entries.length === 0" class="alert alert-secondary small mb-3">
      No provider entries configured. Add one to expose alternative LLM backends.
    </div>

    <div v-else class="table-responsive mb-3">
      <table class="table table-sm table-hover">
        <thead>
          <tr>
            <th>ID</th>
            <th>Display Name</th>
            <th>Type</th>
            <th>Models</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="entry in store.entries" :key="entry.id">
            <td class="font-monospace small align-middle">{{ entry.id }}</td>
            <td class="align-middle">{{ entry.display_name }}</td>
            <td class="align-middle">
              <span class="badge bg-secondary">{{ entry.provider_type }}</span>
            </td>
            <td class="align-middle">
              <span
                v-if="entry.models && entry.models.length > 0"
                class="small"
                :title="entry.models.map(m => m.display_name).join(', ')"
              >
                {{ entry.models.length }} model{{ entry.models.length !== 1 ? 's' : '' }}
              </span>
              <span v-else class="text-muted small">—</span>
            </td>
            <td class="text-end align-middle">
              <div class="btn-group btn-group-sm">
                <button class="btn btn-outline-secondary" @click="openEdit(entry)">Edit</button>
                <button class="btn btn-outline-danger" @click="confirmDelete(entry)">Delete</button>
              </div>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <ProviderEditModal
      v-if="modalOpen"
      :entry="editingEntry"
      :existing-ids="existingIds"
      @close="modalOpen = false"
      @saved="onSaved"
    />
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useProviderCatalogStore } from '@/stores/providerCatalog'
import ProviderPendingBanner from './providers/ProviderPendingBanner.vue'
import ProviderStatusCard from './providers/ProviderStatusCard.vue'
import ProviderEditModal from './ProviderEditModal.vue'

const store = useProviderCatalogStore()

const modalOpen = ref(false)
const editingEntry = ref(null)

const existingIds = computed(() =>
  store.entries.map(e => e.id)
)

function openCreate() {
  editingEntry.value = null
  modalOpen.value = true
}

function openEdit(entry) {
  editingEntry.value = entry
  modalOpen.value = true
}

async function confirmDelete(entry) {
  if (!confirm(`Delete provider "${entry.display_name}" (${entry.id})?`)) return
  try {
    await store.deleteEntry(entry.id)
  } catch (e) {
    alert(e?.data?.detail || e?.message || 'Delete failed')
  }
}

function onSaved() {
  modalOpen.value = false
}

onMounted(() => {
  store.fetchIfEmpty()
})
</script>
