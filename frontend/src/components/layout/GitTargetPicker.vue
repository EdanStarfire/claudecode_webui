<template>
  <div class="git-target-picker">
    <div v-if="state.loading" class="text-center py-2">
      <div class="spinner-border spinner-border-sm" role="status"></div>
      <span class="ms-2 small">Loading {{ label }} status...</span>
    </div>

    <div v-else-if="state.status" class="card bg-body-secondary mb-2">
      <div class="card-body py-2 px-3">
        <div class="small">
          <div><strong>{{ label }} branch:</strong> {{ state.status.branch }}</div>

          <!-- Remote commit info (primary) -->
          <template v-if="!state.status.remote_fetch_failed && state.status.remote_commit_hash">
            <div><strong>Latest on origin:</strong> {{ state.status.remote_commit_message }}</div>
            <div class="font-monospace text-muted" style="font-size: 0.75rem;">{{ state.status.remote_commit_hash?.substring(0, 12) }}</div>
            <div v-if="state.status.commits_behind > 0" class="text-info mt-1">
              {{ state.status.commits_behind }} commit{{ state.status.commits_behind !== 1 ? 's' : '' }} behind origin
            </div>
            <div v-else class="text-success mt-1">
              Already up to date
            </div>
          </template>

          <!-- Fallback: local commit when remote unavailable -->
          <template v-else>
            <div class="text-warning mt-1 mb-1" v-if="state.status.remote_fetch_failed">
              <small>Could not fetch remote info — showing local commit</small>
            </div>
            <div><strong>Last commit:</strong> {{ state.status.last_commit_message }}</div>
            <div class="font-monospace text-muted" style="font-size: 0.75rem;">{{ state.status.last_commit_hash?.substring(0, 12) }}</div>
          </template>

          <div v-if="state.status.has_uncommitted_changes" class="text-warning mt-1">
            <strong>Warning:</strong> Uncommitted changes detected. git pull may fail.
          </div>
        </div>
      </div>
    </div>

    <div v-else-if="state.error" class="alert alert-warning py-2 small">
      Could not fetch {{ label }} git status: {{ state.error }}
    </div>

    <div v-if="state.status" class="mb-2">
      <button
        type="button"
        class="btn btn-link btn-sm px-0 text-decoration-none"
        @click="$emit('toggle-picker')"
      >
        {{ state.showPicker ? `Hide ${label} branch/commit picker ▾` : `Change ${label} branch or commit ▾` }}
      </button>

      <div v-if="state.showPicker" class="border rounded p-2 mt-1">
        <div v-if="state.branchesLoading" class="text-center py-2">
          <div class="spinner-border spinner-border-sm" role="status"></div>
          <span class="ms-2 small">Loading branches...</span>
        </div>
        <template v-else>
          <div class="mb-2">
            <label class="form-label small mb-1">{{ label }} branch</label>
            <select class="form-select form-select-sm" v-model="state.selectedBranch">
              <option v-for="b in state.branches" :key="b.name" :value="b.name">
                {{ b.name }}{{ b.is_current ? ' (current)' : '' }}{{ b.is_remote_only ? ' [remote]' : '' }}
              </option>
            </select>
          </div>

          <div v-if="state.commitsLoading" class="text-center py-2">
            <div class="spinner-border spinner-border-sm" role="status"></div>
            <span class="ms-2 small">Loading commits...</span>
          </div>
          <template v-else>
            <label class="form-label small mb-1">Commit</label>
            <div class="list-group commit-list" style="max-height: 160px; overflow-y: auto;">
              <button
                type="button"
                class="list-group-item list-group-item-action py-1 px-2 small"
                :class="{ active: state.selectedCommitHash === null }"
                @click="state.selectedCommitHash = null"
              >
                <span class="font-monospace">(latest)</span>
              </button>
              <button
                v-for="c in state.commits"
                :key="c.hash"
                type="button"
                class="list-group-item list-group-item-action py-1 px-2 small"
                :class="{ active: state.selectedCommitHash === c.hash }"
                @click="state.selectedCommitHash = c.hash"
              >
                <span class="font-monospace">{{ c.short_hash }}</span> {{ c.subject }}
                <span class="text-muted">— {{ getRelativeTime(c.date) }}</span>
              </button>
            </div>
            <div v-if="state.commitsTruncated" class="text-muted small mt-1">
              Showing latest 50 commits
            </div>
          </template>

          <div v-if="state.hasCustomSelection && state.status?.has_uncommitted_changes" class="text-danger small mt-2">
            Resolve uncommitted changes before switching {{ label }} branch or commit.
          </div>
        </template>
      </div>
    </div>
  </div>
</template>

<script setup>
import { getRelativeTime } from '@/utils/time'

defineProps({
  label: { type: String, required: true },
  state: { type: Object, required: true }
})
defineEmits(['toggle-picker'])
</script>
