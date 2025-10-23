import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { api } from '../utils/api'
import { useSessionStore } from './session'

/**
 * Message Store - Manages messages and tool calls per session
 */
export const useMessageStore = defineStore('message', () => {
  // ========== STATE ==========

  // Messages per session (sessionId -> Message[])
  const messagesBySession = ref(new Map())

  // Tool calls per session (sessionId -> ToolCall[])
  const toolCallsBySession = ref(new Map())

  // Tool call manager state (tracking tool lifecycle)
  const toolSignatureToId = ref(new Map())
  const permissionToToolMap = ref(new Map())

  // ========== COMPUTED ==========

  // Current session's messages
  const currentMessages = computed(() => {
    const sessionStore = useSessionStore()
    return messagesBySession.value.get(sessionStore.currentSessionId) || []
  })

  // Current session's tool calls
  const currentToolCalls = computed(() => {
    const sessionStore = useSessionStore()
    return toolCallsBySession.value.get(sessionStore.currentSessionId) || []
  })

  // ========== ACTIONS ==========

  /**
   * Load messages for a session from backend
   */
  async function loadMessages(sessionId, limit = 50, offset = 0) {
    try {
      const data = await api.get(
        `/api/sessions/${sessionId}/messages?limit=${limit}&offset=${offset}`
      )

      // Store messages
      messagesBySession.value.set(sessionId, data.messages || [])

      // Trigger reactivity
      messagesBySession.value = new Map(messagesBySession.value)

      console.log(`Loaded ${data.messages?.length || 0} messages for session ${sessionId}`)
      return data.messages || []
    } catch (error) {
      console.error('Failed to load messages:', error)
      throw error
    }
  }

  /**
   * Add a message to a session (from WebSocket)
   */
  function addMessage(sessionId, message) {
    if (!messagesBySession.value.has(sessionId)) {
      messagesBySession.value.set(sessionId, [])
    }

    const messages = messagesBySession.value.get(sessionId)
    messages.push(message)

    // Trigger reactivity
    messagesBySession.value = new Map(messagesBySession.value)
  }

  /**
   * Add a tool call to a session
   */
  function addToolCall(sessionId, toolCall) {
    if (!toolCallsBySession.value.has(sessionId)) {
      toolCallsBySession.value.set(sessionId, [])
    }

    const toolCalls = toolCallsBySession.value.get(sessionId)
    toolCalls.push(toolCall)

    // Trigger reactivity
    toolCallsBySession.value = new Map(toolCallsBySession.value)
  }

  /**
   * Update a tool call (from WebSocket updates)
   */
  function updateToolCall(sessionId, toolUseId, updates) {
    const toolCalls = toolCallsBySession.value.get(sessionId)
    if (toolCalls) {
      const toolCall = toolCalls.find(tc => tc.id === toolUseId)
      if (toolCall) {
        Object.assign(toolCall, updates)

        // Trigger reactivity
        toolCallsBySession.value = new Map(toolCallsBySession.value)
      }
    }
  }

  /**
   * Handle tool use message (create tool call)
   */
  function handleToolUse(sessionId, toolUseBlock) {
    const signature = createToolSignature(toolUseBlock.name, toolUseBlock.input)
    toolSignatureToId.value.set(signature, toolUseBlock.id)

    const toolCall = {
      id: toolUseBlock.id,
      name: toolUseBlock.name,
      input: toolUseBlock.input,
      signature: signature,
      status: 'pending',
      permissionRequestId: null,
      permissionDecision: null,
      result: null,
      explanation: null,
      timestamp: new Date().toISOString(),
      isExpanded: true
    }

    addToolCall(sessionId, toolCall)
    console.log(`Created tool call ${toolCall.id} for ${toolCall.name}`)
    return toolCall
  }

  /**
   * Handle permission request for a tool
   */
  function handlePermissionRequest(sessionId, permissionRequest) {
    const signature = createToolSignature(
      permissionRequest.tool_name,
      permissionRequest.input_params
    )
    const toolUseId = toolSignatureToId.value.get(signature)

    if (toolUseId) {
      permissionToToolMap.value.set(permissionRequest.request_id, toolUseId)

      updateToolCall(sessionId, toolUseId, {
        status: 'permission_required',
        permissionRequestId: permissionRequest.request_id,
        suggestions: permissionRequest.metadata?.suggestions || []
      })

      console.log(`Permission required for tool ${toolUseId}`)
    }
  }

  /**
   * Handle permission response (user decision)
   */
  function handlePermissionResponse(sessionId, permissionResponse) {
    const toolUseId = permissionToToolMap.value.get(permissionResponse.request_id)

    if (toolUseId) {
      const updates = {
        permissionDecision: permissionResponse.decision,
        appliedUpdates: permissionResponse.applied_updates || []
      }

      if (permissionResponse.decision === 'allow') {
        updates.status = 'executing'
      } else {
        updates.status = 'completed'
        updates.result = {
          error: true,
          message: permissionResponse.reasoning || 'Permission denied'
        }
        updates.isExpanded = false
      }

      updateToolCall(sessionId, toolUseId, updates)
      console.log(`Permission ${permissionResponse.decision} for tool ${toolUseId}`)
    }
  }

  /**
   * Handle tool result (completion)
   */
  function handleToolResult(sessionId, toolResultBlock) {
    const toolUseId = toolResultBlock.tool_use_id

    updateToolCall(sessionId, toolUseId, {
      status: toolResultBlock.is_error ? 'error' : 'completed',
      result: {
        error: toolResultBlock.is_error,
        content: toolResultBlock.content
      },
      isExpanded: false // Auto-collapse on completion
    })

    console.log(`Tool ${toolUseId} ${toolResultBlock.is_error ? 'failed' : 'completed'}`)
  }

  /**
   * Toggle tool call expansion
   */
  function toggleToolExpansion(sessionId, toolUseId) {
    const toolCalls = toolCallsBySession.value.get(sessionId)
    if (toolCalls) {
      const toolCall = toolCalls.find(tc => tc.id === toolUseId)
      if (toolCall) {
        toolCall.isExpanded = !toolCall.isExpanded

        // Trigger reactivity
        toolCallsBySession.value = new Map(toolCallsBySession.value)
      }
    }
  }

  /**
   * Create tool signature for matching (tool name + params hash)
   */
  function createToolSignature(toolName, inputParams) {
    if (!toolName) return 'unknown:{}'
    if (!inputParams || typeof inputParams !== 'object') return `${toolName}:{}`

    const paramsHash = JSON.stringify(inputParams, Object.keys(inputParams).sort())
    return `${toolName}:${paramsHash}`
  }

  /**
   * Clear messages for a session (for reset)
   */
  function clearMessages(sessionId) {
    messagesBySession.value.delete(sessionId)
    toolCallsBySession.value.delete(sessionId)

    // Trigger reactivity
    messagesBySession.value = new Map(messagesBySession.value)
    toolCallsBySession.value = new Map(toolCallsBySession.value)
  }

  // ========== RETURN ==========
  return {
    // State
    messagesBySession,
    toolCallsBySession,

    // Computed
    currentMessages,
    currentToolCalls,

    // Actions
    loadMessages,
    addMessage,
    addToolCall,
    updateToolCall,
    handleToolUse,
    handlePermissionRequest,
    handlePermissionResponse,
    handleToolResult,
    toggleToolExpansion,
    clearMessages
  }
})
