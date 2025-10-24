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
  async function loadMessages(sessionId, limit = null, offset = 0) {
    try {
      // If no limit specified, request all messages
      // Use a very high limit to get all messages in one request
      const effectiveLimit = limit || 10000

      const data = await api.get(
        `/api/sessions/${sessionId}/messages?limit=${effectiveLimit}&offset=${offset}`
      )

      const messages = data.messages || []
      const totalCount = data.total_count || messages.length
      const hasMore = data.has_more || false

      console.log(`Loaded ${messages.length} of ${totalCount} messages for session ${sessionId}`)

      // Store messages
      messagesBySession.value.set(sessionId, messages)

      // Process messages to extract tool uses, results, and init data
      messages.forEach(message => {
        // Extract tool uses from assistant messages
        if (message.metadata?.has_tool_uses && message.metadata.tool_uses) {
          message.metadata.tool_uses.forEach(toolUse => {
            handleToolUse(sessionId, toolUse)
          })
        }

        // Extract tool results from user messages
        if (message.metadata?.has_tool_results && message.metadata.tool_results) {
          message.metadata.tool_results.forEach(toolResult => {
            handleToolResult(sessionId, toolResult)
          })
        }

        // Capture init data for session info modal
        if (message.type === 'system' &&
            (message.subtype === 'init' || message.metadata?.subtype === 'init') &&
            message.metadata?.init_data) {
          const sessionStore = useSessionStore()
          sessionStore.storeInitData(sessionId, message.metadata.init_data)
        }
      })

      // Trigger reactivity
      messagesBySession.value = new Map(messagesBySession.value)

      // Warn if there are more messages we didn't load
      if (hasMore) {
        console.warn(`Warning: Only loaded ${messages.length} of ${totalCount} messages. Some messages may be missing.`)
      }

      return { messages, totalCount, hasMore }
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
    console.log('handlePermissionRequest received:', permissionRequest)

    // Extract fields from metadata or top level
    const toolName = permissionRequest.metadata?.tool_name || permissionRequest.tool_name
    const inputParams = permissionRequest.metadata?.input_params || permissionRequest.input_params
    const requestId = permissionRequest.metadata?.request_id || permissionRequest.request_id
    const suggestions = permissionRequest.metadata?.suggestions || permissionRequest.suggestions || []

    console.log('Extracted data:', { toolName, inputParams, requestId, suggestions })

    const signature = createToolSignature(toolName, inputParams)
    const toolUseId = toolSignatureToId.value.get(signature)

    if (toolUseId) {
      permissionToToolMap.value.set(requestId, toolUseId)

      updateToolCall(sessionId, toolUseId, {
        status: 'permission_required',
        permissionRequestId: requestId,
        suggestions: suggestions
      })

      console.log(`Permission required for tool ${toolUseId}`, { suggestions })
    } else {
      console.warn('No tool use ID found for permission request signature:', signature, {
        toolName,
        inputParams,
        availableSignatures: Array.from(toolSignatureToId.value.keys())
      })
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

    // Note: ExitPlanMode mode reset logic is handled by backend to prevent race conditions
    // with multiple connected frontends. Backend tracks setMode suggestions and makes the
    // decision to reset or not.
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
