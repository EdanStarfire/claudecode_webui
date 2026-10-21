<template>
  <div class="project-overview d-flex flex-column h-100">
    <!-- Header -->
    <div class="project-overview-header border-bottom p-3">
      <div class="d-flex align-items-center justify-content-between">
        <div>
          <h4 class="mb-1">
            <span v-if="hasMinions" class="me-2">🏛</span>
            {{ project?.name || 'Project Overview' }}
          </h4>
          <small class="text-muted font-monospace">{{ project?.working_directory }}</small>
        </div>
        <div class="d-flex align-items-center gap-2 flex-wrap">
          <button
            class="btn btn-sm btn-outline-secondary"
            title="Edit project"
            aria-label="Edit project"
            @click="showEditModal"
          >
            ✏️ Edit
          </button>
          <button
            class="btn btn-sm btn-outline-primary"
            :title="hasMinions ? 'Create minion' : 'Add session'"
            :aria-label="hasMinions ? 'Add minion' : 'Add session'"
            @click="showCreateModal"
          >
            ➕ {{ hasMinions ? 'Add Minion' : 'Add Session' }}
          </button>

          <span class="vr mx-1"></span>

          <!-- Stop All: default state -->
          <button
            v-if="stopState === 'default'"
            class="btn btn-sm btn-outline-danger"
            :disabled="projectSessions.length === 0"
            :title="projectSessions.length === 0 ? 'No sessions to stop' : 'Stop all sessions'"
            @click="beginStopConfirm"
          >
            ⏹ Stop All
          </button>

          <!-- Stop All: inline confirmation (no auto-cancel timer) -->
          <div
            v-else-if="stopState === 'confirming'"
            class="d-inline-flex align-items-center gap-1 border border-danger rounded px-2 py-1"
            style="background: rgba(var(--bs-danger-rgb), 0.12);"
          >
            <span class="text-danger small">⚠️ Stop {{ projectSessions.length }} session{{ projectSessions.length !== 1 ? 's' : '' }}?</span>
            <button class="btn btn-sm btn-outline-secondary py-0" @click="cancelStop">Cancel</button>
            <button class="btn btn-sm btn-danger py-0" @click="confirmStop">Confirm Stop All</button>
          </div>

          <!-- Stop All: stopping in progress -->
          <button
            v-else-if="stopState === 'stopping'"
            class="btn btn-sm btn-danger"
            disabled
          >
            <span class="spinner-border spinner-border-sm me-1" role="status" aria-hidden="true"></span>
            Stopping…
          </button>

          <!-- Resume Sessions: default state -->
          <button
            v-if="resumeState === 'default'"
            class="btn btn-sm btn-outline-warning"
            :disabled="stoppedCount === 0 || stopState === 'stopping'"
            :title="stoppedCount === 0 ? 'No stopped sessions to resume' : `Resume ${stoppedCount} stopped session${stoppedCount !== 1 ? 's' : ''}`"
            @click="beginResumeConfirm"
          >
            ↻ Resume Sessions
            <span
              v-if="stoppedCount > 0"
              class="badge bg-warning text-dark ms-1"
            >{{ stoppedCount }}</span>
          </button>

          <!-- Resume Sessions: inline confirmation with editable batch size -->
          <div
            v-else-if="resumeState === 'confirming'"
            class="d-inline-flex align-items-center gap-1 border border-warning rounded px-2 py-1"
            style="background: rgba(var(--bs-warning-rgb), 0.12);"
          >
            <span class="text-warning-emphasis small">Resume {{ stoppedCount }} session{{ stoppedCount !== 1 ? 's' : '' }} in batches of</span>
            <input
              type="number"
              class="form-control form-control-sm py-0"
              style="width: 4.5rem;"
              min="1"
              step="1"
              v-model.number="resumeBatchSizeInput"
              @click.stop
              aria-label="Resume batch size"
            >
            <span class="text-warning-emphasis small">delay</span>
            <input
              type="number"
              class="form-control form-control-sm py-0"
              style="width: 4.5rem;"
              min="0"
              step="1"
              v-model.number="resumeBatchDelayInput"
              @click.stop
              aria-label="Resume batch delay in seconds"
            >
            <span class="text-warning-emphasis small">s</span>
            <button class="btn btn-sm btn-outline-secondary py-0" @click="cancelResume">Cancel</button>
            <button class="btn btn-sm btn-warning py-0" @click="confirmResume">Confirm Resume</button>
          </div>

          <!-- Resume Sessions: resuming in progress -->
          <button
            v-else
            class="btn btn-sm btn-warning"
            disabled
          >
            <span class="spinner-border spinner-border-sm me-1" role="status" aria-hidden="true"></span>
            Resuming…
          </button>

          <!-- Issue #1934: Batch selection controls — only shown while a selection is active -->
          <template v-if="selectedCount > 0">
            <span class="vr mx-1"></span>
            <span class="badge bg-primary">{{ selectedCount }} selected</span>
            <button class="btn btn-sm btn-outline-secondary" @click="uiStore.clearSessionSelection()">Clear</button>

            <!-- Stop Selected: default state -->
            <button
              v-if="stopSelectedState === 'default'"
              class="btn btn-sm btn-outline-danger"
              :disabled="stoppableSelectedCount === 0"
              :title="stoppableSelectedCount === 0 ? 'No selected sessions to stop' : `Stop ${stoppableSelectedCount} selected session${stoppableSelectedCount !== 1 ? 's' : ''}`"
              @click="beginStopSelectedConfirm"
            >
              ⏹ Stop Selected
              <span v-if="stoppableSelectedCount > 0" class="badge bg-light text-danger ms-1">{{ stoppableSelectedCount }}</span>
            </button>

            <!-- Stop Selected: inline confirmation -->
            <div
              v-else-if="stopSelectedState === 'confirming'"
              class="d-inline-flex align-items-center gap-1 border border-danger rounded px-2 py-1"
              style="background: rgba(var(--bs-danger-rgb), 0.12);"
            >
              <span class="text-danger small">⚠️ Stop {{ stoppableSelectedCount }} selected session{{ stoppableSelectedCount !== 1 ? 's' : '' }}?</span>
              <button class="btn btn-sm btn-outline-secondary py-0" @click="cancelStopSelected">Cancel</button>
              <button class="btn btn-sm btn-danger py-0" @click="confirmStopSelected">Confirm Stop Selected</button>
            </div>

            <!-- Stop Selected: stopping in progress -->
            <button
              v-else
              class="btn btn-sm btn-danger"
              disabled
            >
              <span class="spinner-border spinner-border-sm me-1" role="status" aria-hidden="true"></span>
              Stopping…
            </button>

            <!-- Start Selected: default state -->
            <button
              v-if="startSelectedState === 'default'"
              class="btn btn-sm btn-outline-warning"
              :disabled="startableSelectedCount === 0"
              :title="startableSelectedCount === 0 ? 'No selected sessions to start' : `Start ${startableSelectedCount} selected session${startableSelectedCount !== 1 ? 's' : ''}`"
              @click="beginStartSelectedConfirm"
            >
              ↻ Start Selected
              <span v-if="startableSelectedCount > 0" class="badge bg-light text-warning-emphasis ms-1">{{ startableSelectedCount }}</span>
            </button>

            <!-- Start Selected: inline confirmation with editable batch size -->
            <div
              v-else-if="startSelectedState === 'confirming'"
              class="d-inline-flex align-items-center gap-1 border border-warning rounded px-2 py-1"
              style="background: rgba(var(--bs-warning-rgb), 0.12);"
            >
              <span class="text-warning-emphasis small">Start {{ startableSelectedCount }} session{{ startableSelectedCount !== 1 ? 's' : '' }} in batches of</span>
              <input
                type="number"
                class="form-control form-control-sm py-0"
                style="width: 4.5rem;"
                min="1"
                step="1"
                v-model.number="startSelectedBatchSizeInput"
                @click.stop
                aria-label="Start selected batch size"
              >
              <span class="text-warning-emphasis small">delay</span>
              <input
                type="number"
                class="form-control form-control-sm py-0"
                style="width: 4.5rem;"
                min="0"
                step="1"
                v-model.number="startSelectedBatchDelayInput"
                @click.stop
                aria-label="Start selected batch delay in seconds"
              >
              <span class="text-warning-emphasis small">s</span>
              <button class="btn btn-sm btn-outline-secondary py-0" @click="cancelStartSelected">Cancel</button>
              <button class="btn btn-sm btn-warning py-0" @click="confirmStartSelected">Confirm Start Selected</button>
            </div>

            <!-- Start Selected: starting in progress -->
            <button
              v-else
              class="btn btn-sm btn-warning"
              disabled
            >
              <span class="spinner-border spinner-border-sm me-1" role="status" aria-hidden="true"></span>
              Starting…
            </button>
          </template>
        </div>
      </div>
    </div>

    <!-- Fleet operation toast -->
    <div
      v-if="fleetToast"
      class="alert mb-0 py-2 px-3 rounded-0 border-start-0 border-end-0"
      :class="`alert-${fleetToast.type}`"
      role="alert"
    >
      {{ fleetToast.message }}
    </div>

    <!-- Content area -->
    <div class="project-overview-content flex-grow-1 overflow-auto p-3">
      <!-- Loading state -->
      <div v-if="loading" class="text-center py-5">
        <div class="spinner-border" role="status">
          <span class="visually-hidden">Loading...</span>
        </div>
        <p class="text-muted mt-2">Loading project data...</p>
      </div>

      <!-- Error state -->
      <div v-else-if="error" class="alert alert-danger">
        {{ error }}
      </div>

      <!-- Project not found -->
      <div v-else-if="!project" class="text-center py-5">
        <h5 class="text-muted">Project not found</h5>
        <p class="text-secondary">The requested project does not exist.</p>
      </div>

      <!-- Project content -->
      <div v-else>
        <!-- Quick Stats Row -->
        <div class="row g-3 mb-4">
          <div class="col-md-4">
            <div class="card h-100">
              <div class="card-body">
                <h6 class="card-subtitle mb-2 text-muted">Sessions</h6>
                <h3 class="card-title mb-0">{{ sessionCount }}</h3>
                <small class="text-muted">{{ activeSessionCount }} active</small>
              </div>
            </div>
          </div>
          <div class="col-md-4">
            <div class="card h-100">
              <div class="card-body">
                <h6 class="card-subtitle mb-2 text-muted">Status</h6>
                <h3 class="card-title mb-0">
                  <span :class="overallStatusClass">{{ overallStatus }}</span>
                </h3>
                <small class="text-muted">{{ statusDescription }}</small>
              </div>
            </div>
          </div>
        </div>

        <!-- Minion Hierarchy (two-column layout) -->
        <div class="card mb-4">
          <div class="card-header d-flex align-items-center justify-content-between">
            <h5 class="mb-0">🌳 {{ hasMinions ? 'Minion Hierarchy' : 'Sessions' }}</h5>
            <span class="badge bg-secondary">{{ projectSessions.length }}</span>
          </div>
          <div class="card-body">
            <!-- Control row: view mode toggle + sort buttons + group-by-state -->
            <div class="d-flex flex-wrap justify-content-end gap-2 mb-2 me-4">
              <!-- View mode toggle -->
              <div class="btn-group btn-group-sm" role="group" aria-label="View mode">
                <button
                  type="button"
                  class="btn"
                  :class="uiStore.projectViewMode === 'hierarchy' ? 'btn-primary' : 'btn-outline-secondary'"
                  @click="uiStore.setProjectViewMode('hierarchy')"
                  title="Hierarchy view"
                >🌳 Hierarchy</button>
                <button
                  type="button"
                  class="btn"
                  :class="uiStore.projectViewMode === 'flat' ? 'btn-primary' : 'btn-outline-secondary'"
                  @click="uiStore.setProjectViewMode('flat')"
                  title="Flat list view"
                >☰ Flat</button>
              </div>

              <!-- Row density toggle (issue #2061) -->
              <div class="btn-group btn-group-sm" role="group" aria-label="Row density">
                <button
                  type="button"
                  class="btn"
                  :class="!uiStore.compactMinionTreeRows ? 'btn-primary' : 'btn-outline-secondary'"
                  @click="uiStore.setCompactMinionTreeRows(false)"
                  title="Normal row height"
                >▦ Normal</button>
                <button
                  type="button"
                  class="btn"
                  :class="uiStore.compactMinionTreeRows ? 'btn-primary' : 'btn-outline-secondary'"
                  @click="uiStore.setCompactMinionTreeRows(true)"
                  title="Compact single-line rows"
                >☰ Compact</button>
              </div>

              <!-- Hierarchy sort toggle (visible only in hierarchy mode) -->
              <template v-if="uiStore.projectViewMode === 'hierarchy'">
                <div
                  class="btn-group btn-group-sm"
                  role="group"
                  aria-label="Hierarchy sort order"
                >
                  <button
                    type="button"
                    class="btn"
                    :class="uiStore.agentSort === 'alpha' ? 'btn-primary' : 'btn-outline-secondary'"
                    @click="uiStore.setAgentSort('alpha')"
                    title="Sort alphabetically"
                  >A–Z</button>
                  <button
                    type="button"
                    class="btn"
                    :class="uiStore.agentSort === 'creation' ? 'btn-primary' : 'btn-outline-secondary'"
                    @click="uiStore.setAgentSort('creation')"
                    title="Sort by creation order"
                  >Created</button>
                  <button
                    type="button"
                    class="btn"
                    :class="uiStore.agentSort === 'last_active' ? 'btn-primary' : 'btn-outline-secondary'"
                    @click="uiStore.setAgentSort('last_active')"
                    title="Sort by most recent completion"
                  >Last Active</button>
                </div>
                <button
                  type="button"
                  class="btn btn-sm btn-outline-secondary"
                  @click="uiStore.setAgentSortDirection(uiStore.agentSortDirection === 'asc' ? 'desc' : 'asc')"
                  :title="uiStore.agentSortDirection === 'asc' ? 'Ascending — click to reverse' : 'Descending — click to reverse'"
                >{{ uiStore.agentSortDirection === 'asc' ? '↑' : '↓' }}</button>
              </template>

              <!-- Flat sort toggle (visible only in flat mode) -->
              <template v-else>
                <div
                  class="btn-group btn-group-sm"
                  role="group"
                  aria-label="Flat list sort order"
                >
                  <button
                    type="button"
                    class="btn"
                    :class="uiStore.flatSort === 'alpha' ? 'btn-primary' : 'btn-outline-secondary'"
                    @click="uiStore.setFlatSort('alpha')"
                    title="Sort alphabetically"
                  >A–Z</button>
                  <button
                    type="button"
                    class="btn"
                    :class="uiStore.flatSort === 'creation' ? 'btn-primary' : 'btn-outline-secondary'"
                    @click="uiStore.setFlatSort('creation')"
                    title="Sort by creation order"
                  >Created</button>
                  <button
                    type="button"
                    class="btn"
                    :class="uiStore.flatSort === 'last_active' ? 'btn-primary' : 'btn-outline-secondary'"
                    @click="uiStore.setFlatSort('last_active')"
                    title="Sort by most recent completion"
                  >Last Active</button>
                </div>
                <button
                  type="button"
                  class="btn btn-sm btn-outline-secondary"
                  @click="uiStore.setFlatSortDirection(uiStore.flatSortDirection === 'asc' ? 'desc' : 'asc')"
                  :title="uiStore.flatSortDirection === 'asc' ? 'Ascending — click to reverse' : 'Descending — click to reverse'"
                >{{ uiStore.flatSortDirection === 'asc' ? '↑' : '↓' }}</button>
              </template>

              <!-- Grouping mode (flat mode only) -->
              <div
                v-if="uiStore.projectViewMode === 'flat'"
                class="btn-group btn-group-sm ms-2"
                role="group"
                aria-label="Grouping mode"
              >
                <button
                  type="button"
                  class="btn"
                  :class="uiStore.flatGroupMode === 'none' ? 'btn-primary' : 'btn-outline-secondary'"
                  @click="uiStore.setFlatGroupMode('none')"
                  title="Ungrouped"
                >Ungrouped</button>
                <button
                  type="button"
                  class="btn"
                  :class="uiStore.flatGroupMode === 'status' ? 'btn-primary' : 'btn-outline-secondary'"
                  @click="uiStore.setFlatGroupMode('status')"
                  title="Group by status"
                >By Status</button>
                <button
                  type="button"
                  class="btn"
                  :class="uiStore.flatGroupMode === 'custom' ? 'btn-primary' : 'btn-outline-secondary'"
                  @click="uiStore.setFlatGroupMode('custom')"
                  title="Group by custom priority groups"
                >Custom</button>
              </div>
            </div>

            <!-- Filter bar (issue #2050: applies to both Hierarchy and Flat view) -->
            <div class="filter-bar d-flex flex-wrap align-items-center gap-2 mb-2">
              <input
                type="search"
                class="form-control form-control-sm"
                style="max-width: 220px;"
                placeholder="Search by name or role..."
                v-model="filterSearchQuery"
                aria-label="Search sessions by name or role"
              >

              <div class="d-flex flex-wrap gap-1" role="group" aria-label="Filter by status">
                <button
                  v-for="g in STATE_GROUPS"
                  :key="g.key"
                  type="button"
                  class="btn btn-sm"
                  :class="filterStatuses.has(g.key) ? 'btn-primary' : 'btn-outline-secondary'"
                  @click="toggleFilterStatus(g.key)"
                >{{ g.label }}</button>
              </div>

              <select
                multiple
                class="form-select form-select-sm"
                style="max-width: 200px; height: 2rem;"
                aria-label="Filter by custom group"
                v-model="filterGroupIdsArray"
              >
                <option value="unassigned">Unassigned</option>
                <option v-for="group in project.kanban_groups || []" :key="group.group_id" :value="group.group_id">
                  {{ group.name }}
                </option>
              </select>

              <button v-if="hasActiveFilters" type="button" class="btn btn-sm btn-outline-secondary" @click="resetFilters">
                Clear filters
              </button>

              <!-- Flat-specific: filteredFlatNodes/sortedFlatNodes count exact matches, which
                   isn't the same quantity as "nodes shown" in Hierarchy view (dimmed ancestors
                   are shown too, without being matches) -->
              <small v-if="hasActiveFilters && uiStore.projectViewMode === 'flat'" class="text-muted">
                {{ filteredFlatNodes.length }} of {{ sortedFlatNodes.length }} sessions shown
              </small>
            </div>

            <!-- Hierarchy view -->
            <template v-if="uiStore.projectViewMode === 'hierarchy'">
              <!-- Loading hierarchy -->
              <div v-if="loadingHierarchy" class="text-center py-4">
                <div class="spinner-border spinner-border-sm" role="status">
                  <span class="visually-hidden">Loading...</span>
                </div>
                <p class="text-muted mt-2 mb-0">Loading hierarchy...</p>
              </div>

              <!-- Empty state -->
              <div v-else-if="!minionHierarchy || !minionHierarchy.children || minionHierarchy.children.length === 0" class="text-center text-muted py-4">
                No sessions yet. Click "Add Session" to create one.
              </div>

              <!-- Filtered-to-zero state (issue #2050, Stage B) -->
              <div v-else-if="hasActiveFilters && filteredHierarchyChildren.length === 0" class="text-center text-muted py-4">
                No sessions match the active filters.
              </div>

              <!-- Hierarchy tree -->
              <div v-else class="hierarchy-tree">
                <!-- Root Minions (user-spawned) -->
                <div class="root-minions">
                  <MinionTreeNode
                    v-for="minion in sortedRootMinions"
                    :key="minion.id"
                    :minion-data="minion"
                    :level="0"
                    @minion-click="navigateToSession"
                  />
                </div>
              </div>
            </template>

            <!-- Flat list view -->
            <div v-else class="flat-list">
              <!-- Ungrouped -->
              <div v-if="uiStore.flatGroupMode === 'none'" class="flat-list-nodes">
                <MinionTreeNode
                  v-for="node in filteredFlatNodes"
                  :key="node.id"
                  :minion-data="node"
                  :level="0"
                  @minion-click="navigateToSession"
                />
              </div>

              <!-- By Status (action-priority: unread → prompting → error → active → idle → terminated) -->
              <div v-else-if="uiStore.flatGroupMode === 'status'">
                <div v-for="group in groupedFlatNodes" :key="group.key" class="flat-group mb-3">
                  <h6 class="flat-group-heading text-muted">
                    {{ group.label }}
                    <span class="badge bg-secondary ms-2">{{ group.nodes.length }}</span>
                  </h6>
                  <MinionTreeNode
                    v-for="node in group.nodes"
                    :key="node.id"
                    :minion-data="node"
                    :level="0"
                    @minion-click="navigateToSession"
                  />
                </div>
              </div>

              <!-- Custom (user-managed priority groups, issue #1722) -->
              <div v-else>
                <!-- Suppress the per-bucket "No sessions in this group" wall when filters
                     zero out every bucket — the single message below covers it instead. -->
                <template v-if="!allSessionsFilteredOut">
                <div v-for="group in customGroupedFlatNodes" :key="group.key" class="flat-group mb-3">
                  <h6 class="flat-group-heading text-muted d-flex align-items-center justify-content-between">
                    <span class="d-flex align-items-center gap-2 flex-grow-1 min-w-0">
                      <input
                        v-if="editingGroupId === group.groupId"
                        type="text"
                        class="form-control form-control-sm w-auto group-name-input"
                        v-model="editingGroupName"
                        @keyup.enter="commitRenameGroup"
                        @keyup.esc="cancelRenameGroup"
                        @blur="commitRenameGroup"
                        @click.stop
                      >
                      <span
                        v-else
                        :class="{ 'group-name-clickable': group.groupId }"
                        :title="group.groupId ? 'Click to rename' : ''"
                        @click.stop="group.groupId && startRenameGroup(group)"
                      >{{ group.label }}</span>
                      <span class="badge bg-secondary">{{ group.nodes.length }}</span>
                    </span>

                    <!-- Per-group actions menu (custom groups only, not Unassigned) -->
                    <span v-if="group.groupId" class="position-relative">
                      <button
                        type="button"
                        class="btn btn-sm btn-link text-muted p-0 px-1 group-menu-toggle"
                        title="Group actions"
                        aria-label="Group actions"
                        @click.stop="toggleGroupMenu(group.groupId)"
                      >⋯</button>
                      <div
                        v-if="groupMenuOpenId === group.groupId"
                        class="group-menu card shadow"
                        role="menu"
                        @click.stop
                      >
                        <button type="button" class="group-menu-item" role="menuitem" @click="startRenameGroup(group)">Rename</button>
                        <button
                          type="button"
                          class="group-menu-item"
                          role="menuitem"
                          :disabled="!canMoveGroupUp(group.groupId)"
                          @click="moveGroup(group.groupId, -1)"
                        >Move Up</button>
                        <button
                          type="button"
                          class="group-menu-item"
                          role="menuitem"
                          :disabled="!canMoveGroupDown(group.groupId)"
                          @click="moveGroup(group.groupId, 1)"
                        >Move Down</button>
                        <button type="button" class="group-menu-item text-danger" role="menuitem" @click="beginDeleteGroup(group.groupId)">Delete</button>
                      </div>
                    </span>
                  </h6>

                  <!-- Inline delete confirmation (no modal, per explicit user feedback) -->
                  <div
                    v-if="confirmingDeleteGroupId === group.groupId"
                    class="alert alert-danger py-1 px-2 mb-2 d-flex align-items-center gap-2"
                  >
                    <span class="small flex-grow-1">Delete "{{ group.label }}"? Its sessions will move to Unassigned.</span>
                    <button type="button" class="btn btn-sm btn-outline-secondary py-0" @click="confirmingDeleteGroupId = undefined">Cancel</button>
                    <button type="button" class="btn btn-sm btn-danger py-0" @click="confirmDeleteGroup(group.groupId)">Delete</button>
                  </div>

                  <MinionTreeNode
                    v-for="node in group.nodes"
                    :key="node.id"
                    :minion-data="node"
                    :level="0"
                    @minion-click="navigateToSession"
                  />
                  <!-- Custom groups (unlike status buckets) still render empty, since they're user-managed -->
                  <div v-if="group.nodes.length === 0" class="text-muted small fst-italic px-2 py-1">
                    {{ group.totalCount === 0 ? 'No sessions in this group' : 'No sessions in this group match the active filters' }}
                  </div>
                </div>
                </template>

                <!-- Add group -->
                <div class="mt-2">
                  <input
                    v-if="addingGroup"
                    type="text"
                    class="form-control form-control-sm w-auto group-name-input"
                    v-model="newGroupName"
                    placeholder="Group name"
                    @keyup.enter="commitAddGroup"
                    @keyup.esc="cancelAddGroup"
                    @blur="commitAddGroup"
                    @click.stop
                  >
                  <button v-else type="button" class="btn btn-sm btn-outline-secondary" @click="beginAddGroup">+ New Group</button>
                </div>
              </div>

              <!-- Empty state -->
              <div v-if="projectSessions.length === 0" class="text-center text-muted py-4">
                No sessions yet. Click "Add Session" to create one.
              </div>
              <div v-else-if="allSessionsFilteredOut" class="text-center text-muted py-4">
                No sessions match the active filters.
              </div>
            </div>
          </div>
        </div>

        <!-- Configuration -->
        <div class="card">
          <div class="card-header">
            <h5 class="mb-0">Configuration</h5>
          </div>
          <div class="card-body">
            <dl class="row mb-0">
              <dt class="col-sm-4">Working Directory</dt>
              <dd class="col-sm-8 font-monospace text-break">{{ project.working_directory }}</dd>

              <dt class="col-sm-4">Project ID</dt>
              <dd class="col-sm-8 font-monospace text-break">{{ project.project_id }}</dd>

              <dt class="col-sm-4">Legion Mode</dt>
              <dd class="col-sm-8">{{ hasMinions ? 'Yes (has minions)' : 'No' }}</dd>
            </dl>
          </div>
        </div>
      </div>
    </div>

    <!-- Status bar -->
    <div class="project-overview-statusbar border-top p-2 bg-body-tertiary d-flex align-items-center justify-content-between">
      <small class="text-muted">
        Project Overview
      </small>
      <small class="text-muted">
        {{ sessionCount }} session{{ sessionCount !== 1 ? 's' : '' }}
      </small>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useProjectStore } from '@/stores/project'
import { useSessionStore } from '@/stores/session'
import { useUIStore } from '@/stores/ui'
import { useLegionStore } from '@/stores/legion'
import { useQueueStore } from '@/stores/queue'
import { compareAgents, normalizeLastActive } from '@/utils/agentSort'
import { getDisplayState } from '@/composables/useSessionState'
import { api } from '@/utils/api'
import { findInHierarchy, filterHierarchy } from '@/utils/hierarchyUtils'
import MinionTreeNode from '../legion/MinionTreeNode.vue'

const props = defineProps({
  projectId: {
    type: String,
    required: true
  }
})

const RESUME_MESSAGE = "Your session was stopped using an emergency stop feature, and is now being allowed to resume. If you had anything you were mid-progress on, continue it now. Otherwise, you are back online and ready to help."

const router = useRouter()
const projectStore = useProjectStore()
const sessionStore = useSessionStore()
const uiStore = useUIStore()
const legionStore = useLegionStore()
const queueStore = useQueueStore()

const loading = ref(false)
const error = ref(null)
const minionHierarchy = ref(null)
const loadingHierarchy = ref(false)
let sessionWatchStop = null

// Fleet control state
const stopState = ref('default') // 'default' | 'confirming' | 'stopping'
const resumeState = ref('default') // 'default' | 'confirming' | 'resuming'
const resumeBatchSizeInput = ref(uiStore.resumeBatchSize)
const resumeBatchDelayInput = ref(uiStore.resumeBatchDelaySeconds)
const fleetToast = ref(null)
let fleetToastTimer = null

// Issue #2159 (stage 6c): local cache of the Backend-persisted fleet snapshot —
// replaces frontend/src/utils/stoppedSet.js's sessionStorage-only bookkeeping,
// which never survived a browser reload or a Backend restart. Loaded from
// legionStore.getFleetSnapshot() on mount/project-switch (restoreFleetSnapshot()
// below); every mutation below updates this local Set immediately (so the UI
// doesn't wait on a network round trip) and persists the same change via
// legionStore.recordFleetStopped()/clearFleetStopped().
const fleetStoppedIds = ref(new Set())
const fleetProcessingIds = ref(new Set())
const stoppedCount = computed(() => fleetStoppedIds.value.size)

// Issue #1934: Batch selection controls (Stop Selected / Start Selected)
const stopSelectedState = ref('default') // 'default' | 'confirming' | 'stopping'
const startSelectedState = ref('default') // 'default' | 'confirming' | 'starting'
const startSelectedBatchSizeInput = ref(uiStore.resumeBatchSize)
const startSelectedBatchDelayInput = ref(uiStore.resumeBatchDelaySeconds)

function setFleetToast(type, message, autoDismissMs = 8000) {
  fleetToast.value = { type, message }
  clearTimeout(fleetToastTimer)
  if (autoDismissMs) {
    fleetToastTimer = setTimeout(() => { fleetToast.value = null }, autoDismissMs)
  }
}

// Local-only prune against currently-known live sessions, for display accuracy
// between network round trips. The Backend's own get_fleet_snapshot() self-prunes
// server-side on every real fetch, so this never needs its own backend call.
function refreshStoppedCount() {
  const allIds = new Set(projectSessions.value.map(s => s.session_id))
  fleetStoppedIds.value = new Set([...fleetStoppedIds.value].filter(id => allIds.has(id)))
  fleetProcessingIds.value = new Set([...fleetProcessingIds.value].filter(id => allIds.has(id)))
}

async function releaseStartedSessions() {
  const current = [...fleetStoppedIds.value]
  if (current.length === 0) return
  const started = current.filter(id => {
    const s = sessionStore.getSession(id)
    return s?.state?.toUpperCase() === 'ACTIVE'
  })
  if (started.length === 0) return
  started.forEach(id => {
    fleetStoppedIds.value.delete(id)
    fleetProcessingIds.value.delete(id)
  })
  refreshStoppedCount()
  try {
    await legionStore.clearFleetStopped(props.projectId, started)
  } catch (err) {
    console.error('Failed to clear fleet snapshot for released sessions:', err)
  }
}

function beginStopConfirm() {
  stopState.value = 'confirming'
  fleetToast.value = null
}

function cancelStop() {
  stopState.value = 'default'
}

// Issue #1933: bounded window to let Backend's still-running-in-the-background
// halt-all catch up after the HTTP request itself failed/timed out client-side.
// Aligned with (and a bit above) src/routers/relay.py's 120s halt-all relay
// timeout — in the worst case the HTTP call doesn't fail until that ceiling,
// so the reconciliation window has to be at least that long to have any chance
// of observing Backend actually finish, not just of catching an early network
// blip. The 2s poll interval stays cheap regardless of how long the window is:
// each tick only reads already-live sessionStore state, no extra network calls.
const STOP_RECONCILE_TOTAL_MS = 130000
const STOP_RECONCILE_INTERVAL_MS = 2000

function isSessionTerminated(sessionId) {
  const s = sessionStore.getSession(sessionId)
  return s?.state?.toUpperCase?.() === 'TERMINATED'
}

async function applyReconciledStopped(confirmedStopped, processingSnapshot) {
  if (confirmedStopped.length === 0) return
  const wasProcessing = confirmedStopped.filter(id => processingSnapshot.get(id))
  confirmedStopped.forEach(id => fleetStoppedIds.value.add(id))
  wasProcessing.forEach(id => fleetProcessingIds.value.add(id))
  refreshStoppedCount()
  try {
    // Merge (not overwrite): issue #1934's Stop Selected can record into this same
    // project's fleet snapshot concurrently — the Backend union()s, so overwriting
    // here would still be safe, but recordFleetStopped() always merges regardless.
    await legionStore.recordFleetStopped(props.projectId, confirmedStopped, wasProcessing)
  } catch (err) {
    console.error('Failed to record fleet snapshot:', err)
  }
}

async function reconcileStopFailure(targetIds, processingSnapshot) {
  const deadline = Date.now() + STOP_RECONCILE_TOTAL_MS
  // Written progressively (not just once at the end) so Resume Sessions reflects
  // newly-confirmed sessions as soon as each poll tick detects them, rather than
  // making the operator wait out the full bounded window to see any result.
  let confirmedStopped = targetIds.filter(isSessionTerminated)
  await applyReconciledStopped(confirmedStopped, processingSnapshot)

  while (confirmedStopped.length < targetIds.length && Date.now() < deadline) {
    await new Promise(resolve => setTimeout(resolve, STOP_RECONCILE_INTERVAL_MS))
    confirmedStopped = targetIds.filter(isSessionTerminated)
    await applyReconciledStopped(confirmedStopped, processingSnapshot)
  }

  return confirmedStopped
}

async function confirmStop() {
  stopState.value = 'stopping'
  // Snapshot is_processing before termination — sessions may be mid-task
  const processingSnapshot = new Map(
    projectSessions.value.map(s => [s.session_id, !!s.is_processing])
  )
  // Exclude already-TERMINATED sessions from the reconciliation target set —
  // mirrors emergency_halt_all()'s own exclusion (legion_coordinator.py), so a
  // session stopped earlier for unrelated reasons doesn't get folded into this
  // Stop All's fleet snapshot/Resume Sessions tracking on the failure-path.
  const targetIds = projectSessions.value
    .filter(s => s.state?.toUpperCase?.() !== 'TERMINATED')
    .map(s => s.session_id)
  try {
    const result = await legionStore.haltAll(props.projectId)
    const stopped = result.stopped_session_ids ?? []
    const failed = result.failed_sessions ?? []

    // emergency_halt_all() already persisted the snapshot server-side (issue
    // #2159) — just mirror it into the local cache, no redundant backend call.
    const wasProcessing = stopped.filter(id => processingSnapshot.get(id))
    stopped.forEach(id => fleetStoppedIds.value.add(id))
    wasProcessing.forEach(id => fleetProcessingIds.value.add(id))
    refreshStoppedCount()

    if (failed.length === 0) {
      setFleetToast('success', `✓ Stopped ${stopped.length} session${stopped.length !== 1 ? 's' : ''}. Click "Resume Sessions" to bring them back online.`)
    } else {
      const failedNames = failed.map(([id, err]) => {
        const s = sessionStore.getSession(id)
        return `${s?.name || id} (${err})`
      }).join(', ')
      setFleetToast('danger', `✗ Stopped ${stopped.length} of ${result.total_sessions} sessions. Failed: ${failedNames}. You can retry Stop All for remaining sessions.`, 0)
    }
  } catch (err) {
    setFleetToast('danger', `Stop All request failed (${err.message || err}) — checking which sessions actually stopped...`, 0)
    // Unlock the UI now — the request itself already failed, and reconciliation
    // below can take up to STOP_RECONCILE_TOTAL_MS. Leaving stopState 'stopping'
    // for that whole window would keep Stop All spinner-locked and Resume
    // Sessions disabled even as sessions get progressively confirmed.
    stopState.value = 'default'
    const confirmedStopped = await reconcileStopFailure(targetIds, processingSnapshot)
    if (confirmedStopped.length > 0) {
      setFleetToast('warning', `⚠ Stop All request timed out, but ${confirmedStopped.length} of ${targetIds.length} sessions were confirmed stopped. Click "Resume Sessions" to bring them back online.`, 0)
    } else {
      setFleetToast('danger', `✗ Stop All failed: ${err.message || err}`, 0)
    }
  } finally {
    stopState.value = 'default'
  }
}

function beginResumeConfirm() {
  if (resumeState.value !== 'default' || stoppedCount.value === 0 || stopState.value === 'stopping') return
  resumeBatchSizeInput.value = uiStore.resumeBatchSize
  resumeBatchDelayInput.value = uiStore.resumeBatchDelaySeconds
  resumeState.value = 'confirming'
  fleetToast.value = null
}

function cancelResume() {
  resumeState.value = 'default'
}

async function confirmResume() {
  const batchSize = Math.max(1, parseInt(resumeBatchSizeInput.value, 10) || uiStore.resumeBatchSize)
  const rawDelay = parseInt(resumeBatchDelayInput.value, 10)
  const delaySeconds = Number.isFinite(rawDelay) && rawDelay >= 0
    ? rawDelay
    : uiStore.resumeBatchDelaySeconds
  resumeState.value = 'resuming'
  fleetToast.value = null
  try {
    const allIds = new Set(projectSessions.value.map(s => s.session_id))
    fleetStoppedIds.value = new Set([...fleetStoppedIds.value].filter(id => allIds.has(id)))
    const toResume = [...fleetStoppedIds.value].filter(id => {
      const s = sessionStore.getSession(id)
      if (!s) return false
      const state = s.state?.toUpperCase?.() || s.state
      return state !== 'ACTIVE' && state !== 'STARTING'
    })

    const processingSet = fleetProcessingIds.value

    // Throttle: resume in sequential batches of `batchSize` so we don't fire every
    // stopped session's start/enqueue concurrently (issue #1733).
    const settled = []
    for (let i = 0; i < toResume.length; i += batchSize) {
      const chunk = toResume.slice(i, i + batchSize)
      const chunkSettled = await Promise.allSettled(
        chunk.map(id =>
          processingSet.has(id)
            ? queueStore.enqueueMessage(id, RESUME_MESSAGE, false)
            : sessionStore.startSession(id)
        )
      )
      settled.push(...chunkSettled)

      const isLastBatch = i + batchSize >= toResume.length
      if (delaySeconds > 0 && !isLastBatch) {
        await new Promise(resolve => setTimeout(resolve, delaySeconds * 1000))
      }
    }

    const succeeded = toResume.filter((_, i) => settled[i].status === 'fulfilled')
    const failed = toResume.filter((_, i) => settled[i].status === 'rejected')

    // Clear successfully resumed sessions from the fleet snapshot; keep failed for retry.
    succeeded.forEach(id => {
      fleetStoppedIds.value.delete(id)
      fleetProcessingIds.value.delete(id)
    })
    refreshStoppedCount()
    if (succeeded.length > 0) {
      try {
        await legionStore.clearFleetStopped(props.projectId, succeeded)
      } catch (err) {
        console.error('Failed to clear fleet snapshot for resumed sessions:', err)
      }
    }

    if (failed.length === 0) {
      setFleetToast('success', `✓ Resumed ${succeeded.length} session${succeeded.length !== 1 ? 's' : ''}. They will restart shortly.`)
    } else {
      setFleetToast('danger', `✗ Resumed ${succeeded.length} session${succeeded.length !== 1 ? 's' : ''}; ${failed.length} failed. You can retry Resume for the remaining sessions.`, 0)
    }
  } catch (err) {
    setFleetToast('danger', `✗ Resume failed: ${err.message || err}`, 0)
  } finally {
    resumeState.value = 'default'
  }
}

// --- Issue #1934: Selection-scoped batch Stop/Start ---
// Eligibility is derived from the same live projectSessions computed used
// throughout this file, so it's always recomputed from current sessionStore
// data at click time rather than a stale snapshot taken at select-time.

const selectedSessions = computed(() =>
  projectSessions.value.filter(s => uiStore.selectedSessionIds.has(s.session_id))
)

// Live-pruned count: a session deleted via another UI surface (e.g.
// DeletedAgentsModal) drops out of projectSessions but stays in the raw
// uiStore.selectedSessionIds Set until explicitly cleared — deriving the
// displayed count from selectedSessions (already filtered against live
// projectSessions) instead of the Set's raw size keeps it accurate.
const selectedCount = computed(() => selectedSessions.value.length)

const stoppableSelectedSessions = computed(() =>
  selectedSessions.value.filter(s => s.state?.toUpperCase?.() !== 'TERMINATED')
)

const startableSelectedSessions = computed(() =>
  selectedSessions.value.filter(s => {
    const state = s.state?.toUpperCase?.() || s.state
    return state !== 'ACTIVE' && state !== 'STARTING'
  })
)

const stoppableSelectedCount = computed(() => stoppableSelectedSessions.value.length)
const startableSelectedCount = computed(() => startableSelectedSessions.value.length)

function beginStopSelectedConfirm() {
  if (stopSelectedState.value !== 'default' || stoppableSelectedCount.value === 0) return
  stopSelectedState.value = 'confirming'
  fleetToast.value = null
}

function cancelStopSelected() {
  stopSelectedState.value = 'default'
}

async function confirmStopSelected() {
  stopSelectedState.value = 'stopping'
  fleetToast.value = null

  const targets = stoppableSelectedSessions.value
  const totalSelected = selectedSessions.value.length
  const skipped = totalSelected - targets.length
  const processingSnapshot = new Map(targets.map(s => [s.session_id, !!s.is_processing]))

  try {
    const settled = await Promise.allSettled(targets.map(s => sessionStore.terminateSession(s.session_id)))
    const ids = targets.map(s => s.session_id)
    const succeeded = ids.filter((_, i) => settled[i].status === 'fulfilled')
    const failed = ids.filter((_, i) => settled[i].status === 'rejected')

    if (succeeded.length > 0) {
      // Fold into the same fleet snapshot Stop All uses, so Resume Sessions
      // remains the single undo regardless of which path stopped a session.
      await applyReconciledStopped(succeeded, processingSnapshot)
    }

    uiStore.clearSessionSelection()

    const notes = []
    if (skipped > 0) notes.push(`${skipped} already stopped`)
    if (failed.length > 0) notes.push(`${failed.length} failed`)
    const suffix = notes.length > 0 ? ` (${notes.join(', ')})` : ''
    setFleetToast(
      failed.length > 0 ? 'danger' : 'success',
      `${failed.length > 0 ? '✗' : '✓'} Stopped ${succeeded.length} of ${totalSelected} selected${suffix}.`
    )
  } catch (err) {
    setFleetToast('danger', `✗ Stop Selected failed: ${err.message || err}`, 0)
  } finally {
    stopSelectedState.value = 'default'
  }
}

function beginStartSelectedConfirm() {
  if (startSelectedState.value !== 'default' || startableSelectedCount.value === 0) return
  startSelectedBatchSizeInput.value = uiStore.resumeBatchSize
  startSelectedBatchDelayInput.value = uiStore.resumeBatchDelaySeconds
  startSelectedState.value = 'confirming'
  fleetToast.value = null
}

function cancelStartSelected() {
  startSelectedState.value = 'default'
}

async function confirmStartSelected() {
  const batchSize = Math.max(1, parseInt(startSelectedBatchSizeInput.value, 10) || uiStore.resumeBatchSize)
  const rawDelay = parseInt(startSelectedBatchDelayInput.value, 10)
  const delaySeconds = Number.isFinite(rawDelay) && rawDelay >= 0
    ? rawDelay
    : uiStore.resumeBatchDelaySeconds
  startSelectedState.value = 'starting'
  fleetToast.value = null

  const totalSelected = selectedSessions.value.length
  const toStart = startableSelectedSessions.value.map(s => s.session_id)
  const skipped = totalSelected - toStart.length
  const processingSet = fleetProcessingIds.value

  try {
    const settled = []
    for (let i = 0; i < toStart.length; i += batchSize) {
      const chunk = toStart.slice(i, i + batchSize)
      const chunkSettled = await Promise.allSettled(
        chunk.map(id =>
          processingSet.has(id)
            ? queueStore.enqueueMessage(id, RESUME_MESSAGE, false)
            : sessionStore.startSession(id)
        )
      )
      settled.push(...chunkSettled)

      const isLastBatch = i + batchSize >= toStart.length
      if (delaySeconds > 0 && !isLastBatch) {
        await new Promise(resolve => setTimeout(resolve, delaySeconds * 1000))
      }
    }

    const succeeded = toStart.filter((_, i) => settled[i].status === 'fulfilled')
    const failed = toStart.filter((_, i) => settled[i].status === 'rejected')

    if (succeeded.length > 0) {
      // Same shared tracking as Resume Sessions: drop successfully-started ids,
      // keep failed ones for retry via the existing Resume Sessions control.
      succeeded.forEach(id => {
        fleetStoppedIds.value.delete(id)
        fleetProcessingIds.value.delete(id)
      })
      refreshStoppedCount()
      try {
        await legionStore.clearFleetStopped(props.projectId, succeeded)
      } catch (err) {
        console.error('Failed to clear fleet snapshot for started sessions:', err)
      }
    }

    uiStore.clearSessionSelection()

    const notes = []
    if (skipped > 0) notes.push(`${skipped} already active`)
    if (failed.length > 0) notes.push(`${failed.length} failed`)
    const suffix = notes.length > 0 ? ` (${notes.join(', ')})` : ''
    setFleetToast(
      failed.length > 0 ? 'danger' : 'success',
      `${failed.length > 0 ? '✗' : '✓'} Started ${succeeded.length} of ${totalSelected} selected${suffix}.`
    )
  } catch (err) {
    setFleetToast('danger', `✗ Start Selected failed: ${err.message || err}`, 0)
  } finally {
    startSelectedState.value = 'default'
  }
}

// Get project data
const project = computed(() => projectStore.getProject(props.projectId))

// Get sessions for this project
const projectSessions = computed(() => {
  if (!project.value?.session_ids) return []
  return project.value.session_ids
    .map(id => sessionStore.getSession(id))
    .filter(s => s !== null && s !== undefined)
})

// Issue #349: All sessions are minions - check if project has any sessions
const hasMinions = computed(() =>
  projectSessions.value.length > 0
)

// Stats
const sessionCount = computed(() => projectSessions.value.length)

const activeSessionCount = computed(() =>
  projectSessions.value.filter(s => s.state === 'ACTIVE').length
)

// Overall status
const overallStatus = computed(() => {
  const hasActive = projectSessions.value.some(s => s.state === 'ACTIVE')
  const hasProcessing = projectSessions.value.some(s => s.is_processing)
  const hasError = projectSessions.value.some(s => s.state === 'ERROR')

  if (hasError) return 'Error'
  if (hasProcessing) return 'Working'
  if (hasActive) return 'Active'
  if (sessionCount.value > 0) return 'Idle'
  return 'Empty'
})

const overallStatusClass = computed(() => {
  switch (overallStatus.value) {
    case 'Error': return 'text-danger'
    case 'Working': return 'text-warning'
    case 'Active': return 'text-success'
    case 'Idle': return 'text-muted'
    default: return 'text-secondary'
  }
})

const statusDescription = computed(() => {
  switch (overallStatus.value) {
    case 'Error': return 'One or more sessions have errors'
    case 'Working': return 'Processing in progress'
    case 'Active': return 'Sessions are running'
    case 'Idle': return 'No active processing'
    default: return 'No sessions created'
  }
})

// Load minion hierarchy from API
async function loadMinionHierarchy() {
  loadingHierarchy.value = true
  try {
    const response = await api.get(`/api/legions/${props.projectId}/hierarchy`)
    minionHierarchy.value = response
  } catch (err) {
    console.error('Failed to load minion hierarchy:', err)
    minionHierarchy.value = null
  } finally {
    loadingHierarchy.value = false
  }
}

const sortedRootMinions = computed(() => {
  const children = filteredHierarchyChildren.value
  const mode = uiStore.agentSort
  const direction = uiStore.agentSortDirection === 'desc' ? -1 : 1
  return [...children].sort((a, b) => compareAgents(mode, a, b, {
    nameOf: n => n.name,
    orderOf: n => sessionStore.getSession(n.id)?.order,
    idOf: n => n.id,
    lastActiveOf: n => normalizeLastActive(sessionStore.getSession(n.id)?.last_completion_at)
  }, direction))
})

const flatNodes = computed(() =>
  projectSessions.value.map(s => ({
    id: s.session_id,
    name: s.name || s.session_id,
    type: 'minion',
    state: s.state,
    is_processing: s.is_processing,
    latest_message: s.latest_message,
    latest_message_type: s.latest_message_type,
    latest_message_time: s.latest_message_time,
    children: [],
  }))
)

const sortedFlatNodes = computed(() => {
  const nodes = flatNodes.value
  const mode = uiStore.flatSort
  const direction = uiStore.flatSortDirection === 'desc' ? -1 : 1
  return [...nodes].sort((a, b) => compareAgents(mode, a, b, {
    nameOf: n => n.name,
    orderOf: n => sessionStore.getSession(n.id)?.order,
    idOf: n => n.id,
    lastActiveOf: n => normalizeLastActive(sessionStore.getSession(n.id)?.last_completion_at),
  }, direction))
})

const STATE_GROUPS = [
  { key: 'unread',     label: 'Unread' },
  { key: 'prompting',  label: 'Prompting' },
  { key: 'error',      label: 'Error' },
  { key: 'active',     label: 'Active' },
  { key: 'idle',       label: 'Idle' },
  { key: 'terminated', label: 'Terminated' },
]

function classifyNode(node) {
  if (sessionStore.isUnreviewed(node.id)) return 'unread'
  const live = sessionStore.getSession(node.id)
  const ds = getDisplayState(live || node)
  if (ds === 'pending-prompt') return 'prompting'
  if (ds === 'error' || ds === 'failed') return 'error'
  if (ds === 'active' || ds === 'starting' || ds === 'processing' || ds === 'running') return 'active'
  if (ds === 'created' || ds === 'paused') return 'idle'
  if (ds === 'terminated') return 'terminated'
  return 'idle'
}

// Filter state (issue #2050): component-local, not persisted to uiStore —
// uiStore's other view-mode/sort state is global across all projects, which would be
// wrong for a project-scoped kanban-group-ID filter. Applies to both Flat and
// Hierarchy view.
const filterSearchQuery = ref('')
const filterStatuses = ref(new Set())
const filterGroupIds = ref(new Set())

function resetFilters() {
  filterSearchQuery.value = ''
  filterStatuses.value = new Set()
  filterGroupIds.value = new Set()
}

function toggleFilterStatus(key) {
  const next = new Set(filterStatuses.value)
  if (next.has(key)) next.delete(key)
  else next.add(key)
  filterStatuses.value = next
}

// Bridges the native multi-select's array v-model to the Set used by the predicate.
const filterGroupIdsArray = computed({
  get: () => [...filterGroupIds.value],
  set: (arr) => { filterGroupIds.value = new Set(arr) },
})

// Drop any selected group filter whose group was deleted out from under it — otherwise
// a stale, now-unselectable group_id stays in filterGroupIds and silently zeroes out
// every result (the <select> can't show it as selected since its <option> is gone).
watch(() => project.value?.kanban_groups, (groups) => {
  if (filterGroupIds.value.size === 0) return
  const validIds = new Set(['unassigned', ...(groups || []).map(g => g.group_id)])
  const next = new Set([...filterGroupIds.value].filter(id => validIds.has(id)))
  if (next.size !== filterGroupIds.value.size) filterGroupIds.value = next
})

// Single source of truth for the filter predicate — exported (named function, not
// inlined into a template expression) so Stage B's hierarchy pruning can reuse it
// unchanged.
function sessionMatchesFilter(id) {
  if (filterSearchQuery.value.trim()) {
    const live = sessionStore.getSession(id)
    const q = filterSearchQuery.value.trim().toLowerCase()
    const hay = `${live?.name || ''} ${live?.role || ''}`.toLowerCase()
    if (!hay.includes(q)) return false
  }
  if (filterStatuses.value.size > 0 && !filterStatuses.value.has(classifyNode({ id }))) {
    return false
  }
  if (filterGroupIds.value.size > 0) {
    const groupId = project.value?.kanban_group_assignments?.[id] || 'unassigned'
    if (!filterGroupIds.value.has(groupId)) return false
  }
  return true
}

const hasActiveFilters = computed(() =>
  !!filterSearchQuery.value.trim() || filterStatuses.value.size > 0 || filterGroupIds.value.size > 0
)

// Identity passthrough when inactive — guarantees "no behavior change when no
// filter is active" by construction rather than by parallel-maintained logic.
const filteredFlatNodes = computed(() =>
  hasActiveFilters.value ? sortedFlatNodes.value.filter(n => sessionMatchesFilter(n.id)) : sortedFlatNodes.value
)

// Named once so the "all results filtered out" check can't drift between its
// two template usages (the Custom-group wall-suppression and the empty-state message).
const allSessionsFilteredOut = computed(() => hasActiveFilters.value && filteredFlatNodes.value.length === 0)

// Hierarchy-view counterpart to filteredFlatNodes (issue #2050, Stage B): prunes
// branches with no match anywhere in their subtree, dims non-matching ancestors kept
// for context. Identity passthrough when inactive, same guarantee as filteredFlatNodes.
const filteredHierarchyChildren = computed(() => {
  const children = minionHierarchy.value?.children || []
  if (!hasActiveFilters.value) return children
  return children.map(c => filterHierarchy(c, n => sessionMatchesFilter(n.id))).filter(Boolean)
})

const groupedFlatNodes = computed(() => {
  const sorted = filteredFlatNodes.value
  const buckets = STATE_GROUPS.map(g => ({ ...g, nodes: [] }))
  const byKey = Object.fromEntries(buckets.map(b => [b.key, b]))
  for (const node of sorted) {
    byKey[classifyNode(node)].nodes.push(node)
  }
  return buckets.filter(b => b.nodes.length > 0)
})

// Custom priority groups (issue #1722): buckets filteredFlatNodes by the project's
// kanban_group_assignments map, falling back to the implicit Unassigned bucket.
// Unlike groupedFlatNodes above, buckets are never filtered out when empty — custom
// groups are user-managed, so the user needs to see/manage them even at zero members.
// totalCount (unfiltered) rides alongside nodes (filtered) so the template can tell
// a genuinely empty group apart from one whose members are merely filtered out —
// otherwise both render the same misleading "No sessions in this group" text.
const customGroupedFlatNodes = computed(() => {
  const buckets = [{ key: 'unassigned', groupId: null, label: 'Unassigned', nodes: [], totalCount: 0 }]
  for (const group of project.value?.kanban_groups || []) {
    buckets.push({ key: group.group_id, groupId: group.group_id, label: group.name, nodes: [], totalCount: 0 })
  }
  const byKey = Object.fromEntries(buckets.map(b => [b.key, b]))
  for (const node of sortedFlatNodes.value) {
    const groupId = project.value?.kanban_group_assignments?.[node.id] || 'unassigned'
    ;(byKey[groupId] || byKey.unassigned).totalCount++
  }
  for (const node of filteredFlatNodes.value) {
    const groupId = project.value?.kanban_group_assignments?.[node.id] || 'unassigned'
    ;(byKey[groupId] || byKey.unassigned).nodes.push(node)
  }
  return buckets
})

// --- Custom group management (issue #1722): rename, reorder, delete, create ---

// editingGroupId/confirmingDeleteGroupId default to undefined (NOT null): the
// Unassigned bucket's groupId is null, so a null default would make its v-if
// checks (`=== group.groupId`) match on initial render, before any click.
const editingGroupId = ref(undefined)
const editingGroupName = ref('')
const groupMenuOpenId = ref(null)
const confirmingDeleteGroupId = ref(undefined)
const addingGroup = ref(false)
const newGroupName = ref('')

function startRenameGroup(group) {
  groupMenuOpenId.value = null
  editingGroupId.value = group.groupId
  editingGroupName.value = group.label
}

function cancelRenameGroup() {
  editingGroupId.value = undefined
}

async function commitRenameGroup() {
  const groupId = editingGroupId.value
  if (!groupId) return
  const name = editingGroupName.value.trim()
  editingGroupId.value = undefined
  if (!name || !project.value) return
  const current = project.value.kanban_groups?.find(g => g.group_id === groupId)
  if (current && current.name === name) return
  try {
    await projectStore.renameKanbanGroup(project.value.project_id, groupId, name)
  } catch (e) {
    console.error('Failed to rename kanban group:', e)
  }
}

function toggleGroupMenu(groupId) {
  groupMenuOpenId.value = groupMenuOpenId.value === groupId ? null : groupId
}

function closeGroupMenu() {
  groupMenuOpenId.value = null
}

function canMoveGroupUp(groupId) {
  const groups = project.value?.kanban_groups || []
  return groups.findIndex(g => g.group_id === groupId) > 0
}

function canMoveGroupDown(groupId) {
  const groups = project.value?.kanban_groups || []
  const idx = groups.findIndex(g => g.group_id === groupId)
  return idx >= 0 && idx < groups.length - 1
}

async function moveGroup(groupId, direction) {
  groupMenuOpenId.value = null
  if (!project.value) return
  const groups = [...(project.value.kanban_groups || [])]
  const idx = groups.findIndex(g => g.group_id === groupId)
  const target = idx + direction
  if (idx < 0 || target < 0 || target >= groups.length) return
  ;[groups[idx], groups[target]] = [groups[target], groups[idx]]
  try {
    await projectStore.reorderKanbanGroups(project.value.project_id, groups.map(g => g.group_id))
  } catch (e) {
    console.error('Failed to reorder kanban groups:', e)
  }
}

function beginDeleteGroup(groupId) {
  groupMenuOpenId.value = null
  confirmingDeleteGroupId.value = groupId
}

async function confirmDeleteGroup(groupId) {
  confirmingDeleteGroupId.value = undefined
  if (!project.value) return
  try {
    await projectStore.deleteKanbanGroup(project.value.project_id, groupId)
  } catch (e) {
    console.error('Failed to delete kanban group:', e)
  }
}

function beginAddGroup() {
  addingGroup.value = true
  newGroupName.value = ''
}

function cancelAddGroup() {
  addingGroup.value = false
}

async function commitAddGroup() {
  if (!addingGroup.value) return
  const name = newGroupName.value.trim()
  addingGroup.value = false
  newGroupName.value = ''
  if (!name || !project.value) return
  try {
    await projectStore.createKanbanGroup(project.value.project_id, name)
  } catch (e) {
    console.error('Failed to create kanban group:', e)
  }
}

// Navigation
function navigateToSession(sessionId) {
  router.push(`/session/${sessionId}`)
}

// Modals
function showEditModal() {
  uiStore.showModal('edit-project', { project: project.value })
}

function showCreateModal() {
  if (project.value) {
    router.push(`/settings/session/__new__/general?project_id=${project.value.project_id}`)
  }
}

// Issue #2159 (stage 6c): load the Backend-persisted fleet snapshot for a
// project — replaces the old sessionStorage getStoppedSet()/getProcessingSet()
// read, so "Stop All from earlier still pending" survives a browser reload
// AND a Backend restart. Raw snapshot without pruning against live sessions —
// they may not be loaded yet when this runs; pruning happens in
// refreshStoppedCount() after any stop/resume action.
async function restoreFleetSnapshot(projectId) {
  try {
    const snapshot = await legionStore.getFleetSnapshot(projectId)
    fleetStoppedIds.value = new Set(snapshot.stopped_session_ids || [])
    fleetProcessingIds.value = new Set(snapshot.processing_session_ids || [])
  } catch (err) {
    console.error('Failed to load fleet snapshot:', err)
    fleetStoppedIds.value = new Set()
    fleetProcessingIds.value = new Set()
  }
  await releaseStartedSessions()
  if (stoppedCount.value > 0) {
    setFleetToast('info', `ⓘ Stop All from earlier still pending — Resume is available for ${stoppedCount.value} session${stoppedCount.value !== 1 ? 's' : ''}.`, 0)
  }
}

// Select project on mount and load hierarchy
onMounted(() => {
  projectStore.selectProject(props.projectId)
  // Clear session selection when viewing project overview
  sessionStore.currentSessionId = null

  // Issue #1934: App.vue keys the routed component on route.path, so navigating
  // between projects remounts this component rather than just updating
  // props.projectId — the watch() below never fires for that case. Clearing here
  // too is what actually makes "not preserved across a project switch" true.
  uiStore.clearSessionSelection()
  resetFilters()

  restoreFleetSnapshot(props.projectId)

  // Load minion hierarchy
  loadMinionHierarchy()

  // Watch for minion state changes from session store
  sessionWatchStop = watch(
    () => sessionStore.sessions,
    (sessions) => {
      releaseStartedSessions()

      if (!minionHierarchy.value) return

      // Update all minion states, is_processing, and latest_message in hierarchy
      for (const [sessionId, session] of sessions) {
        if (session.project_id === props.projectId) {
          const minion = findInHierarchy(minionHierarchy.value, n => n.id === sessionId)
          if (minion && minion.type === 'minion') {
            // Update state if changed
            if (minion.state !== session.state) {
              minion.state = session.state
            }
            // Update is_processing if changed
            if (minion.is_processing !== session.is_processing) {
              minion.is_processing = session.is_processing
            }
            // Update latest_message fields if changed
            if (minion.latest_message !== session.latest_message) {
              minion.latest_message = session.latest_message
              minion.latest_message_type = session.latest_message_type
              minion.latest_message_time = session.latest_message_time
            }
            // Issue #2259: mirror disposed_at (and created_at, for symmetry —
            // it never changes post-creation) so the hierarchy's activity
            // fallback reflects a dispose observed via live broadcast.
            if (minion.disposed_at !== session.disposed_at) {
              minion.disposed_at = session.disposed_at
            }
            if (minion.created_at !== session.created_at) {
              minion.created_at = session.created_at
            }
          }
        }
      }
    },
    { deep: true }
  )

  // Watch for minions being created or deleted (reload hierarchy)
  watch(
    () => {
      const proj = projectStore.projects.get(props.projectId)
      return proj?.session_ids?.length || 0
    },
    (newLength, oldLength) => {
      if (newLength !== oldLength) {
        loadMinionHierarchy()
      }
    }
  )
})

// Update selection when projectId changes
watch(() => props.projectId, (newId) => {
  projectStore.selectProject(newId)
  sessionStore.currentSessionId = null
  stopState.value = 'default'
  resumeState.value = 'default'
  stopSelectedState.value = 'default'
  startSelectedState.value = 'default'
  uiStore.clearSessionSelection()
  resetFilters()
  fleetToast.value = null
  clearTimeout(fleetToastTimer)
  restoreFleetSnapshot(newId)
  loadMinionHierarchy()
})

// Close the per-group "⋯" actions menu on any click outside it (issue #1722)
onMounted(() => {
  document.addEventListener('click', closeGroupMenu)
})

// Cleanup on unmount
onUnmounted(() => {
  if (sessionWatchStop) {
    sessionWatchStop()
  }
  clearTimeout(fleetToastTimer)
  document.removeEventListener('click', closeGroupMenu)
})
</script>

<style scoped>
.project-overview {
  background-color: var(--bs-body-bg);
}

.project-overview-header {
  background-color: var(--bs-secondary-bg);
}

.project-overview-content {
  background-color: var(--bs-body-bg);
}

.project-overview-statusbar {
  font-size: 0.85rem;
}

.card {
  box-shadow: 0 1px 3px rgba(var(--bs-emphasis-color-rgb, 0, 0, 0), 0.1);
}

.list-group-item-action:hover {
  background-color: var(--bs-secondary-bg);
}

/* Hierarchy tree styles */
.hierarchy-tree {
  background-color: var(--bs-secondary-bg);
  padding: 0.5rem;
  border-radius: 0.25rem;
}

.flat-list {
  background-color: var(--bs-secondary-bg);
  padding: 0.5rem;
  border-radius: 0.25rem;
}

.flat-group-heading {
  font-size: 0.85rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  padding: 0.25rem 0.5rem;
  margin-bottom: 0.5rem;
  border-bottom: 1px solid var(--bs-border-color);
}

.group-name-clickable {
  cursor: pointer;
}

.group-name-clickable:hover {
  text-decoration: underline;
}

.group-name-input {
  font-size: 0.85rem;
  padding: 0.1rem 0.4rem;
  height: auto;
}

.group-menu-toggle {
  font-size: 1rem;
  line-height: 1;
  text-decoration: none;
}

.group-menu {
  position: absolute;
  top: calc(100% + 2px);
  right: 0;
  z-index: 1060;
  width: 140px;
  padding: 0.25rem 0;
}

.group-menu-item {
  display: block;
  width: 100%;
  padding: 0.35rem 0.75rem;
  background: none;
  border: none;
  text-align: left;
  font-size: 0.8rem;
  text-transform: none;
  letter-spacing: normal;
  color: var(--bs-body-color);
  cursor: pointer;
}

.group-menu-item:hover:not(:disabled) {
  background-color: var(--bs-secondary-bg);
}

.group-menu-item:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
</style>
