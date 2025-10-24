import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { api } from '../utils/api'

/**
 * Project Store - Manages project state and operations
 */
export const useProjectStore = defineStore('project', () => {
  // ========== STATE ==========

  // Projects map (projectId -> ProjectData)
  const projects = ref(new Map())

  // Current project selection (for Legion timeline/spy/horde views)
  const currentProjectId = ref(null)

  // ========== COMPUTED ==========

  const currentProject = computed(() =>
    projects.value.get(currentProjectId.value)
  )

  // Ordered projects - sorted by order property
  const orderedProjects = computed(() =>
    Array.from(projects.value.values())
      .sort((a, b) => a.order - b.order)
  )

  // Check if project is multi-agent (Legion)
  const isMultiAgent = (projectId) => {
    const project = projects.value.get(projectId)
    return project?.is_multi_agent || false
  }

  // ========== ACTIONS ==========

  /**
   * Fetch all projects from backend
   */
  async function fetchProjects() {
    try {
      const data = await api.get('/api/projects')

      // Clear and rebuild projects map
      projects.value.clear()
      data.projects.forEach(project => {
        projects.value.set(project.project_id, project)
      })

      console.log(`Loaded ${projects.value.size} projects`)
      return data.projects
    } catch (error) {
      console.error('Failed to fetch projects:', error)
      throw error
    }
  }

  /**
   * Create a new project
   */
  async function createProject(name, workingDirectory, isMultiAgent = false) {
    try {
      const response = await api.post('/api/projects', {
        name,
        working_directory: workingDirectory,
        is_multi_agent: isMultiAgent
      })

      const project = response.project

      // Add to projects map
      projects.value.set(project.project_id, project)

      // Trigger reactivity
      projects.value = new Map(projects.value)

      console.log(`Created project ${project.project_id}`)
      return project
    } catch (error) {
      console.error('Failed to create project:', error)
      throw error
    }
  }

  /**
   * Update project (name, expansion state)
   */
  async function updateProject(projectId, updates) {
    try {
      await api.put(`/api/projects/${projectId}`, updates)

      // Update local cache
      updateProjectLocal(projectId, updates)

      console.log(`Updated project ${projectId}`, updates)
    } catch (error) {
      console.error('Failed to update project:', error)
      throw error
    }
  }

  /**
   * Update project locally (from WebSocket or after API call)
   */
  function updateProjectLocal(projectId, updates) {
    const project = projects.value.get(projectId)
    if (project) {
      Object.assign(project, updates)

      // Trigger reactivity
      projects.value = new Map(projects.value)
    }
  }

  /**
   * Delete a project
   */
  async function deleteProject(projectId) {
    try {
      await api.delete(`/api/projects/${projectId}`)

      // Remove from map
      projects.value.delete(projectId)

      // Trigger reactivity
      projects.value = new Map(projects.value)

      // Clear current project if deleted
      if (currentProjectId.value === projectId) {
        currentProjectId.value = null
      }

      console.log(`Deleted project ${projectId}`)
    } catch (error) {
      console.error('Failed to delete project:', error)
      throw error
    }
  }

  /**
   * Toggle project expansion state
   */
  async function toggleExpansion(projectId) {
    try {
      const response = await api.put(`/api/projects/${projectId}/toggle-expansion`)

      // Update local cache
      const project = projects.value.get(projectId)
      if (project) {
        project.is_expanded = response.is_expanded

        // Trigger reactivity
        projects.value = new Map(projects.value)
      }

      return response.is_expanded
    } catch (error) {
      console.error('Failed to toggle expansion:', error)
      throw error
    }
  }

  /**
   * Reorder projects
   */
  async function reorderProjects(projectIds) {
    try {
      await api.put('/api/projects/reorder', { project_ids: projectIds })

      // Update orders locally
      projectIds.forEach((id, index) => {
        const project = projects.value.get(id)
        if (project) {
          project.order = index
        }
      })

      // Trigger reactivity
      projects.value = new Map(projects.value)

      console.log('Reordered projects', projectIds)
    } catch (error) {
      console.error('Failed to reorder projects:', error)
      throw error
    }
  }

  /**
   * Reorder sessions within a project
   */
  async function reorderSessionsInProject(projectId, sessionIds) {
    try {
      await api.put(`/api/projects/${projectId}/sessions/reorder`, {
        session_ids: sessionIds
      })

      // Update local state
      const project = projects.value.get(projectId)
      if (project) {
        project.session_ids = sessionIds
        // Trigger reactivity
        projects.value = new Map(projects.value)
      }

      console.log(`Reordered sessions in project ${projectId}`, sessionIds)
    } catch (error) {
      console.error('Failed to reorder sessions:', error)
      throw error
    }
  }

  /**
   * Get project by ID
   */
  function getProject(projectId) {
    return projects.value.get(projectId)
  }

  /**
   * Format project path for display (show last 2 segments)
   */
  function formatPath(absolutePath) {
    if (!absolutePath) return ''

    const parts = absolutePath.split(/[/\\]/).filter(p => p)

    if (parts.length === 0) return '/'
    if (parts.length === 1) return `/${parts[0]}`
    if (parts.length === 2) return `/${parts.join('/')}`

    // 3+ folders: show ellipsis + last 2
    return `.../${parts.slice(-2).join('/')}`
  }

  // ========== RETURN ==========
  return {
    // State
    projects,
    currentProjectId,

    // Computed
    currentProject,
    orderedProjects,
    isMultiAgent,

    // Actions
    fetchProjects,
    createProject,
    updateProject,
    updateProjectLocal,
    deleteProject,
    toggleExpansion,
    reorderProjects,
    reorderSessionsInProject,
    getProject,
    formatPath
  }
})
