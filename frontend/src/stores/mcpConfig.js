import { defineStore } from 'pinia'
import { ref } from 'vue'
import { api } from '../utils/api'

/**
 * MCP Config Store - Manages global MCP server configurations (Issue #676)
 *
 * Handles:
 * - CRUD operations for global MCP server definitions
 * - Loading configs for picker components
 */
export const useMcpConfigStore = defineStore('mcpConfig', () => {
  // ========== STATE ==========

  // All configs by ID
  const configs = ref(new Map())

  // Loading state
  const loading = ref(false)

  // ========== GETTERS ==========

  function configList() {
    return Array.from(configs.value.values())
  }

  function getConfig(id) {
    return configs.value.get(id)
  }

  // ========== ACTIONS ==========

  async function fetchConfigs() {
    loading.value = true
    try {
      const data = await api.get('/api/mcp-configs')
      configs.value = new Map()
      for (const config of data) {
        configs.value.set(config.id, config)
      }
      configs.value = new Map(configs.value)
    } catch (error) {
      console.error('Failed to fetch MCP configs:', error)
    } finally {
      loading.value = false
    }
  }

  async function createConfig(configData) {
    try {
      const config = await api.post('/api/mcp-configs', configData)
      configs.value.set(config.id, config)
      configs.value = new Map(configs.value)
      return config
    } catch (error) {
      console.error('Failed to create MCP config:', error)
      throw error
    }
  }

  async function updateConfig(configId, configData) {
    try {
      const config = await api.put(`/api/mcp-configs/${configId}`, configData)
      configs.value.set(config.id, config)
      configs.value = new Map(configs.value)
      return config
    } catch (error) {
      console.error('Failed to update MCP config:', error)
      throw error
    }
  }

  async function deleteConfig(configId) {
    try {
      await api.delete(`/api/mcp-configs/${configId}`)
      configs.value.delete(configId)
      configs.value = new Map(configs.value)
    } catch (error) {
      console.error('Failed to delete MCP config:', error)
      throw error
    }
  }

  return {
    configs,
    loading,
    configList,
    getConfig,
    fetchConfigs,
    createConfig,
    updateConfig,
    deleteConfig,
  }
})
