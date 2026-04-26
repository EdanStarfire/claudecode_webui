<template>
  <div class="analytics-filters d-flex flex-wrap gap-2 align-items-center">
    <!-- Preset range buttons -->
    <div class="btn-group btn-group-sm" role="group" aria-label="Time range">
      <button
        v-for="p in store.TIME_PRESETS"
        :key="p.label"
        class="btn"
        :class="filters.preset === p.label && !isCustom ? 'btn-primary' : 'btn-outline-secondary'"
        @click="store.setPreset(p.label)"
      >{{ p.label }}</button>
      <button
        class="btn"
        :class="isCustom ? 'btn-primary' : 'btn-outline-secondary'"
        @click="showCustom = !showCustom"
      >Custom</button>
    </div>

    <!-- Custom range inputs (shown inline when active) -->
    <template v-if="showCustom || isCustom">
      <input
        type="datetime-local"
        class="form-control form-control-sm"
        style="max-width: 200px;"
        :value="customSinceLocal"
        @change="onCustomChange"
        ref="sinceInput"
        aria-label="Start date/time"
      />
      <span class="text-muted small">–</span>
      <input
        type="datetime-local"
        class="form-control form-control-sm"
        style="max-width: 200px;"
        :value="customUntilLocal"
        @change="onCustomChange"
        ref="untilInput"
        aria-label="End date/time"
      />
    </template>

    <!-- Model multi-select -->
    <select
      v-if="store.availableModels.length > 0"
      class="form-select form-select-sm"
      style="max-width: 200px;"
      multiple
      aria-label="Filter by model"
      @change="onModelChange"
    >
      <option value="" disabled>Filter by model</option>
      <option
        v-for="m in store.availableModels"
        :key="m"
        :value="m"
        :selected="filters.models.includes(m)"
      >{{ m }}</option>
    </select>

    <!-- Session search -->
    <input
      type="search"
      class="form-control form-control-sm"
      style="max-width: 200px;"
      placeholder="Search sessions…"
      :value="filters.sessionSearch"
      @input="store.setSessionSearch($event.target.value)"
      aria-label="Search sessions"
    />

    <!-- Refresh button -->
    <button
      class="btn btn-sm btn-outline-secondary ms-auto"
      :disabled="store.loading"
      @click="store.refresh()"
      aria-label="Refresh"
    >{{ store.loading ? '…' : '↺' }}</button>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { useAnalyticsStore } from '@/stores/analytics'

const store = useAnalyticsStore()
const filters = computed(() => store.filters)
const isCustom = computed(() => filters.value.preset === 'custom')
const showCustom = ref(false)

const sinceInput = ref(null)
const untilInput = ref(null)

function toLocalDateTimeValue(unixSeconds) {
  if (!unixSeconds) return ''
  const d = new Date(unixSeconds * 1000)
  // datetime-local requires "YYYY-MM-DDTHH:MM" format in local time
  const pad = n => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`
}

const customSinceLocal = computed(() => toLocalDateTimeValue(filters.value.since))
const customUntilLocal = computed(() => toLocalDateTimeValue(filters.value.until))

function onCustomChange() {
  const sinceVal = sinceInput.value?.value
  const untilVal = untilInput.value?.value
  if (sinceVal && untilVal) {
    const since = Math.floor(new Date(sinceVal).getTime() / 1000)
    const until = Math.floor(new Date(untilVal).getTime() / 1000)
    if (since < until) {
      store.setCustomRange(since, until)
      store.refresh()
    }
  }
}

function onModelChange(e) {
  const selected = [...e.target.selectedOptions].map(o => o.value).filter(Boolean)
  store.setModelFilter(selected)
}
</script>

<style scoped>
.analytics-filters {
  padding: 8px 0;
}
</style>
