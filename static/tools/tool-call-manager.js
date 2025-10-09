/**
 * Tool Call Manager for handling tool use lifecycle
 * Manages tool call state, permission requests, and results
 */
class ToolCallManager {
    constructor() {
        this.toolCalls = new Map(); // tool_use_id -> ToolCallState
        this.toolSignatureToId = new Map(); // "toolName:hash(params)" -> tool_use_id
        this.permissionToToolMap = new Map(); // permission_request_id -> tool_use_id
        this.assistantTurnToolGroups = new Map(); // assistant_message_timestamp -> [tool_use_ids]
    }

    createToolSignature(toolName, inputParams) {
        // Create unique signature from tool name + params
        if (!toolName) {
            Logger.error('TOOL_MANAGER', 'createToolSignature called with undefined toolName', {toolName, inputParams});
            return 'unknown:{}';
        }
        if (!inputParams || typeof inputParams !== 'object') {
            Logger.warn('TOOL_MANAGER', 'createToolSignature called with invalid inputParams', {toolName, inputParams});
            return `${toolName}:{}`;
        }
        const paramsHash = JSON.stringify(inputParams, Object.keys(inputParams).sort());
        return `${toolName}:${paramsHash}`;
    }

    handleToolUse(toolUseBlock) {
        Logger.debug('TOOL_MANAGER', 'Handling tool use', toolUseBlock);

        const signature = this.createToolSignature(toolUseBlock.name, toolUseBlock.input);
        this.toolSignatureToId.set(signature, toolUseBlock.id);

        // Create tool call state
        const toolCallState = {
            id: toolUseBlock.id,
            name: toolUseBlock.name,
            input: toolUseBlock.input,
            signature: signature,
            status: 'pending', // pending, permission_required, executing, completed, error
            permissionRequestId: null,
            permissionDecision: null,
            result: null,
            explanation: null,
            timestamp: new Date().toISOString(),
            isExpanded: true // Start expanded, can be collapsed later
        };

        this.toolCalls.set(toolUseBlock.id, toolCallState);
        return toolCallState;
    }

    handlePermissionRequest(permissionRequest) {
        Logger.debug('TOOL_MANAGER', 'Handling permission request', permissionRequest);

        const signature = this.createToolSignature(permissionRequest.tool_name, permissionRequest.input_params);
        const toolUseId = this.toolSignatureToId.get(signature);

        if (toolUseId) {
            this.permissionToToolMap.set(permissionRequest.request_id, toolUseId);

            // Update tool state
            const toolCall = this.toolCalls.get(toolUseId);
            if (toolCall) {
                toolCall.status = 'permission_required';
                toolCall.permissionRequestId = permissionRequest.request_id;
                return toolCall;
            }
        } else {
            // Handle historical/unknown tools gracefully
            Logger.debug('TOOL_MANAGER', 'Creating historical tool call for unknown tool', permissionRequest);

            // Generate a unique ID for this historical tool call
            const historicalId = `historical_${permissionRequest.request_id}`;

            // Create a basic tool call record for historical tools
            const historicalToolCall = {
                id: historicalId,
                name: permissionRequest.tool_name,
                input: permissionRequest.input_params,
                signature: signature,
                status: 'permission_required',
                permissionRequestId: permissionRequest.request_id,
                permissionDecision: null,
                result: null,
                explanation: null,
                timestamp: new Date().toISOString(),
                isExpanded: false,
                isHistorical: true  // Flag to indicate this is a historical tool call
            };

            // Store the historical tool call
            this.toolCalls.set(historicalId, historicalToolCall);
            this.permissionToToolMap.set(permissionRequest.request_id, historicalId);

            return historicalToolCall;
        }
        return null;
    }

    handlePermissionResponse(permissionResponse) {
        Logger.debug('TOOL_MANAGER', 'Handling permission response', permissionResponse);

        const toolUseId = this.permissionToToolMap.get(permissionResponse.request_id);
        if (toolUseId) {
            const toolCall = this.toolCalls.get(toolUseId);
            if (toolCall) {
                toolCall.permissionDecision = permissionResponse.decision;

                if (permissionResponse.decision === 'allow') {
                    toolCall.status = 'executing';
                } else {
                    toolCall.status = 'completed';
                    toolCall.result = { error: true, message: permissionResponse.reasoning || 'Permission denied' };
                    // Auto-collapse tool call when permission is denied
                    toolCall.isExpanded = false;
                }

                return toolCall;
            }
        }
        return null;
    }

    handleToolResult(toolResultBlock) {
        Logger.debug('TOOL_MANAGER', 'Handling tool result', toolResultBlock);

        const toolUseId = toolResultBlock.tool_use_id;
        const toolCall = this.toolCalls.get(toolUseId);

        if (toolCall) {
            toolCall.status = toolResultBlock.is_error ? 'error' : 'completed';
            toolCall.result = {
                error: toolResultBlock.is_error,
                content: toolResultBlock.content
            };

            // Auto-collapse tool call when completed
            toolCall.isExpanded = false;

            return toolCall;
        }
        return null;
    }

    handleAssistantExplanation(assistantMessage, relatedToolIds) {
        Logger.debug('TOOL_MANAGER', 'Handling assistant explanation', {assistantMessage, relatedToolIds});

        // Update explanation for related tools
        relatedToolIds.forEach(toolId => {
            const toolCall = this.toolCalls.get(toolId);
            if (toolCall) {
                toolCall.explanation = assistantMessage.content;
            }
        });
    }

    getToolCall(toolUseId) {
        return this.toolCalls.get(toolUseId);
    }

    getAllToolCalls() {
        return Array.from(this.toolCalls.values());
    }

    toggleToolExpansion(toolUseId) {
        const toolCall = this.toolCalls.get(toolUseId);
        if (toolCall) {
            toolCall.isExpanded = !toolCall.isExpanded;
            return toolCall;
        }
        return null;
    }

    setToolExpansion(toolUseId, isExpanded) {
        const toolCall = this.toolCalls.get(toolUseId);
        if (toolCall) {
            toolCall.isExpanded = isExpanded;
            return toolCall;
        }
        return null;
    }

    generateCollapsedSummary(toolCall) {
        const statusIcon = {
            'pending': '🔄',
            'permission_required': '❓',
            'executing': '⚡',
            'completed': toolCall.permissionDecision === 'deny' ? '❌' : '✅',
            'error': '💥'
        }[toolCall.status] || '🔧';

        const statusText = {
            'pending': 'Pending',
            'permission_required': 'Awaiting Permission',
            'executing': 'Executing',
            'completed': toolCall.permissionDecision === 'deny' ? 'Denied' : 'Completed',
            'error': 'Error'
        }[toolCall.status] || 'Unknown';

        // Create parameter summary
        const paramSummary = this.createParameterSummary(toolCall.input);

        return `${statusIcon} ${toolCall.name}${paramSummary} - ${statusText}`;
    }

    createParameterSummary(input) {
        if (!input || Object.keys(input).length === 0) return '';

        // Show key parameters in a readable format
        const keys = Object.keys(input);
        if (keys.length === 1) {
            const value = input[keys[0]];
            const truncated = typeof value === 'string' && value.length > 30 ?
                value.substring(0, 30) + '...' : value;
            return ` (${keys[0]}="${truncated}")`;
        } else if (keys.length <= 3) {
            const pairs = keys.map(key => {
                const value = input[key];
                const truncated = typeof value === 'string' && value.length > 15 ?
                    value.substring(0, 15) + '...' : value;
                return `${key}="${truncated}"`;
            });
            return ` (${pairs.join(', ')})`;
        } else {
            return ` (${keys.length} parameters)`;
        }
    }
}
