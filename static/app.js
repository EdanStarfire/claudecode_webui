// Claude Code WebUI JavaScript Application

// Tool Call Manager for handling tool use lifecycle
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
            console.error('createToolSignature called with undefined toolName:', toolName, inputParams);
            return 'unknown:{}';
        }
        if (!inputParams || typeof inputParams !== 'object') {
            console.warn('createToolSignature called with invalid inputParams:', toolName, inputParams);
            return `${toolName}:{}`;
        }
        const paramsHash = JSON.stringify(inputParams, Object.keys(inputParams).sort());
        return `${toolName}:${paramsHash}`;
    }

    handleToolUse(toolUseBlock) {
        console.log('ToolCallManager: Handling tool use', toolUseBlock);

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
        console.log('ToolCallManager: Handling permission request', permissionRequest);

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
            console.debug('ToolCallManager: Creating historical tool call for unknown tool', permissionRequest);

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
        console.log('ToolCallManager: Handling permission response', permissionResponse);

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
        console.log('ToolCallManager: Handling tool result', toolResultBlock);

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
        console.log('ToolCallManager: Handling assistant explanation', assistantMessage, relatedToolIds);

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

// Tool Handler Registry for custom tool display rendering
class ToolHandlerRegistry {
    constructor() {
        this.handlers = new Map(); // toolName -> handler
        this.patternHandlers = []; // Array of {pattern: RegExp, handler}
    }

    /**
     * Register a handler for a specific tool name
     * @param {string} toolName - Name of the tool (e.g., "Read", "Edit")
     * @param {Object} handler - Handler object with render methods
     */
    registerHandler(toolName, handler) {
        this.handlers.set(toolName, handler);
    }

    /**
     * Register a handler for tools matching a pattern
     * @param {RegExp|string} pattern - Pattern to match tool names (e.g., /^mcp__/, "mcp__*")
     * @param {Object} handler - Handler object with render methods
     */
    registerPatternHandler(pattern, handler) {
        const regex = typeof pattern === 'string'
            ? new RegExp('^' + pattern.replace(/\*/g, '.*') + '$')
            : pattern;
        this.patternHandlers.push({ pattern: regex, handler });
    }

    /**
     * Get handler for a specific tool name
     * @param {string} toolName - Name of the tool
     * @returns {Object|null} Handler object or null if no handler found
     */
    getHandler(toolName) {
        // Check exact match first
        if (this.handlers.has(toolName)) {
            return this.handlers.get(toolName);
        }

        // Check pattern matches
        for (const { pattern, handler } of this.patternHandlers) {
            if (pattern.test(toolName)) {
                return handler;
            }
        }

        return null;
    }

    /**
     * Check if a handler exists for a tool
     * @param {string} toolName - Name of the tool
     * @returns {boolean}
     */
    hasHandler(toolName) {
        return this.getHandler(toolName) !== null;
    }
}

// Default Tool Handler - provides standard rendering
class DefaultToolHandler {
    renderParameters(toolCall, escapeHtmlFn) {
        return `
            <div class="tool-parameters">
                <strong>Parameters:</strong>
                <pre class="tool-params-json">${escapeHtmlFn(JSON.stringify(toolCall.input, null, 2))}</pre>
            </div>
        `;
    }

    renderResult(toolCall, escapeHtmlFn) {
        if (!toolCall.result) return '';

        const resultClass = toolCall.result.error ? 'tool-result-error' : 'tool-result-success';
        return `
            <div class="tool-result ${resultClass}">
                <strong>Result:</strong>
                <pre class="tool-result-content">${escapeHtmlFn(toolCall.result.content || toolCall.result.message || 'No content')}</pre>
            </div>
        `;
    }

    getCollapsedSummary(toolCall) {
        // Return null to use default summary generation
        return null;
    }
}

// Read Tool Handler - custom display for Read tool
class ReadToolHandler {
    renderParameters(toolCall, escapeHtmlFn) {
        const filePath = toolCall.input.file_path || 'Unknown';
        const offset = toolCall.input.offset;
        const limit = toolCall.input.limit;

        let rangeInfo = '';
        if (offset !== undefined || limit !== undefined) {
            const startLine = offset !== undefined ? offset + 1 : 1;
            const endLine = limit !== undefined ? (offset || 0) + limit : '∞';
            rangeInfo = `<span class="read-range">Lines ${startLine}-${endLine}</span>`;
        }

        return `
            <div class="tool-parameters tool-read-params">
                <div class="read-file-path">
                    <span class="read-icon">📄</span>
                    <strong>Reading:</strong>
                    <code class="file-path">${escapeHtmlFn(filePath)}</code>
                    ${rangeInfo}
                </div>
            </div>
        `;
    }

    renderResult(toolCall, escapeHtmlFn) {
        if (!toolCall.result) return '';

        const resultClass = toolCall.result.error ? 'tool-result-error' : 'tool-result-success';

        if (toolCall.result.error) {
            return `
                <div class="tool-result ${resultClass}">
                    <strong>Error:</strong>
                    <pre class="tool-result-content">${escapeHtmlFn(toolCall.result.content || toolCall.result.message || 'No content')}</pre>
                </div>
            `;
        }

        // Parse file content and show preview
        const content = toolCall.result.content || '';
        const lines = content.split('\n');
        const previewLimit = 20;
        const hasMore = lines.length > previewLimit;
        const previewLines = lines.slice(0, previewLimit);

        return `
            <div class="tool-result ${resultClass}">
                <strong>Content Preview:</strong>
                <div class="read-result-header">
                    <span class="read-line-count">${lines.length} lines</span>
                    ${hasMore ? `<span class="read-preview-note">(showing first ${previewLimit})</span>` : ''}
                </div>
                <pre class="tool-result-content read-content-preview">${escapeHtmlFn(previewLines.join('\n'))}</pre>
                ${hasMore ? '<div class="read-more-indicator">...</div>' : ''}
            </div>
        `;
    }

    getCollapsedSummary(toolCall) {
        const statusIcon = {
            'pending': '🔄',
            'permission_required': '❓',
            'executing': '⚡',
            'completed': toolCall.permissionDecision === 'deny' ? '❌' : '✅',
            'error': '💥'
        }[toolCall.status] || '🔧';

        const filePath = toolCall.input.file_path || 'Unknown';
        const fileName = filePath.split(/[/\\]/).pop();

        let statusText = '';
        if (toolCall.status === 'completed' && !toolCall.result?.error) {
            const lines = (toolCall.result?.content || '').split('\n').length;
            statusText = `${lines} lines`;
        } else if (toolCall.result?.error) {
            statusText = 'Error';
        } else {
            statusText = {
                'pending': 'Pending',
                'permission_required': 'Awaiting Permission',
                'executing': 'Executing',
                'completed': 'Completed',
                'error': 'Error'
            }[toolCall.status] || 'Unknown';
        }

        return `${statusIcon} Read: ${fileName} - ${statusText}`;
    }
}

// Edit Tool Handler - custom display for Edit tool with diff view
class EditToolHandler {
    renderParameters(toolCall, escapeHtmlFn) {
        const filePath = toolCall.input.file_path || 'Unknown';
        const oldString = toolCall.input.old_string || '';
        const newString = toolCall.input.new_string || '';
        const replaceAll = toolCall.input.replace_all || false;

        // Generate diff view
        const diffHtml = this.generateDiffView(oldString, newString, escapeHtmlFn);

        return `
            <div class="tool-parameters tool-edit-params">
                <div class="edit-file-path">
                    <span class="edit-icon">✏️</span>
                    <strong>Editing:</strong>
                    <code class="file-path">${escapeHtmlFn(filePath)}</code>
                    ${replaceAll ? '<span class="edit-replace-all-badge">Replace All</span>' : ''}
                </div>
                <div class="edit-diff-container">
                    <div class="edit-diff-header">
                        <span class="diff-label">Changes:</span>
                    </div>
                    ${diffHtml}
                </div>
            </div>
        `;
    }

    generateDiffView(oldString, newString, escapeHtmlFn) {
        // Split into lines for line-by-line comparison
        const oldLines = oldString.split('\n');
        const newLines = newString.split('\n');

        // Simple line-by-line diff (not a full LCS algorithm, but good enough for display)
        const maxLines = Math.max(oldLines.length, newLines.length);
        let diffHtml = '<div class="diff-view">';

        for (let i = 0; i < maxLines; i++) {
            const oldLine = i < oldLines.length ? oldLines[i] : null;
            const newLine = i < newLines.length ? newLines[i] : null;

            if (oldLine !== null && newLine !== null) {
                if (oldLine === newLine) {
                    // Unchanged line
                    diffHtml += `<div class="diff-line diff-unchanged">
                        <span class="diff-marker"> </span>
                        <span class="diff-content">${escapeHtmlFn(oldLine)}</span>
                    </div>`;
                } else {
                    // Changed line - show both old and new
                    diffHtml += `<div class="diff-line diff-removed">
                        <span class="diff-marker">-</span>
                        <span class="diff-content">${escapeHtmlFn(oldLine)}</span>
                    </div>`;
                    diffHtml += `<div class="diff-line diff-added">
                        <span class="diff-marker">+</span>
                        <span class="diff-content">${escapeHtmlFn(newLine)}</span>
                    </div>`;
                }
            } else if (oldLine !== null) {
                // Line removed
                diffHtml += `<div class="diff-line diff-removed">
                    <span class="diff-marker">-</span>
                    <span class="diff-content">${escapeHtmlFn(oldLine)}</span>
                </div>`;
            } else if (newLine !== null) {
                // Line added
                diffHtml += `<div class="diff-line diff-added">
                    <span class="diff-marker">+</span>
                    <span class="diff-content">${escapeHtmlFn(newLine)}</span>
                </div>`;
            }
        }

        diffHtml += '</div>';
        return diffHtml;
    }

    renderResult(toolCall, escapeHtmlFn) {
        if (!toolCall.result) return '';

        const resultClass = toolCall.result.error ? 'tool-result-error' : 'tool-result-success';

        if (toolCall.result.error) {
            return `
                <div class="tool-result ${resultClass}">
                    <strong>Error:</strong>
                    <pre class="tool-result-content">${escapeHtmlFn(toolCall.result.content || toolCall.result.message || 'No content')}</pre>
                </div>
            `;
        }

        // Success message
        return `
            <div class="tool-result ${resultClass}">
                <div class="edit-success-message">
                    <span class="success-icon">✅</span>
                    <strong>File edited successfully</strong>
                </div>
            </div>
        `;
    }

    getCollapsedSummary(toolCall) {
        const statusIcon = {
            'pending': '🔄',
            'permission_required': '❓',
            'executing': '⚡',
            'completed': toolCall.permissionDecision === 'deny' ? '❌' : '✅',
            'error': '💥'
        }[toolCall.status] || '🔧';

        const filePath = toolCall.input.file_path || 'Unknown';
        const fileName = filePath.split(/[/\\]/).pop();

        // Count lines changed
        const oldLines = (toolCall.input.old_string || '').split('\n').length;
        const newLines = (toolCall.input.new_string || '').split('\n').length;
        const linesChanged = Math.max(oldLines, newLines);

        let statusText = '';
        if (toolCall.status === 'completed' && !toolCall.result?.error) {
            statusText = `${linesChanged} lines changed`;
        } else if (toolCall.result?.error) {
            statusText = 'Error';
        } else {
            statusText = {
                'pending': 'Pending',
                'permission_required': 'Awaiting Permission',
                'executing': 'Executing',
                'completed': 'Completed',
                'error': 'Error'
            }[toolCall.status] || 'Unknown';
        }

        return `${statusIcon} Edit: ${fileName} - ${statusText}`;
    }
}

// MultiEdit Tool Handler - custom display for MultiEdit tool with multiple diffs
class MultiEditToolHandler {
    renderParameters(toolCall, escapeHtmlFn) {
        const filePath = toolCall.input.file_path || 'Unknown';
        const edits = toolCall.input.edits || [];

        // Generate diff view for each edit
        const editsHtml = edits.map((edit, index) => {
            const diffHtml = this.generateDiffView(edit.old_string || '', edit.new_string || '', escapeHtmlFn);
            return `
                <div class="multiedit-edit-block">
                    <div class="multiedit-edit-header">
                        <span class="multiedit-edit-label">Edit ${index + 1} of ${edits.length}</span>
                    </div>
                    ${diffHtml}
                </div>
            `;
        }).join('');

        return `
            <div class="tool-parameters tool-edit-params">
                <div class="edit-file-path">
                    <span class="edit-icon">✏️</span>
                    <strong>Multi-Editing:</strong>
                    <code class="file-path">${escapeHtmlFn(filePath)}</code>
                    <span class="multiedit-count-badge">${edits.length} edits</span>
                </div>
                <div class="edit-diff-container">
                    ${editsHtml}
                </div>
            </div>
        `;
    }

    generateDiffView(oldString, newString, escapeHtmlFn) {
        // Reuse the same diff generation logic as EditToolHandler
        const oldLines = oldString.split('\n');
        const newLines = newString.split('\n');

        const maxLines = Math.max(oldLines.length, newLines.length);
        let diffHtml = '<div class="diff-view">';

        for (let i = 0; i < maxLines; i++) {
            const oldLine = i < oldLines.length ? oldLines[i] : null;
            const newLine = i < newLines.length ? newLines[i] : null;

            if (oldLine !== null && newLine !== null) {
                if (oldLine === newLine) {
                    // Unchanged line
                    diffHtml += `<div class="diff-line diff-unchanged">
                        <span class="diff-marker"> </span>
                        <span class="diff-content">${escapeHtmlFn(oldLine)}</span>
                    </div>`;
                } else {
                    // Changed line - show both old and new
                    diffHtml += `<div class="diff-line diff-removed">
                        <span class="diff-marker">-</span>
                        <span class="diff-content">${escapeHtmlFn(oldLine)}</span>
                    </div>`;
                    diffHtml += `<div class="diff-line diff-added">
                        <span class="diff-marker">+</span>
                        <span class="diff-content">${escapeHtmlFn(newLine)}</span>
                    </div>`;
                }
            } else if (oldLine !== null) {
                // Line removed
                diffHtml += `<div class="diff-line diff-removed">
                    <span class="diff-marker">-</span>
                    <span class="diff-content">${escapeHtmlFn(oldLine)}</span>
                </div>`;
            } else if (newLine !== null) {
                // Line added
                diffHtml += `<div class="diff-line diff-added">
                    <span class="diff-marker">+</span>
                    <span class="diff-content">${escapeHtmlFn(newLine)}</span>
                </div>`;
            }
        }

        diffHtml += '</div>';
        return diffHtml;
    }

    renderResult(toolCall, escapeHtmlFn) {
        if (!toolCall.result) return '';

        const resultClass = toolCall.result.error ? 'tool-result-error' : 'tool-result-success';

        if (toolCall.result.error) {
            return `
                <div class="tool-result ${resultClass}">
                    <strong>Error:</strong>
                    <pre class="tool-result-content">${escapeHtmlFn(toolCall.result.content || toolCall.result.message || 'No content')}</pre>
                </div>
            `;
        }

        // Success message
        const editCount = (toolCall.input.edits || []).length;
        return `
            <div class="tool-result ${resultClass}">
                <div class="edit-success-message">
                    <span class="success-icon">✅</span>
                    <strong>${editCount} edits applied successfully</strong>
                </div>
            </div>
        `;
    }

    getCollapsedSummary(toolCall) {
        const statusIcon = {
            'pending': '🔄',
            'permission_required': '❓',
            'executing': '⚡',
            'completed': toolCall.permissionDecision === 'deny' ? '❌' : '✅',
            'error': '💥'
        }[toolCall.status] || '🔧';

        const filePath = toolCall.input.file_path || 'Unknown';
        const fileName = filePath.split(/[/\\]/).pop();
        const editCount = (toolCall.input.edits || []).length;

        let statusText = '';
        if (toolCall.status === 'completed' && !toolCall.result?.error) {
            statusText = `${editCount} edits`;
        } else if (toolCall.result?.error) {
            statusText = 'Error';
        } else {
            statusText = {
                'pending': 'Pending',
                'permission_required': 'Awaiting Permission',
                'executing': 'Executing',
                'completed': 'Completed',
                'error': 'Error'
            }[toolCall.status] || 'Unknown';
        }

        return `${statusIcon} MultiEdit: ${fileName} - ${statusText}`;
    }
}

// Write Tool Handler - custom display for Write tool (creating new files)
class WriteToolHandler {
    renderParameters(toolCall, escapeHtmlFn) {
        const filePath = toolCall.input.file_path || 'Unknown';
        const content = toolCall.input.content || '';
        const lines = content.split('\n');
        const previewLimit = 20;
        const hasMore = lines.length > previewLimit;
        const previewLines = lines.slice(0, previewLimit);

        return `
            <div class="tool-parameters tool-write-params">
                <div class="write-file-path">
                    <span class="write-icon">📝</span>
                    <strong>Writing new file:</strong>
                    <code class="file-path">${escapeHtmlFn(filePath)}</code>
                </div>
                <div class="write-content-container">
                    <div class="write-content-header">
                        <span class="write-label">Content:</span>
                        <span class="write-line-count">${lines.length} lines</span>
                        ${hasMore ? `<span class="write-preview-note">(showing first ${previewLimit})</span>` : ''}
                    </div>
                    <pre class="write-content-preview">${escapeHtmlFn(previewLines.join('\n'))}</pre>
                    ${hasMore ? '<div class="write-more-indicator">...</div>' : ''}
                </div>
            </div>
        `;
    }

    renderResult(toolCall, escapeHtmlFn) {
        if (!toolCall.result) return '';

        const resultClass = toolCall.result.error ? 'tool-result-error' : 'tool-result-success';

        if (toolCall.result.error) {
            return `
                <div class="tool-result ${resultClass}">
                    <strong>Error:</strong>
                    <pre class="tool-result-content">${escapeHtmlFn(toolCall.result.content || toolCall.result.message || 'No content')}</pre>
                </div>
            `;
        }

        // Success message
        const lines = (toolCall.input.content || '').split('\n').length;
        return `
            <div class="tool-result ${resultClass}">
                <div class="write-success-message">
                    <span class="success-icon">✅</span>
                    <strong>File created successfully (${lines} lines written)</strong>
                </div>
            </div>
        `;
    }

    getCollapsedSummary(toolCall) {
        const statusIcon = {
            'pending': '🔄',
            'permission_required': '❓',
            'executing': '⚡',
            'completed': toolCall.permissionDecision === 'deny' ? '❌' : '✅',
            'error': '💥'
        }[toolCall.status] || '🔧';

        const filePath = toolCall.input.file_path || 'Unknown';
        const fileName = filePath.split(/[/\\]/).pop();

        let statusText = '';
        if (toolCall.status === 'completed' && !toolCall.result?.error) {
            const lines = (toolCall.input.content || '').split('\n').length;
            statusText = `${lines} lines`;
        } else if (toolCall.result?.error) {
            statusText = 'Error';
        } else {
            statusText = {
                'pending': 'Pending',
                'permission_required': 'Awaiting Permission',
                'executing': 'Executing',
                'completed': 'Completed',
                'error': 'Error'
            }[toolCall.status] || 'Unknown';
        }

        return `${statusIcon} Write: ${fileName} - ${statusText}`;
    }
}

// TodoWrite Tool Handler - custom display for TodoWrite tool (task tracking)
class TodoWriteToolHandler {
    renderParameters(toolCall, escapeHtmlFn) {
        const todos = toolCall.input.todos || [];

        // Count todos by status
        const pending = todos.filter(t => t.status === 'pending').length;
        const inProgress = todos.filter(t => t.status === 'in_progress').length;
        const completed = todos.filter(t => t.status === 'completed').length;

        // Generate checklist
        const checklistHtml = todos.map((todo, index) => {
            const checkboxIcon = {
                'pending': '☐',
                'in_progress': '◐',
                'completed': '☑'
            }[todo.status] || '☐';

            const itemClass = `todo-item todo-${todo.status}`;

            return `
                <div class="${itemClass}">
                    <span class="todo-checkbox">${checkboxIcon}</span>
                    <span class="todo-content">${escapeHtmlFn(todo.content)}</span>
                </div>
            `;
        }).join('');

        return `
            <div class="tool-parameters tool-todo-params">
                <div class="todo-header">
                    <span class="todo-icon">📋</span>
                    <strong>Task List:</strong>
                    <div class="todo-summary">
                        ${completed > 0 ? `<span class="todo-count todo-count-completed">${completed} completed</span>` : ''}
                        ${inProgress > 0 ? `<span class="todo-count todo-count-in-progress">${inProgress} in progress</span>` : ''}
                        ${pending > 0 ? `<span class="todo-count todo-count-pending">${pending} pending</span>` : ''}
                    </div>
                </div>
                <div class="todo-checklist">
                    ${checklistHtml}
                </div>
            </div>
        `;
    }

    renderResult(toolCall, escapeHtmlFn) {
        if (!toolCall.result) return '';

        const resultClass = toolCall.result.error ? 'tool-result-error' : 'tool-result-success';

        if (toolCall.result.error) {
            return `
                <div class="tool-result ${resultClass}">
                    <strong>Error:</strong>
                    <pre class="tool-result-content">${escapeHtmlFn(toolCall.result.content || toolCall.result.message || 'No content')}</pre>
                </div>
            `;
        }

        // Success message
        const todoCount = (toolCall.input.todos || []).length;
        return `
            <div class="tool-result ${resultClass}">
                <div class="todo-success-message">
                    <span class="success-icon">✅</span>
                    <strong>Task list updated (${todoCount} tasks)</strong>
                </div>
            </div>
        `;
    }

    getCollapsedSummary(toolCall) {
        const statusIcon = {
            'pending': '🔄',
            'permission_required': '❓',
            'executing': '⚡',
            'completed': toolCall.permissionDecision === 'deny' ? '❌' : '✅',
            'error': '💥'
        }[toolCall.status] || '🔧';

        const todos = toolCall.input.todos || [];
        const completed = todos.filter(t => t.status === 'completed').length;
        const pending = todos.filter(t => t.status === 'pending').length;
        const inProgressTodos = todos.filter(t => t.status === 'in_progress');
        const total = todos.length;

        let statusText = '';
        if (toolCall.status === 'completed' && !toolCall.result?.error) {
            // Build status text with counts
            const parts = [];
            if (completed > 0) parts.push(`${completed} completed`);
            if (pending > 0) parts.push(`${pending} pending`);
            statusText = parts.join(', ');

            // Add in-progress tasks if any
            if (inProgressTodos.length > 0) {
                const inProgressText = inProgressTodos.map(t => `◐ ${t.content}`).join(' | ');
                statusText = statusText ? `${statusText} | ${inProgressText}` : inProgressText;
            }
        } else if (toolCall.result?.error) {
            statusText = 'Error';
        } else {
            statusText = {
                'pending': 'Pending',
                'permission_required': 'Awaiting Permission',
                'executing': 'Executing',
                'completed': 'Completed',
                'error': 'Error'
            }[toolCall.status] || 'Unknown';
        }

        return `${statusIcon} TodoWrite: ${statusText}`;
    }
}

// Grep Tool Handler - custom display for Grep tool
class GrepToolHandler {
    renderParameters(toolCall, escapeHtmlFn) {
        const pattern = toolCall.input.pattern || '';
        const path = toolCall.input.path || '.';
        const outputMode = toolCall.input.output_mode || 'files_with_matches';
        const caseInsensitive = toolCall.input['-i'] || false;
        const showLineNumbers = toolCall.input['-n'] || false;
        const contextAfter = toolCall.input['-A'];
        const contextBefore = toolCall.input['-B'];
        const contextBoth = toolCall.input['-C'];
        const glob = toolCall.input.glob || null;
        const type = toolCall.input.type || null;
        const headLimit = toolCall.input.head_limit;
        const multiline = toolCall.input.multiline || false;

        let searchInfo = `<span class="grep-pattern"><code>${escapeHtmlFn(pattern)}</code></span>`;

        // Build flags list
        const flags = [];
        if (caseInsensitive) flags.push('case-insensitive');
        if (showLineNumbers) flags.push('line numbers');
        if (multiline) flags.push('multiline');
        if (contextBoth !== undefined) flags.push(`±${contextBoth} lines context`);
        else {
            if (contextBefore !== undefined) flags.push(`-${contextBefore} lines before`);
            if (contextAfter !== undefined) flags.push(`+${contextAfter} lines after`);
        }
        if (headLimit !== undefined) flags.push(`limit ${headLimit}`);

        if (flags.length > 0) {
            searchInfo += ` <span class="grep-flags">(${flags.join(', ')})</span>`;
        }

        let pathInfo = `<code class="file-path">${escapeHtmlFn(path)}</code>`;
        if (glob) {
            pathInfo += ` <span class="grep-glob">matching ${escapeHtmlFn(glob)}</span>`;
        }
        if (type) {
            pathInfo += ` <span class="grep-type">type: ${escapeHtmlFn(type)}</span>`;
        }

        return `
            <div class="tool-parameters tool-grep-params">
                <div class="grep-search-info">
                    <span class="grep-icon">🔍</span>
                    <strong>Searching for:</strong> ${searchInfo}
                </div>
                <div class="grep-path-info">
                    <strong>In:</strong> ${pathInfo}
                </div>
                <div class="grep-mode-info">
                    <strong>Mode:</strong> <span class="grep-mode">${escapeHtmlFn(outputMode)}</span>
                </div>
            </div>
        `;
    }

    renderResult(toolCall, escapeHtmlFn) {
        if (!toolCall.result) return '';

        const resultClass = toolCall.result.error ? 'tool-result-error' : 'tool-result-success';

        if (toolCall.result.error) {
            return `
                <div class="tool-result ${resultClass}">
                    <strong>Error:</strong>
                    <pre class="tool-result-content">${escapeHtmlFn(toolCall.result.content || toolCall.result.message || 'No content')}</pre>
                </div>
            `;
        }

        const content = toolCall.result.content || '';
        const outputMode = toolCall.input.output_mode || 'files_with_matches';

        // Handle files_with_matches mode - just list files
        if (outputMode === 'files_with_matches') {
            const lines = content.split('\n').filter(line => line.trim());

            // First line might be a summary like "Found 2 files"
            const foundMatch = lines[0].match(/^Found (\d+) files?/);
            const fileCount = foundMatch ? foundMatch[1] : lines.length;
            const fileList = foundMatch ? lines.slice(1) : lines;

            if (fileList.length === 0) {
                return `
                    <div class="tool-result ${resultClass}">
                        <strong>No matches found</strong>
                    </div>
                `;
            }

            return `
                <div class="tool-result ${resultClass}">
                    <div class="grep-result-header">
                        <strong>Found ${fileCount} file${fileCount !== '1' ? 's' : ''}:</strong>
                    </div>
                    <div class="grep-file-list">
                        ${fileList.map(file => `<div class="grep-file-item"><code>${escapeHtmlFn(file)}</code></div>`).join('')}
                    </div>
                </div>
            `;
        }

        // Handle content mode - show lines grouped by file
        if (outputMode === 'content') {
            const lines = content.split('\n').filter(line => line.trim());

            if (lines.length === 0) {
                return `
                    <div class="tool-result ${resultClass}">
                        <strong>No matches found</strong>
                    </div>
                `;
            }

            // Group lines by file (format: filepath:line_number:content)
            const fileGroups = new Map();
            lines.forEach(line => {
                // Match pattern: filepath:line_number:content
                const match = line.match(/^([^:]+):(\d+):(.*)$/);
                if (match) {
                    const [, filepath, lineNum, lineContent] = match;
                    if (!fileGroups.has(filepath)) {
                        fileGroups.set(filepath, []);
                    }
                    fileGroups.get(filepath).push({
                        lineNum: lineNum,
                        content: lineContent
                    });
                }
            });

            if (fileGroups.size === 0) {
                // Fallback if format doesn't match
                return `
                    <div class="tool-result ${resultClass}">
                        <strong>Results:</strong>
                        <pre class="tool-result-content">${escapeHtmlFn(content)}</pre>
                    </div>
                `;
            }

            let resultHtml = `
                <div class="tool-result ${resultClass}">
                    <div class="grep-result-header">
                        <strong>Found matches in ${fileGroups.size} file${fileGroups.size !== 1 ? 's' : ''}:</strong>
                    </div>
                    <div class="grep-content-results">
            `;

            fileGroups.forEach((matches, filepath) => {
                resultHtml += `
                    <div class="grep-file-group">
                        <div class="grep-file-header">
                            <code class="file-path">${escapeHtmlFn(filepath)}</code>
                            <span class="grep-match-count">(${matches.length} match${matches.length !== 1 ? 'es' : ''})</span>
                        </div>
                        <div class="grep-matches">
                            ${matches.map(m => `
                                <div class="grep-match-line">
                                    <span class="grep-line-num">${m.lineNum}:</span>
                                    <code class="grep-line-content">${escapeHtmlFn(m.content)}</code>
                                </div>
                            `).join('')}
                        </div>
                    </div>
                `;
            });

            resultHtml += `
                    </div>
                </div>
            `;

            return resultHtml;
        }

        // Handle count mode or unknown modes
        return `
            <div class="tool-result ${resultClass}">
                <strong>Results:</strong>
                <pre class="tool-result-content">${escapeHtmlFn(content)}</pre>
            </div>
        `;
    }

    getCollapsedSummary(toolCall) {
        const statusIcon = {
            'pending': '🔄',
            'permission_required': '❓',
            'executing': '⚡',
            'completed': toolCall.permissionDecision === 'deny' ? '❌' : '✅',
            'error': '💥'
        }[toolCall.status] || '🔧';

        const pattern = toolCall.input.pattern || 'pattern';
        const outputMode = toolCall.input.output_mode || 'files_with_matches';

        let statusText = '';
        if (toolCall.status === 'completed' && !toolCall.result?.error) {
            const content = toolCall.result?.content || '';
            const lines = content.split('\n').filter(line => line.trim());

            if (outputMode === 'files_with_matches') {
                const foundMatch = lines[0].match(/^Found (\d+) files?/);
                const fileCount = foundMatch ? foundMatch[1] : lines.length - 1;
                statusText = `${fileCount} file${fileCount !== '1' ? 's' : ''}`;
            } else if (outputMode === 'content') {
                statusText = `${lines.length} match${lines.length !== 1 ? 'es' : ''}`;
            } else {
                statusText = 'Completed';
            }
        } else if (toolCall.result?.error) {
            statusText = 'Error';
        } else {
            statusText = {
                'pending': 'Pending',
                'permission_required': 'Awaiting Permission',
                'executing': 'Executing',
                'completed': 'Completed',
                'error': 'Error'
            }[toolCall.status] || 'Unknown';
        }

        return `${statusIcon} Grep: "${pattern}" - ${statusText}`;
    }
}

// Glob Tool Handler - custom display for Glob tool (file name pattern matching)
class GlobToolHandler {
    renderParameters(toolCall, escapeHtmlFn) {
        const pattern = toolCall.input.pattern || '';
        const path = toolCall.input.path || '.';

        return `
            <div class="tool-parameters tool-glob-params">
                <div class="glob-search-info">
                    <span class="glob-icon">📁</span>
                    <strong>Finding files matching:</strong>
                    <span class="glob-pattern"><code>${escapeHtmlFn(pattern)}</code></span>
                </div>
                ${path !== '.' ? `
                    <div class="glob-path-info">
                        <strong>In directory:</strong>
                        <code class="file-path">${escapeHtmlFn(path)}</code>
                    </div>
                ` : ''}
            </div>
        `;
    }

    renderResult(toolCall, escapeHtmlFn) {
        if (!toolCall.result) return '';

        const resultClass = toolCall.result.error ? 'tool-result-error' : 'tool-result-success';

        if (toolCall.result.error) {
            return `
                <div class="tool-result ${resultClass}">
                    <strong>Error:</strong>
                    <pre class="tool-result-content">${escapeHtmlFn(toolCall.result.content || toolCall.result.message || 'No content')}</pre>
                </div>
            `;
        }

        const content = toolCall.result.content || '';
        const files = content.split('\n').filter(line => line.trim());

        if (files.length === 0) {
            return `
                <div class="tool-result ${resultClass}">
                    <strong>No matching files found</strong>
                </div>
            `;
        }

        return `
            <div class="tool-result ${resultClass}">
                <div class="glob-result-header">
                    <strong>Found ${files.length} file${files.length !== 1 ? 's' : ''}:</strong>
                </div>
                <div class="glob-file-list">
                    ${files.map(file => `<div class="glob-file-item"><code>${escapeHtmlFn(file)}</code></div>`).join('')}
                </div>
            </div>
        `;
    }

    getCollapsedSummary(toolCall) {
        const statusIcon = {
            'pending': '🔄',
            'permission_required': '❓',
            'executing': '⚡',
            'completed': toolCall.permissionDecision === 'deny' ? '❌' : '✅',
            'error': '💥'
        }[toolCall.status] || '🔧';

        const pattern = toolCall.input.pattern || 'pattern';

        let statusText = '';
        if (toolCall.status === 'completed' && !toolCall.result?.error) {
            const content = toolCall.result?.content || '';
            const fileCount = content.split('\n').filter(line => line.trim()).length;
            statusText = `${fileCount} file${fileCount !== 1 ? 's' : ''}`;
        } else if (toolCall.result?.error) {
            statusText = 'Error';
        } else {
            statusText = {
                'pending': 'Pending',
                'permission_required': 'Awaiting Permission',
                'executing': 'Executing',
                'completed': 'Completed',
                'error': 'Error'
            }[toolCall.status] || 'Unknown';
        }

        return `${statusIcon} Glob: "${pattern}" - ${statusText}`;
    }
}

// WebFetch Tool Handler - custom display for WebFetch tool
class WebFetchToolHandler {
    renderParameters(toolCall, escapeHtmlFn) {
        const url = toolCall.input.url || '';
        const prompt = toolCall.input.prompt || '';

        return `
            <div class="tool-parameters tool-webfetch-params">
                <div class="webfetch-url-info">
                    <span class="webfetch-icon">🌐</span>
                    <strong>Fetching:</strong>
                    <a href="${escapeHtmlFn(url)}" target="_blank" rel="noopener noreferrer" class="webfetch-url">${escapeHtmlFn(url)}</a>
                </div>
                ${prompt ? `
                    <div class="webfetch-prompt-info">
                        <strong>Task:</strong>
                        <span class="webfetch-prompt">${escapeHtmlFn(prompt)}</span>
                    </div>
                ` : ''}
            </div>
        `;
    }

    renderResult(toolCall, escapeHtmlFn) {
        if (!toolCall.result) return '';

        const resultClass = toolCall.result.error ? 'tool-result-error' : 'tool-result-success';

        if (toolCall.result.error) {
            return `
                <div class="tool-result ${resultClass}">
                    <strong>Error:</strong>
                    <pre class="tool-result-content">${escapeHtmlFn(toolCall.result.content || toolCall.result.message || 'No content')}</pre>
                </div>
            `;
        }

        const content = toolCall.result.content || '';
        const lines = content.split('\n');
        const previewLimit = 15;
        const hasMore = lines.length > previewLimit;
        const previewLines = lines.slice(0, previewLimit);

        return `
            <div class="tool-result ${resultClass}">
                <div class="webfetch-result-header">
                    <strong>Response:</strong>
                    <span class="webfetch-line-count">${lines.length} lines</span>
                    ${hasMore ? `<span class="webfetch-preview-note">(showing first ${previewLimit})</span>` : ''}
                </div>
                <pre class="tool-result-content webfetch-content-preview">${escapeHtmlFn(previewLines.join('\n'))}</pre>
                ${hasMore ? '<div class="webfetch-more-indicator">...</div>' : ''}
            </div>
        `;
    }

    getCollapsedSummary(toolCall) {
        const statusIcon = {
            'pending': '🔄',
            'permission_required': '❓',
            'executing': '⚡',
            'completed': toolCall.permissionDecision === 'deny' ? '❌' : '✅',
            'error': '💥'
        }[toolCall.status] || '🔧';

        const url = toolCall.input.url || '';
        const domain = url.match(/^https?:\/\/([^\/]+)/)?.[1] || url;

        let statusText = '';
        if (toolCall.status === 'completed' && !toolCall.result?.error) {
            statusText = 'Fetched';
        } else if (toolCall.result?.error) {
            statusText = 'Error';
        } else {
            statusText = {
                'pending': 'Pending',
                'permission_required': 'Awaiting Permission',
                'executing': 'Fetching',
                'completed': 'Completed',
                'error': 'Error'
            }[toolCall.status] || 'Unknown';
        }

        return `${statusIcon} WebFetch: ${domain} - ${statusText}`;
    }
}

// WebSearch Tool Handler - custom display for WebSearch tool
class WebSearchToolHandler {
    renderParameters(toolCall, escapeHtmlFn) {
        const query = toolCall.input.query || '';
        const allowedDomains = toolCall.input.allowed_domains || [];
        const blockedDomains = toolCall.input.blocked_domains || [];

        return `
            <div class="tool-parameters tool-websearch-params">
                <div class="websearch-query-info">
                    <span class="websearch-icon">🔎</span>
                    <strong>Searching for:</strong>
                    <span class="websearch-query">${escapeHtmlFn(query)}</span>
                </div>
                ${allowedDomains.length > 0 ? `
                    <div class="websearch-filter-info">
                        <strong>Allowed domains:</strong>
                        <span class="websearch-domains">${allowedDomains.map(d => escapeHtmlFn(d)).join(', ')}</span>
                    </div>
                ` : ''}
                ${blockedDomains.length > 0 ? `
                    <div class="websearch-filter-info">
                        <strong>Blocked domains:</strong>
                        <span class="websearch-domains">${blockedDomains.map(d => escapeHtmlFn(d)).join(', ')}</span>
                    </div>
                ` : ''}
            </div>
        `;
    }

    renderResult(toolCall, escapeHtmlFn) {
        if (!toolCall.result) return '';

        const resultClass = toolCall.result.error ? 'tool-result-error' : 'tool-result-success';

        if (toolCall.result.error) {
            return `
                <div class="tool-result ${resultClass}">
                    <strong>Error:</strong>
                    <pre class="tool-result-content">${escapeHtmlFn(toolCall.result.content || toolCall.result.message || 'No content')}</pre>
                </div>
            `;
        }

        const content = toolCall.result.content || '';

        // Try to parse the Links JSON from the content
        const linksMatch = content.match(/Links: (\[.*?\])/s);
        let links = [];
        if (linksMatch) {
            try {
                links = JSON.parse(linksMatch[1]);
            } catch (e) {
                // Failed to parse, will show raw content
            }
        }

        // Extract the summary after the Links section
        const summaryMatch = content.match(/Links: \[.*?\]\n\n(.*)/s);
        const summary = summaryMatch ? summaryMatch[1] : content;

        if (links.length > 0) {
            return `
                <div class="tool-result ${resultClass}">
                    <div class="websearch-links-section">
                        <strong>Found ${links.length} result${links.length !== 1 ? 's' : ''}:</strong>
                        <div class="websearch-links">
                            ${links.map(link => `
                                <div class="websearch-link-item">
                                    <a href="${escapeHtmlFn(link.url)}" target="_blank" rel="noopener noreferrer" class="websearch-link-title">
                                        ${escapeHtmlFn(link.title)}
                                    </a>
                                    <div class="websearch-link-url">${escapeHtmlFn(link.url)}</div>
                                </div>
                            `).join('')}
                        </div>
                    </div>
                    <div class="websearch-summary-section">
                        <strong>Summary:</strong>
                        <div class="websearch-summary">${escapeHtmlFn(summary)}</div>
                    </div>
                </div>
            `;
        }

        // Fallback to simple content display
        return `
            <div class="tool-result ${resultClass}">
                <strong>Results:</strong>
                <pre class="tool-result-content">${escapeHtmlFn(content)}</pre>
            </div>
        `;
    }

    getCollapsedSummary(toolCall) {
        const statusIcon = {
            'pending': '🔄',
            'permission_required': '❓',
            'executing': '⚡',
            'completed': toolCall.permissionDecision === 'deny' ? '❌' : '✅',
            'error': '💥'
        }[toolCall.status] || '🔧';

        const query = toolCall.input.query || 'search';

        let statusText = '';
        if (toolCall.status === 'completed' && !toolCall.result?.error) {
            // Try to extract result count
            const content = toolCall.result?.content || '';
            const linksMatch = content.match(/Links: (\[.*?\])/s);
            if (linksMatch) {
                try {
                    const links = JSON.parse(linksMatch[1]);
                    statusText = `${links.length} result${links.length !== 1 ? 's' : ''}`;
                } catch (e) {
                    statusText = 'Completed';
                }
            } else {
                statusText = 'Completed';
            }
        } else if (toolCall.result?.error) {
            statusText = 'Error';
        } else {
            statusText = {
                'pending': 'Pending',
                'permission_required': 'Awaiting Permission',
                'executing': 'Searching',
                'completed': 'Completed',
                'error': 'Error'
            }[toolCall.status] || 'Unknown';
        }

        return `${statusIcon} WebSearch: "${query}" - ${statusText}`;
    }
}

class BashToolHandler {
    renderParameters(toolCall, escapeHtmlFn) {
        const command = toolCall.input.command || '';
        const description = toolCall.input.description || '';
        const timeout = toolCall.input.timeout;
        const runInBackground = toolCall.input.run_in_background || false;

        let html = '<div class="tool-bash-params">';

        // Header with bash icon
        html += '<div class="bash-header">';
        html += '<span class="bash-icon">💻</span>';
        html += '<div class="bash-header-content">';
        if (description) {
            html += `<div><strong>Description:</strong> ${escapeHtmlFn(description)}</div>`;
        }

        // Display flags inline
        const flags = [];
        if (timeout) {
            flags.push(`timeout: ${timeout}ms`);
        }
        if (runInBackground) {
            flags.push('background');
        }
        if (flags.length > 0) {
            html += `<div class="bash-flags">${flags.join(', ')}</div>`;
        }

        html += '</div>';
        html += '</div>';

        // Command section (displayed as text block for potentially long commands)
        html += '<div class="bash-command-section">';
        html += '<div class="bash-command-label"><strong>Command:</strong></div>';
        html += '<div class="bash-command-content">';
        html += escapeHtmlFn(command);
        html += '</div>';
        html += '</div>';

        html += '</div>';
        return html;
    }

    renderResult(toolCall, escapeHtmlFn) {
        if (!toolCall.result) return '';

        const content = toolCall.result.content || '';
        const resultClass = toolCall.result.error ? 'tool-result-error' : 'tool-result-success';

        if (!content) {
            return `<div class="tool-result ${resultClass}"><strong>Output:</strong><div class="bash-result-empty">No output</div></div>`;
        }

        return `
            <div class="tool-result ${resultClass}">
                <strong>Output:</strong>
                <pre class="bash-result-content">${escapeHtmlFn(content)}</pre>
            </div>
        `;
    }

    getCollapsedSummary(toolCall, escapeHtmlFn) {
        const description = toolCall.input.description || toolCall.input.command || 'Bash';
        const runInBackground = toolCall.input.run_in_background || false;

        // Status-based icons
        const statusIcon = {
            'pending': '⏳',
            'running': '▶️',
            'completed': '✅',
            'error': '❌'
        }[toolCall.status] || '💻';

        const bgFlag = runInBackground ? '(background) ' : '';
        return `${statusIcon} Bash: ${bgFlag}${description}`;
    }
}

class BashOutputToolHandler {
    renderParameters(toolCall, escapeHtmlFn) {
        const bashId = toolCall.input.bash_id || '';
        const filter = toolCall.input.filter || '';

        let html = '<div class="tool-bash-params">';

        // Header with bash output icon
        html += '<div class="bash-header">';
        html += '<span class="bash-icon">📋</span>';
        html += '<div class="bash-header-content">';
        html += `<div><strong>Shell ID:</strong> ${escapeHtmlFn(bashId)}</div>`;
        if (filter) {
            html += `<div><strong>Filter:</strong> ${escapeHtmlFn(filter)}</div>`;
        }
        html += '</div>';
        html += '</div>';

        html += '</div>';
        return html;
    }

    renderResult(toolCall, escapeHtmlFn) {
        if (!toolCall.result) return '';

        const content = toolCall.result.content || '';
        const resultClass = toolCall.result.error ? 'tool-result-error' : 'tool-result-success';

        if (!content) {
            return `<div class="tool-result ${resultClass}"><strong>Output:</strong><div class="bash-result-empty">No output</div></div>`;
        }

        // Parse XML tags from content
        const statusMatch = content.match(/<status>(.*?)<\/status>/);
        const exitCodeMatch = content.match(/<exit_code>(.*?)<\/exit_code>/);
        const stdoutMatch = content.match(/<stdout>(.*?)<\/stdout>/s);
        const timestampMatch = content.match(/<timestamp>(.*?)<\/timestamp>/);

        const status = statusMatch ? statusMatch[1] : '';
        const exitCode = exitCodeMatch ? exitCodeMatch[1] : '';
        const stdout = stdoutMatch ? stdoutMatch[1].trim() : '';
        const timestamp = timestampMatch ? timestampMatch[1] : '';

        // Determine status icon and color based on status and exit code
        let statusIcon = '❓'; // Unknown/default
        let statusColor = 'gray';

        if (status === 'running') {
            statusIcon = '▶️';
            statusColor = 'yellow';
        } else if (status === 'completed') {
            if (exitCode === '0') {
                statusIcon = '✅';
                statusColor = 'green';
            } else {
                statusIcon = '❌';
                statusColor = 'red';
            }
        } else if (status === 'error' || status === 'killed') {
            statusIcon = '❌';
            statusColor = 'red';
        }

        let html = `<div class="tool-result ${resultClass}"><strong>Output:</strong>`;

        // Display stdout as text block
        if (stdout) {
            html += '<pre class="bashoutput-stdout-content">';
            html += escapeHtmlFn(stdout);
            html += '</pre>';
        }

        // Display timestamp and exit code footer with status icon
        if (timestamp || exitCode) {
            html += '<div class="bashoutput-footer">';
            html += `${statusIcon} Checked: `;
            if (timestamp) {
                html += escapeHtmlFn(timestamp);
            }
            if (exitCode) {
                html += ` (Exit Code: ${escapeHtmlFn(exitCode)})`;
            }
            html += '</div>';
        }

        html += '</div>';
        return html;
    }

    getCollapsedSummary(toolCall, escapeHtmlFn) {
        const bashId = toolCall.input.bash_id || 'unknown';

        // Parse result content for status and exit code
        const content = toolCall.result?.content || '';
        const statusMatch = content.match(/<status>(.*?)<\/status>/);
        const exitCodeMatch = content.match(/<exit_code>(.*?)<\/exit_code>/);

        const bashStatus = statusMatch ? statusMatch[1] : '';
        const exitCode = exitCodeMatch ? exitCodeMatch[1] : '';

        // Status-based icons
        let statusIcon = '📋';
        if (bashStatus === 'running') {
            statusIcon = '▶️';
        } else if (bashStatus === 'completed') {
            statusIcon = exitCode === '0' ? '✅' : '❌';
        } else if (bashStatus === 'error' || bashStatus === 'killed') {
            statusIcon = '❌';
        } else if (toolCall.status === 'pending') {
            statusIcon = '⏳';
        }

        // Build summary
        let summary = `${statusIcon} BashOutput:`;
        if (bashStatus) {
            summary += ` (${bashStatus})`;
        }
        summary += ` ${bashId}`;
        if (exitCode) {
            summary += ` (Exit Code: ${exitCode})`;
        }

        return summary;
    }
}

class KillShellToolHandler {
    renderParameters(toolCall, escapeHtmlFn) {
        const shellId = toolCall.input.shell_id || '';

        let html = '<div class="tool-bash-params">';

        // Header with kill icon
        html += '<div class="bash-header">';
        html += '<span class="bash-icon">🛑</span>';
        html += '<div class="bash-header-content">';
        html += `<div><strong>Shell ID:</strong> ${escapeHtmlFn(shellId)}</div>`;
        html += '</div>';
        html += '</div>';

        html += '</div>';
        return html;
    }

    renderResult(toolCall, escapeHtmlFn) {
        if (!toolCall.result) return '';

        const content = toolCall.result.content || '';
        const resultClass = toolCall.result.error ? 'tool-result-error' : 'tool-result-success';

        if (!content) {
            return `<div class="tool-result ${resultClass}"><strong>Result:</strong><div class="bash-result-empty">No result</div></div>`;
        }

        return `
            <div class="tool-result ${resultClass}">
                <strong>Result:</strong>
                <div class="killshell-result-content">${escapeHtmlFn(content)}</div>
            </div>
        `;
    }

    getCollapsedSummary(toolCall, escapeHtmlFn) {
        const shellId = toolCall.input.shell_id || 'unknown';

        // Status-based icons
        const statusIcon = {
            'pending': '⏳',
            'running': '▶️',
            'completed': toolCall.result?.error ? '❌' : '✅',
            'error': '❌'
        }[toolCall.status] || '🛑';

        return `${statusIcon} KillShell: ${shellId}`;
    }
}

class TaskToolHandler {
    renderParameters(toolCall, escapeHtmlFn) {
        const subagentType = toolCall.input.subagent_type || 'general-purpose';
        const description = toolCall.input.description || '';
        const prompt = toolCall.input.prompt || '';

        let html = '<div class="tool-task-params">';

        // Header with agent icon
        html += '<div class="task-header">';
        html += '<span class="task-icon">🤖</span>';
        html += '<div class="task-header-content">';
        html += `<div><strong>Agent Type:</strong> ${escapeHtmlFn(subagentType)}</div>`;
        if (description) {
            html += `<div><strong>Task:</strong> ${escapeHtmlFn(description)}</div>`;
        }
        html += '</div>';
        html += '</div>';

        // Prompt section (displayed as text block for potentially large content)
        if (prompt) {
            html += '<div class="task-prompt-section">';
            html += '<div class="task-prompt-label"><strong>Prompt:</strong></div>';
            html += '<div class="task-prompt-content">';
            html += escapeHtmlFn(prompt);
            html += '</div>';
            html += '</div>';
        }

        html += '</div>';
        return html;
    }

    renderResult(toolCall, escapeHtmlFn) {
        if (!toolCall.result) return '';

        const content = toolCall.result.content || '';
        const resultClass = toolCall.result.error ? 'tool-result-error' : 'tool-result-success';

        if (!content) {
            return `<div class="tool-result ${resultClass}"><strong>Agent Response:</strong><div class="task-result-empty">No result returned</div></div>`;
        }

        return `
            <div class="tool-result ${resultClass}">
                <strong>Agent Response:</strong>
                <div class="task-result-content">${escapeHtmlFn(content)}</div>
            </div>
        `;
    }

    getCollapsedSummary(toolCall, escapeHtmlFn) {
        const description = toolCall.input.description || 'Task';
        const subagentType = toolCall.input.subagent_type || 'general-purpose';

        // Status-based icons
        const statusIcon = {
            'pending': '⏳',
            'running': '▶️',
            'completed': '✅',
            'error': '❌'
        }[toolCall.status] || '🤖';

        return `${statusIcon} Task: (${subagentType}) ${description}`;
    }
}

class ClaudeWebUI {
    constructor() {
        this.currentSessionId = null;
        this.sessions = new Map();
        this.orderedSessions = []; // Maintains session order from backend

        // Session-specific WebSocket for message streaming
        this.sessionWebsocket = null;
        this.sessionConnectionRetryCount = 0;
        this.maxSessionRetries = 5;
        this.intentionalSessionDisconnect = false;

        // Global UI WebSocket for session state updates
        this.uiWebsocket = null;
        this.uiConnectionRetryCount = 0;
        this.maxUIRetries = 10;

        // Auto-scroll functionality
        this.autoScrollEnabled = true;
        this.isUserScrolling = false;
        this.scrollTimeout = null;

        // Processing state management
        this.isProcessing = false;

        // Status indicator configuration
        this.statusColors = this.initializeStatusColors();

        // Sidebar state management
        this.sidebarCollapsed = false;
        this.sidebarWidth = 300;
        this.isResizing = false;

        // Session deletion state tracking
        this.deletingSessions = new Set();

        // Tool call management
        this.toolCallManager = new ToolCallManager();

        // Tool handler registry for custom tool display
        this.toolHandlerRegistry = new ToolHandlerRegistry();
        this.defaultToolHandler = new DefaultToolHandler();
        this.initializeToolHandlers();

        this.init();
    }

    initializeToolHandlers() {
        // Register built-in tool handlers
        this.toolHandlerRegistry.registerHandler('Read', new ReadToolHandler());
        this.toolHandlerRegistry.registerHandler('Edit', new EditToolHandler());
        this.toolHandlerRegistry.registerHandler('MultiEdit', new MultiEditToolHandler());
        this.toolHandlerRegistry.registerHandler('Write', new WriteToolHandler());
        this.toolHandlerRegistry.registerHandler('TodoWrite', new TodoWriteToolHandler());
        this.toolHandlerRegistry.registerHandler('Grep', new GrepToolHandler());
        this.toolHandlerRegistry.registerHandler('Glob', new GlobToolHandler());
        this.toolHandlerRegistry.registerHandler('WebFetch', new WebFetchToolHandler());
        this.toolHandlerRegistry.registerHandler('WebSearch', new WebSearchToolHandler());
        this.toolHandlerRegistry.registerHandler('Bash', new BashToolHandler());
        this.toolHandlerRegistry.registerHandler('BashOutput', new BashOutputToolHandler());
        this.toolHandlerRegistry.registerHandler('KillShell', new KillShellToolHandler());
        this.toolHandlerRegistry.registerHandler('Task', new TaskToolHandler());

        // Register pattern handlers for MCP tools (can be customized later)
        // this.toolHandlerRegistry.registerPatternHandler('mcp__*', new McpToolHandler());
    }

    initializeStatusColors() {
        return {
            // Session states
            session: {
                'CREATED': { color: 'grey', animate: false, text: 'Created' },
                'created': { color: 'grey', animate: false, text: 'Created' },
                'Starting': { color: 'green', animate: true, text: 'Starting' },
                'starting': { color: 'green', animate: true, text: 'Starting' },
                'running': { color: 'green', animate: false, text: 'Running' },
                'active': { color: 'green', animate: false, text: 'Active' },
                'processing': { color: 'purple', animate: true, text: 'Processing' },
                'completed': { color: 'grey', animate: false, text: 'Completed' },
                'failed': { color: 'red', animate: false, text: 'Failed' },
                'error': { color: 'red', animate: false, text: 'Failed' },
                'terminated': { color: 'grey', animate: false, text: 'Terminated' },
                'paused': { color: 'grey', animate: false, text: 'Paused' },
                'unknown': { color: 'purple', animate: true, text: 'Unknown' }
            },
            // WebSocket states
            websocket: {
                'connecting': { color: 'green', animate: true, text: 'Connecting' },
                'connected': { color: 'green', animate: false, text: 'Connected' },
                'disconnected': { color: 'red', animate: false, text: 'Disconnected' },
                'unknown': { color: 'purple', animate: true, text: 'Unknown' }
            }
        };
    }

    createStatusIndicator(state, type, actualState = null) {
        const config = this.statusColors[type] && this.statusColors[type][state]
            ? this.statusColors[type][state]
            : this.statusColors[type]['unknown'];

        const indicator = document.createElement('span');
        indicator.className = `status-dot status-dot-${config.color}`;

        if (config.animate) {
            indicator.classList.add('status-dot-blink');
        }

        // Set hover text - show actual state for unknown states
        const hoverText = actualState && state === 'unknown'
            ? `Unknown state: ${actualState}`
            : config.text;
        indicator.title = hoverText;

        return indicator;
    }

    init() {
        this.setupEventListeners();
        this.connectUIWebSocket();
        this.updateConnectionStatus('disconnected');
    }

    setupEventListeners() {
        // Session controls
        document.getElementById('create-session-btn').addEventListener('click', () => this.showCreateSessionModal());
        document.getElementById('refresh-sessions-btn').addEventListener('click', () => this.refreshSessions());

        // Modal controls
        document.getElementById('close-modal').addEventListener('click', () => this.hideCreateSessionModal());
        document.getElementById('cancel-create').addEventListener('click', () => this.hideCreateSessionModal());
        document.getElementById('create-session-form').addEventListener('submit', (e) => this.handleCreateSession(e));

        // Browse directory button
        document.getElementById('browse-directory').addEventListener('click', () => this.browseDirectory());

        // Session actions
        document.getElementById('delete-session-btn').addEventListener('click', () => this.showDeleteSessionModal());
        document.getElementById('exit-session-btn').addEventListener('click', () => this.exitSession());

        // Delete modal controls
        document.getElementById('close-delete-modal').addEventListener('click', () => this.hideDeleteSessionModal());
        document.getElementById('cancel-delete').addEventListener('click', () => this.hideDeleteSessionModal());
        document.getElementById('confirm-delete').addEventListener('click', () => this.confirmDeleteSession());

        // Sidebar controls
        document.getElementById('sidebar-collapse-btn').addEventListener('click', () => this.toggleSidebar());
        document.getElementById('sidebar-resize-handle').addEventListener('mousedown', (e) => this.startResize(e));

        // Message sending
        document.getElementById('send-btn').addEventListener('click', () => this.handleSendButtonClick());
        document.getElementById('message-input').addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                this.handleSendButtonClick();
            }
        });

        // Auto-scroll toggle
        document.getElementById('auto-scroll-toggle').addEventListener('click', () => this.toggleAutoScroll());

        // Messages area scroll detection
        document.getElementById('messages-area').addEventListener('scroll', (e) => this.handleScroll(e));

        // Modal click outside to close
        document.getElementById('create-session-modal').addEventListener('click', (e) => {
            if (e.target.id === 'create-session-modal') {
                this.hideCreateSessionModal();
            }
        });

        document.getElementById('delete-session-modal').addEventListener('click', (e) => {
            if (e.target.id === 'delete-session-modal') {
                this.hideDeleteSessionModal();
            }
        });

        // Window resize handling for sidebar constraints
        window.addEventListener('resize', () => this.handleWindowResize());
    }

    // API Methods
    async apiRequest(endpoint, options = {}) {
        try {
            const response = await fetch(endpoint, {
                headers: {
                    'Content-Type': 'application/json',
                    ...options.headers
                },
                ...options
            });

            if (!response.ok) {
                throw new Error(`HTTP ${response.status}: ${response.statusText}`);
            }

            return await response.json();
        } catch (error) {
            console.error('API request failed:', error);
            this.showError(`API request failed: ${error.message}`);
            throw error;
        }
    }

    // Session Management
    async loadSessions() {
        try {
            this.showLoading(true);
            const data = await this.apiRequest('/api/sessions');
            this.sessions.clear();
            this.orderedSessions = [];

            data.sessions.forEach(session => {
                this.sessions.set(session.session_id, session);
                this.orderedSessions.push(session);
            });

            this.renderSessions();
        } catch (error) {
            console.error('Failed to load sessions:', error);
        } finally {
            this.showLoading(false);
        }
    }

    async createSession(formData) {
        try {
            this.showLoading(true);

            const tools = formData.tools ? formData.tools.split(',').map(t => t.trim()).filter(t => t) : [];

            const payload = {
                working_directory: formData.working_directory,
                permissions: formData.permissions,
                system_prompt: formData.system_prompt || null,
                tools: tools,
                model: formData.model || null
            };

            const data = await this.apiRequest('/api/sessions', {
                method: 'POST',
                body: JSON.stringify(payload)
            });

            // Refresh session list to get correct order (new session at top, existing sessions shifted down)
            await this.refreshSessions();
            await this.selectSession(data.session_id);

            return data.session_id;
        } catch (error) {
            console.error('Failed to create session:', error);
            throw error;
        } finally {
            this.showLoading(false);
        }
    }

    exitSession() {
        if (!this.currentSessionId) return;

        console.log(`Exiting session ${this.currentSessionId}`);

        // Clean disconnect from WebSocket
        this.disconnectSessionWebSocket();

        // Clear current session
        this.currentSessionId = null;

        // Reset UI to no session selected state
        document.getElementById('no-session-selected').classList.remove('hidden');
        document.getElementById('chat-container').classList.add('hidden');

        // Remove active state from all session items
        document.querySelectorAll('.session-item').forEach(item => {
            item.classList.remove('active');
        });

        // Clear messages area
        document.getElementById('messages-area').innerHTML = '';

        // Reset processing state when exiting session
        this.hideProcessingIndicator();

        // Re-enable input controls when exiting session
        this.setInputControlsEnabled(true);
    }

    handleSendButtonClick() {
        if (this.isProcessing && !this.isInterrupting) {
            // Currently processing, button should act as Stop
            this.sendInterrupt();
        } else if (!this.isProcessing && !this.isInterrupting) {
            // Not processing, button should act as Send
            this.sendMessage();
        }
        // If isInterrupting is true, button is disabled so this shouldn't be called
    }

    async sendMessage() {
        const input = document.getElementById('message-input');
        const message = input.value.trim();

        if (!message || !this.currentSessionId || this.isProcessing) return;

        try {
            // Send via WebSocket if connected
            if (this.websocket && this.sessionWebsocket.readyState === WebSocket.OPEN) {
                this.sessionWebsocket.send(JSON.stringify({
                    type: 'send_message',
                    content: message
                }));
            } else {
                // Fallback to REST API
                await this.apiRequest(`/api/sessions/${this.currentSessionId}/messages`, {
                    method: 'POST',
                    body: JSON.stringify({ message })
                });
            }

            // Add user message to UI immediately
            this.addMessageToUI({
                type: 'user',
                content: message,
                timestamp: new Date().toISOString()
            });

            input.value = '';
        } catch (error) {
            console.error('Failed to send message:', error);
        }
    }

    async sendInterrupt() {
        if (!this.currentSessionId || !this.isProcessing) {
            console.log('DEBUG: sendInterrupt() called but conditions not met:', {
                currentSessionId: this.currentSessionId,
                isProcessing: this.isProcessing
            });
            return;
        }

        try {
            console.log('DEBUG: Sending interrupt request for session:', this.currentSessionId);
            console.log('DEBUG: WebSocket state check:', {
                sessionWebsocket: !!this.sessionWebsocket,
                readyState: this.sessionWebsocket?.readyState,
                OPEN: WebSocket.OPEN
            });

            // Update button to "Stopping..." state
            this.showStoppingIndicator();

            // Send interrupt via WebSocket if connected
            if (this.sessionWebsocket && this.sessionWebsocket.readyState === WebSocket.OPEN) {
                const interruptMessage = {
                    type: 'interrupt_session'
                };
                console.log('DEBUG: Sending interrupt message via WebSocket:', interruptMessage);
                this.sessionWebsocket.send(JSON.stringify(interruptMessage));
                console.log('DEBUG: Interrupt message sent successfully');
            } else {
                console.warn('DEBUG: WebSocket not connected, cannot send interrupt');
                console.log('DEBUG: WebSocket connection details:', {
                    sessionWebsocket: !!this.sessionWebsocket,
                    readyState: this.sessionWebsocket?.readyState,
                    expectedState: WebSocket.OPEN
                });
                // Fallback: we could add a REST API endpoint for interrupt if needed
                this.hideProcessingIndicator();
            }

        } catch (error) {
            console.error('DEBUG: Failed to send interrupt:', error);
            this.hideProcessingIndicator();
        }
    }

    showProcessingIndicator() {
        this.isProcessing = true;
        this.isInterrupting = false;
        const progressElement = document.getElementById('claude-progress');
        const sendButton = document.getElementById('send-btn');
        const messageInput = document.getElementById('message-input');

        if (progressElement) {
            progressElement.classList.remove('hidden');
        }
        if (sendButton) {
            sendButton.disabled = false; // Keep enabled for Stop functionality
            sendButton.textContent = 'Stop';
            sendButton.className = 'btn btn-danger'; // Change to red Stop button
        }
        if (messageInput) {
            messageInput.disabled = true;
            // Ensure processing state styling, not error state styling
            messageInput.placeholder = "Processing...";
        }
    }

    hideProcessingIndicator() {
        this.isProcessing = false;
        this.isInterrupting = false;
        const progressElement = document.getElementById('claude-progress');
        const sendButton = document.getElementById('send-btn');

        if (progressElement) {
            progressElement.classList.add('hidden');
        }
        if (sendButton) {
            sendButton.textContent = 'Send';
            sendButton.className = 'btn btn-primary'; // Restore primary button styling
        }

        // Re-enable controls only if current session is not in error state
        this.updateControlsBasedOnSessionState();
    }

    showStoppingIndicator() {
        this.isInterrupting = true;
        const sendButton = document.getElementById('send-btn');

        if (sendButton) {
            sendButton.disabled = true;
            sendButton.textContent = 'Stopping...';
            sendButton.className = 'btn btn-warning'; // Change to orange/warning color for stopping
        }
    }

    updateProcessingState(isProcessing) {
        this.isProcessing = isProcessing;

        if (isProcessing) {
            this.showProcessingIndicator();
        } else {
            this.hideProcessingIndicator();
        }
    }

    updateControlsBasedOnSessionState() {
        if (!this.currentSessionId) {
            // No session selected, enable controls
            this.setInputControlsEnabled(true);
            return;
        }

        const session = this.sessions.get(this.currentSessionId);
        if (session && session.state === 'error') {
            // Session is in error state, keep controls disabled
            this.setInputControlsEnabled(false);
        } else {
            // Session is not in error state, enable controls
            this.setInputControlsEnabled(true);
        }
    }

    // Frontend processing detection methods removed - now using backend state propagation

    shouldDisplayMessage(message) {
        // Get subtype from metadata or root level for backward compatibility
        const subtype = message.subtype || message.metadata?.subtype;

        // === SYSTEM MESSAGE FILTERING ===
        if (message.type === 'system') {
            // Filter out init messages (internal SDK initialization, not user-visible)
            if (subtype === 'init') {
                return false;
            }

            // Allow user-relevant system messages
            if (subtype === 'client_launched' || subtype === 'interrupt') {
                return true;
            }

            // Allow other system messages (errors, status, etc.)
            return true;
        }

        // === INFRASTRUCTURE MESSAGE FILTERING ===
        // Filter out result messages (internal completion markers)
        if (message.type === 'result') {
            return false;
        }

        // Filter out permission messages (handled by tool call UI)
        if (message.type === 'permission_request' || message.type === 'permission_response') {
            return false;
        }

        // === TOOL-RELATED MESSAGE FILTERING ===
        // Filter out assistant messages with tool uses (replaced by tool call cards)
        if (message.type === 'assistant' && this._hasToolUses(message)) {
            return false;
        }

        // Filter out user messages with tool results (replaced by tool call cards)
        if (message.type === 'user' && this._hasToolResults(message)) {
            return false;
        }

        // === USER MESSAGE FILTERING ===
        if (message.type === 'user') {
            // Filter out interrupt messages (shown as system messages instead)
            if (message.content === '[Request interrupted by user]' ||
                message.content === '[Request interrupted by user for tool use]' ||
                subtype === 'interrupt') {
                return false;
            }
        }

        // === DEFAULT ===
        // Display all other message types (regular user/assistant messages, etc.)
        return true;
    }

    /**
     * Check if message has tool uses using metadata
     */
    _hasToolUses(message) {
        return message.metadata &&
               message.metadata.has_tool_uses === true &&
               message.metadata.tool_uses &&
               Array.isArray(message.metadata.tool_uses) &&
               message.metadata.tool_uses.length > 0;
    }


    /**
     * Check if message has tool results using metadata
     */
    _hasToolResults(message) {
        return message.metadata &&
               message.metadata.has_tool_results === true &&
               message.metadata.tool_results &&
               Array.isArray(message.metadata.tool_results) &&
               message.metadata.tool_results.length > 0;
    }

    /**
     * Unified message processing function for both real-time and historical messages
     * @param {Object} message - The message to process
     * @param {string} source - Source of message: 'websocket' or 'historical'
     */
    processMessage(message, source = 'websocket') {
        console.log(`Processing message from ${source}:`, message);

        // Handle progress indicator for init messages (real-time only)
        if (source === 'websocket' && message.type === 'system' && message.subtype === 'init') {
            if (!this.isProcessing) {
                this.showProcessingIndicator();
            }
        }

        // Check if this is a tool-related message and handle it
        const toolHandled = this.handleToolRelatedMessage(message);

        // Check if this is a thinking block message and handle it
        const thinkingHandled = this.handleThinkingBlockMessage(message);


        // If it's a tool-related or thinking block message, don't show it in the regular message flow
        if (toolHandled || thinkingHandled) {
            return { handled: true, displayed: false };
        }

        // Use the unified filtering logic to determine if message should be displayed
        if (this.shouldDisplayMessage(message)) {
            console.log(`Adding ${source} message to UI:`, message.type);
            this.addMessageToUI(message, source === 'historical');
            return { handled: true, displayed: true };
        }

        return { handled: true, displayed: false };
    }

    handleIncomingMessage(message) {
        // Use unified processing for real-time messages
        this.processMessage(message, 'websocket');
    }

    handleToolRelatedMessage(message) {
        try {
            // Handle assistant messages with tool use blocks
            if (message.type === 'assistant' && message.metadata && message.metadata.tool_uses && Array.isArray(message.metadata.tool_uses)) {
                const toolUses = message.metadata.tool_uses;
                let hasToolUse = false;

                toolUses.forEach(toolUse => {
                    hasToolUse = true;
                    const toolUseBlock = {
                        id: toolUse.id,
                        name: toolUse.name,
                        input: toolUse.input
                    };

                    const toolCall = this.toolCallManager.handleToolUse(toolUseBlock);
                    this.renderToolCall(toolCall);
                });

                return hasToolUse;
            }

            // Handle permission requests
            if (message.type === 'permission_request') {
                const permissionRequest = {
                    request_id: message.request_id || message.metadata?.request_id,
                    tool_name: message.tool_name || message.metadata?.tool_name,
                    input_params: message.input_params || message.metadata?.input_params
                };

                const toolCall = this.toolCallManager.handlePermissionRequest(permissionRequest);
                if (toolCall) {
                    this.updateToolCall(toolCall);
                }
                return true;
            }

            // Handle permission responses
            if (message.type === 'permission_response') {
                const permissionResponse = {
                    request_id: message.request_id || message.metadata?.request_id,
                    decision: message.decision || message.metadata?.decision,
                    reasoning: message.reasoning || message.metadata?.reasoning
                };

                const toolCall = this.toolCallManager.handlePermissionResponse(permissionResponse);
                if (toolCall) {
                    this.updateToolCall(toolCall);
                }
                return true;
            }

            // Handle tool result blocks (in user messages)
            if (message.type === 'user' && message.metadata && message.metadata.tool_results && Array.isArray(message.metadata.tool_results)) {
                const toolResults = message.metadata.tool_results;
                let hasToolResult = false;

                toolResults.forEach(toolResult => {
                    hasToolResult = true;
                    const toolResultBlock = {
                        tool_use_id: toolResult.tool_use_id,
                        content: toolResult.content,
                        is_error: toolResult.is_error
                    };

                    const toolCall = this.toolCallManager.handleToolResult(toolResultBlock);
                    if (toolCall) {
                        this.updateToolCall(toolCall);
                    }
                });

                return hasToolResult;
            }

            return false;
        } catch (error) {
            console.error('Error handling tool-related message:', error, message);
            return false;
        }
    }

    handleThinkingBlockMessage(message) {
        try {
            // Check if this is an assistant message with thinking blocks
            if (message.type === 'assistant' && message.metadata && message.metadata.thinking_blocks && Array.isArray(message.metadata.thinking_blocks)) {
                const thinkingBlocks = message.metadata.thinking_blocks;

                if (thinkingBlocks.length > 0) {
                    console.log('Processing thinking blocks:', thinkingBlocks);

                    thinkingBlocks.forEach(thinkingBlock => {
                        // Generate a unique ID for this thinking block
                        const thinkingId = `thinking_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;

                        const thinkingBlockData = {
                            id: thinkingId,
                            content: thinkingBlock.content,
                            timestamp: thinkingBlock.timestamp || message.timestamp,
                            isExpanded: false // Start collapsed
                        };

                        this.renderThinkingBlock(thinkingBlockData);
                    });

                    return true; // Indicate that we handled thinking blocks
                }
            }

            return false; // No thinking blocks found
        } catch (error) {
            console.error('Error handling thinking block message:', error, message);
            return false;
        }
    }

    renderToolCall(toolCall) {
        console.log('Rendering tool call:', toolCall);

        const messagesArea = document.getElementById('messages-area');
        const toolCallElement = this.createToolCallElement(toolCall);

        // Add to DOM
        messagesArea.appendChild(toolCallElement);
        this.smartScrollToBottom();
    }

    updateToolCall(toolCall) {
        console.log('Updating tool call:', toolCall);

        const existingElement = document.getElementById(`tool-call-${toolCall.id}`);
        if (existingElement) {
            const updatedElement = this.createToolCallElement(toolCall);
            existingElement.replaceWith(updatedElement);
        } else {
            this.renderToolCall(toolCall);
        }
    }

    createToolCallElement(toolCall) {
        const element = document.createElement('div');
        element.className = 'tool-call-container';
        element.id = `tool-call-${toolCall.id}`;

        if (toolCall.isExpanded) {
            element.innerHTML = this.createExpandedToolCallHTML(toolCall);
        } else {
            element.innerHTML = this.createCollapsedToolCallHTML(toolCall);
        }

        // Add event delegation for click handlers
        this.setupToolCallEventListeners(element, toolCall);

        return element;
    }

    createExpandedToolCallHTML(toolCall) {
        const statusClass = `tool-status-${toolCall.status}`;
        const statusIcon = {
            'pending': '🔄',
            'permission_required': '❓',
            'executing': '⚡',
            'completed': toolCall.permissionDecision === 'deny' ? '❌' : '✅',
            'error': '💥'
        }[toolCall.status] || '🔧';

        // Get handler for this tool
        const handler = this.toolHandlerRegistry.getHandler(toolCall.name) || this.defaultToolHandler;

        let content = `
            <div class="tool-call-card ${statusClass}">
                <div class="tool-call-header">
                    <span class="tool-status-icon">${statusIcon}</span>
                    <span class="tool-name">${this.escapeHtml(toolCall.name)}</span>
                    <button class="tool-collapse-btn" data-tool-id="${toolCall.id}" title="Collapse">
                        ▼
                    </button>
                </div>

                <div class="tool-call-details">
                    ${handler.renderParameters(toolCall, this.escapeHtml.bind(this))}
                </div>
        `;

        // Add permission prompt if needed
        if (toolCall.status === 'permission_required') {
            content += `
                <div class="tool-permission-prompt">
                    <p><strong>🔐 Permission Required</strong></p>
                    <p>Claude Code wants to use the ${toolCall.name} tool. Do you want to allow this?</p>
                    <div class="permission-buttons">
                        <button class="btn btn-success permission-approve" data-request-id="${toolCall.permissionRequestId}" data-decision="allow">
                            ✅ Approve
                        </button>
                        <button class="btn btn-danger permission-deny" data-request-id="${toolCall.permissionRequestId}" data-decision="deny">
                            ❌ Deny
                        </button>
                    </div>
                </div>
            `;
        }

        // Add result if available using handler
        if (toolCall.result) {
            content += handler.renderResult(toolCall, this.escapeHtml.bind(this));
        }

        // Add explanation if available
        if (toolCall.explanation) {
            content += `
                <div class="tool-explanation">
                    <strong>Explanation:</strong>
                    <div class="tool-explanation-content">${this.escapeHtml(toolCall.explanation)}</div>
                </div>
            `;
        }

        content += '</div>';
        return content;
    }

    createCollapsedToolCallHTML(toolCall) {
        // Get handler for this tool
        const handler = this.toolHandlerRegistry.getHandler(toolCall.name);

        // Use handler's custom summary if available, otherwise use default
        let summary;
        if (handler && handler.getCollapsedSummary) {
            const customSummary = handler.getCollapsedSummary(toolCall);
            summary = customSummary !== null ? customSummary : this.toolCallManager.generateCollapsedSummary(toolCall);
        } else {
            summary = this.toolCallManager.generateCollapsedSummary(toolCall);
        }

        return `
            <div class="tool-call-collapsed" data-tool-id="${toolCall.id}" title="Click to expand">
                <span class="tool-collapsed-summary">${this.escapeHtml(summary)}</span>
                <span class="tool-expand-icon">▶</span>
            </div>
        `;
    }

    setupToolCallEventListeners(element, toolCall) {
        // Handle collapse/expand button clicks
        const collapseBtn = element.querySelector('.tool-collapse-btn');
        if (collapseBtn) {
            collapseBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                this.toggleToolCallExpansion(toolCall.id);
            });
        }

        // Handle expanded card header clicks for collapsing
        const expandedHeader = element.querySelector('.tool-call-header');
        if (expandedHeader) {
            expandedHeader.style.cursor = 'pointer';
            expandedHeader.addEventListener('click', () => {
                this.toggleToolCallExpansion(toolCall.id);
            });
        }

        // Handle collapsed card clicks for expansion
        const collapsedCard = element.querySelector('.tool-call-collapsed');
        if (collapsedCard) {
            collapsedCard.addEventListener('click', () => {
                this.toggleToolCallExpansion(toolCall.id);
            });
        }

        // Handle permission button clicks
        const approveBtn = element.querySelector('.permission-approve');
        const denyBtn = element.querySelector('.permission-deny');

        if (approveBtn) {
            approveBtn.addEventListener('click', () => {
                const requestId = approveBtn.getAttribute('data-request-id');
                this.handlePermissionDecision(requestId, 'allow');
            });
        }

        if (denyBtn) {
            denyBtn.addEventListener('click', () => {
                const requestId = denyBtn.getAttribute('data-request-id');
                this.handlePermissionDecision(requestId, 'deny');
            });
        }
    }

    toggleToolCallExpansion(toolUseId) {
        const toolCall = this.toolCallManager.toggleToolExpansion(toolUseId);
        if (toolCall) {
            this.updateToolCall(toolCall);
        }
    }

    handlePermissionDecision(requestId, decision) {
        console.log('Permission decision:', requestId, decision);

        // Send permission response to backend
        if (this.sessionWebsocket && this.sessionWebsocket.readyState === WebSocket.OPEN) {
            const response = {
                type: 'permission_response',
                request_id: requestId,
                decision: decision,
                timestamp: new Date().toISOString()
            };

            this.sessionWebsocket.send(JSON.stringify(response));
        }
    }

    renderThinkingBlock(thinkingBlock) {
        console.log('Rendering thinking block:', thinkingBlock);

        const messagesArea = document.getElementById('messages-area');
        const thinkingElement = this.createThinkingBlockElement(thinkingBlock);

        // Add to DOM
        messagesArea.appendChild(thinkingElement);
        this.smartScrollToBottom();
    }

    createThinkingBlockElement(thinkingBlock) {
        const element = document.createElement('div');
        element.className = 'thinking-block-container';
        element.id = `thinking-block-${thinkingBlock.id}`;

        // Store the content as a data attribute on the container for persistence
        element.setAttribute('data-thinking-content', thinkingBlock.content);
        element.setAttribute('data-thinking-id', thinkingBlock.id);

        if (thinkingBlock.isExpanded) {
            element.innerHTML = this.createExpandedThinkingBlockHTML(thinkingBlock);
        } else {
            element.innerHTML = this.createCollapsedThinkingBlockHTML(thinkingBlock);
        }

        // Add event delegation for click handlers
        this.setupThinkingBlockEventListeners(element, thinkingBlock);

        return element;
    }

    createExpandedThinkingBlockHTML(thinkingBlock) {
        return `
            <div class="thinking-block-card">
                <div class="thinking-block-header">
                    <span class="thinking-icon">🧠</span>
                    <span class="thinking-label">Claude's Thinking</span>
                    <button class="thinking-collapse-btn" data-thinking-id="${thinkingBlock.id}" title="Collapse">
                        ▼
                    </button>
                </div>
                <div class="thinking-block-content">
                    <pre class="thinking-text">${this.escapeHtml(thinkingBlock.content)}</pre>
                </div>
            </div>
        `;
    }

    createCollapsedThinkingBlockHTML(thinkingBlock) {
        // Create a truncated preview (first line + ...)
        const firstLine = thinkingBlock.content.split('\n')[0];
        const truncated = firstLine.length > 80 ? firstLine.substring(0, 80) + '...' : firstLine;
        const summary = truncated || 'Claude was thinking...';

        return `
            <div class="thinking-block-collapsed" data-thinking-id="${thinkingBlock.id}" title="Click to expand">
                <span class="thinking-collapsed-summary">🧠 Thinking: ${this.escapeHtml(summary)}</span>
                <span class="thinking-expand-icon">▶</span>
            </div>
        `;
    }

    setupThinkingBlockEventListeners(element, thinkingBlock) {
        // Handle collapse button clicks
        const collapseBtn = element.querySelector('.thinking-collapse-btn');
        if (collapseBtn) {
            collapseBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                this.toggleThinkingBlockExpansion(thinkingBlock.id);
            });
        }

        // Handle collapsed card clicks for expansion
        const collapsedCard = element.querySelector('.thinking-block-collapsed');
        if (collapsedCard) {
            collapsedCard.addEventListener('click', () => {
                this.toggleThinkingBlockExpansion(thinkingBlock.id);
            });
        }
    }

    toggleThinkingBlockExpansion(thinkingId) {
        // Simple implementation - toggle expanded state and re-render
        const element = document.getElementById(`thinking-block-${thinkingId}`);
        if (element) {
            // Find current state from DOM structure
            const isCurrentlyExpanded = element.querySelector('.thinking-block-card') !== null;

            // Create updated thinking block data
            const thinkingBlock = {
                id: thinkingId,
                content: this.extractContentFromElement(element),
                isExpanded: !isCurrentlyExpanded
            };

            // Re-render with new state
            const newElement = this.createThinkingBlockElement(thinkingBlock);
            element.replaceWith(newElement);
        }
    }

    extractContentFromElement(element) {
        // First try to get content from the container's data attribute
        const containerContent = element.getAttribute('data-thinking-content');
        if (containerContent) {
            return containerContent;
        }

        // Fallback: Extract content from expanded thinking text element
        const expandedContent = element.querySelector('.thinking-text');
        if (expandedContent) {
            return expandedContent.textContent;
        }

        // Last resort: Try to get from collapsed element's data attribute
        const collapsedElement = element.querySelector('.thinking-block-collapsed');
        if (collapsedElement) {
            const collapsedContent = collapsedElement.getAttribute('data-content');
            if (collapsedContent) {
                return collapsedContent;
            }
        }

        return 'Content not available';
    }

    async loadSessionInfo() {
        if (!this.currentSessionId) return;

        // Skip if this session is being deleted
        if (this.deletingSessions.has(this.currentSessionId)) {
            console.log(`Skipping loadSessionInfo for session ${this.currentSessionId} - deletion in progress`);
            return;
        }

        // Skip if session no longer exists in our local map
        if (!this.sessions.has(this.currentSessionId)) {
            console.log(`Skipping loadSessionInfo for session ${this.currentSessionId} - not in local sessions map`);
            return;
        }

        try {
            const data = await this.apiRequest(`/api/sessions/${this.currentSessionId}`);
            this.updateSessionInfo(data);
        } catch (error) {
            // If it's a 404, the session was likely deleted - handle gracefully
            if (error.message.includes('404')) {
                console.log(`Session ${this.currentSessionId} not found (404) - likely deleted, clearing from UI`);
                this.handleSessionDeleted(this.currentSessionId);
            } else {
                console.error('Failed to load session info:', error);
            }
        }
    }

    async loadMessages() {
        if (!this.currentSessionId) return;

        try {
            console.log('Loading all messages with pagination...');
            const allMessages = [];
            let offset = 0;
            const pageSize = 50;
            let hasMore = true;

            // Load all messages using pagination
            while (hasMore) {
                console.log(`Loading messages page: offset=${offset}, limit=${pageSize}`);
                const response = await this.apiRequest(
                    `/api/sessions/${this.currentSessionId}/messages?limit=${pageSize}&offset=${offset}`
                );

                // Add messages from this page
                allMessages.push(...response.messages);

                // Check if there are more pages
                hasMore = response.has_more;
                offset += pageSize;

                console.log(`Loaded ${response.messages.length} messages, total so far: ${allMessages.length}, has_more: ${hasMore}`);
            }

            console.log(`Finished loading all ${allMessages.length} messages`);
            this.renderMessages(allMessages);
        } catch (error) {
            console.error('Failed to load messages:', error);
        }
    }

    // UI WebSocket Management (for session state updates)
    connectUIWebSocket() {
        if (this.uiWebsocket && this.uiWebsocket.readyState === WebSocket.OPEN) {
            console.log('UI WebSocket already connected');
            return;
        }

        console.log('Connecting to UI WebSocket...');
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${protocol}//${window.location.host}/ws/ui`;

        this.uiWebsocket = new WebSocket(wsUrl);

        this.uiWebsocket.onopen = () => {
            console.log('UI WebSocket connected successfully');
            this.uiConnectionRetryCount = 0;
        };

        this.uiWebsocket.onmessage = (event) => {
            try {
                const data = JSON.parse(event.data);
                this.handleUIWebSocketMessage(data);
            } catch (error) {
                console.error('Error parsing UI WebSocket message:', error);
            }
        };

        this.uiWebsocket.onclose = (event) => {
            console.log('UI WebSocket disconnected', event.code, event.reason);
            this.uiWebsocket = null;

            // Auto-reconnect UI WebSocket (it should always stay connected)
            if (this.uiConnectionRetryCount < this.maxUIRetries) {
                this.uiConnectionRetryCount++;
                const delay = Math.min(1000 * Math.pow(2, this.uiConnectionRetryCount), 30000);
                console.log(`Reconnecting UI WebSocket in ${delay}ms (attempt ${this.uiConnectionRetryCount}/${this.maxUIRetries})`);

                setTimeout(() => {
                    this.connectUIWebSocket();
                }, delay);
            } else {
                console.log('Max UI WebSocket reconnection attempts reached');
            }
        };

        this.uiWebsocket.onerror = (error) => {
            console.error('UI WebSocket error:', error);
        };
    }

    handleUIWebSocketMessage(data) {
        console.log('UI WebSocket message received:', data.type);

        switch (data.type) {
            case 'sessions_list':
                // Initial sessions list on connection
                this.updateSessionsList(data.data.sessions);
                break;
            case 'state_change':
                // Real-time session state change
                this.handleStateChange(data.data);
                break;
            case 'ping':
                // Respond to server ping
                if (this.uiWebsocket && this.uiWebsocket.readyState === WebSocket.OPEN) {
                    this.uiWebsocket.send(JSON.stringify({type: 'pong', timestamp: new Date().toISOString()}));
                }
                break;
            case 'pong':
                // Server responded to our ping
                console.debug('UI WebSocket pong received');
                break;
            default:
                console.log('Unknown UI WebSocket message type:', data.type);
        }
    }

    updateSessionsList(sessions) {
        console.log(`Updating sessions list with ${sessions.length} sessions`);
        this.sessions.clear();
        // Store sessions in order received from backend (which is sorted by order field)
        this.orderedSessions = [];
        sessions.forEach(session => {
            this.sessions.set(session.session_id, session);
            this.orderedSessions.push(session);
        });
        this.renderSessions();
    }

    async refreshSessions() {
        console.log('Refreshing sessions via API fallback');
        // Fallback to API call if UI WebSocket is not available
        await this.loadSessions();
    }

    // Session WebSocket Management (for message streaming)
    connectSessionWebSocket() {
        if (!this.currentSessionId) return;

        // Only disconnect if we have an existing connection to a different session
        if (this.sessionWebsocket && this.sessionWebsocket.readyState === WebSocket.OPEN) {
            console.log('Closing existing session WebSocket connection before creating new one');
            this.disconnectSessionWebSocket();
        }

        // Reset intentional disconnect flag for new connections
        this.intentionalSessionDisconnect = false;

        console.log(`Connecting session WebSocket for session: ${this.currentSessionId}`);
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${protocol}//${window.location.host}/ws/session/${this.currentSessionId}`;

        try {
            this.sessionWebsocket = new WebSocket(wsUrl);

            this.sessionWebsocket.onopen = () => {
                console.log('WebSocket connected');
                this.updateConnectionStatus('connected');
                this.sessionConnectionRetryCount = 0;
            };

            this.sessionWebsocket.onmessage = (event) => {
                try {
                    const data = JSON.parse(event.data);
                    this.handleWebSocketMessage(data);
                } catch (error) {
                    console.error('Failed to parse WebSocket message:', error);
                }
            };

            this.sessionWebsocket.onclose = (event) => {
                console.log('WebSocket disconnected', event.code, event.reason);
                this.updateConnectionStatus('disconnected');

                // Don't retry if this was an intentional disconnect
                if (this.intentionalSessionDisconnect) {
                    console.log('WebSocket closed intentionally, not retrying');
                    return;
                }

                // Don't retry on specific error codes (session invalid/inactive)
                if (event.code === 4404 || event.code === 4003 || event.code === 4500) {
                    console.log(`WebSocket closed with error code ${event.code}, not retrying`);
                    return;
                }

                this.scheduleReconnect();
            };

            this.sessionWebsocket.onerror = (error) => {
                console.error('WebSocket error:', error);
                this.updateConnectionStatus('disconnected');
            };

        } catch (error) {
            console.error('Failed to create WebSocket:', error);
            this.updateConnectionStatus('disconnected');
        }
    }

    disconnectSessionWebSocket() {
        if (this.sessionWebsocket) {
            // Mark as intentional disconnect to prevent reconnection
            this.intentionalSessionDisconnect = true;
            this.sessionWebsocket.close();
            this.sessionWebsocket = null;
        }
        this.updateConnectionStatus('disconnected');
    }

    scheduleReconnect() {
        // Don't reconnect if this was an intentional disconnect
        if (this.intentionalSessionDisconnect) {
            console.log('Reconnect cancelled due to intentional disconnect');
            return;
        }

        if (this.sessionConnectionRetryCount < this.maxSessionRetries) {
            this.sessionConnectionRetryCount++;
            const delay = Math.min(1000 * Math.pow(2, this.sessionConnectionRetryCount), 30000);

            console.log(`Scheduling WebSocket reconnect in ${delay}ms (attempt ${this.sessionConnectionRetryCount})`);
            setTimeout(() => {
                if (this.currentSessionId && !this.intentionalSessionDisconnect) {
                    this.connectSessionWebSocket();
                }
            }, delay);
        } else {
            console.log('Max reconnection attempts reached');
        }
    }

    handleWebSocketMessage(data) {
        console.log('WebSocket message received:', data);

        switch (data.type) {
            case 'message':
                this.handleIncomingMessage(data.data);
                break;
            case 'state_change':
                this.handleStateChange(data.data);
                break;
            case 'connection_established':
                console.log('WebSocket connection confirmed for session:', data.session_id);
                break;
            case 'ping':
                // Respond to server ping to keep connection alive
                if (this.websocket && this.sessionWebsocket.readyState === WebSocket.OPEN) {
                    this.sessionWebsocket.send(JSON.stringify({type: 'pong', timestamp: new Date().toISOString()}));
                }
                break;
            case 'interrupt_response':
                this.handleInterruptResponse(data);
                break;
            default:
                console.log('Unknown WebSocket message type:', data.type);
        }
    }

    handleInterruptResponse(data) {
        console.log('Interrupt response received:', data);

        if (data.success) {
            console.log('Interrupt successful:', data.message);
            // Interrupt was successful, hide processing indicators
            this.hideProcessingIndicator();
        } else {
            console.warn('Interrupt failed:', data.message);
            // Interrupt failed, return to processing state (not stopping state)
            this.showProcessingIndicator();
        }
    }

    // UI Updates
    async selectSession(sessionId) {
        // If already connected to this session, don't reconnect
        if (this.currentSessionId === sessionId && this.sessionWebsocket && this.sessionWebsocket.readyState === WebSocket.OPEN) {
            console.log(`Already connected to session ${sessionId}`);
            return;
        }

        // Clean disconnect from previous session
        if (this.currentSessionId && this.currentSessionId !== sessionId) {
            console.log(`Switching from session ${this.currentSessionId} to ${sessionId}`);
            this.disconnectSessionWebSocket();
            // Wait a moment for the disconnection to complete
            await new Promise(resolve => setTimeout(resolve, 100));
        }

        this.currentSessionId = sessionId;

        // Processing state will be set by loadSessionInfo() call below

        // Update UI
        document.querySelectorAll('.session-item').forEach(item => {
            item.classList.remove('active');
        });

        const sessionElement = document.querySelector(`[data-session-id="${sessionId}"]`);
        if (sessionElement) {
            sessionElement.classList.add('active');
        }

        // Show chat container
        document.getElementById('no-session-selected').classList.add('hidden');
        document.getElementById('chat-container').classList.remove('hidden');

        // Load session info first to check state
        await this.loadSessionInfo();

        // Check if session needs to be started or is ready for use
        const session = this.sessions.get(sessionId);
        if (session) {
            if (session.state === 'error') {
                // Session is in error state, skip WebSocket initialization
                console.log(`Session ${sessionId} is in error state, skipping WebSocket connection`);
                // Just load messages without attempting to connect
            } else if (session.state === 'active' || session.state === 'running') {
                // Session is already active, just connect WebSocket
                console.log(`Session ${sessionId} is already active, connecting WebSocket`);
                this.connectSessionWebSocket();
            } else if (session.state === 'starting') {
                // Session is starting, wait for it to become active
                console.log(`Session ${sessionId} is starting, waiting for it to become active...`);
                let attempts = 0;
                const maxAttempts = 15;
                const pollInterval = 1000;
                while (attempts < maxAttempts) {
                    await new Promise(resolve => setTimeout(resolve, pollInterval));
                    await this.loadSessionInfo();
                    const updatedSession = this.sessions.get(sessionId);
                    if (updatedSession && updatedSession.state === 'error') {
                        console.log(`Session ${sessionId} entered error state during startup, stopping wait`);
                        break;
                    } else if (updatedSession && (updatedSession.state === 'active' || updatedSession.state === 'running')) {
                        console.log(`Session ${sessionId} is now active, connecting WebSocket`);
                        this.connectSessionWebSocket();
                        break;
                    }
                    attempts++;
                    console.log(`Waiting for session ${sessionId} to become active... (attempt ${attempts}/${maxAttempts})`);
                }

                if (attempts >= maxAttempts) {
                    console.warn(`Session ${sessionId} did not become active after ${maxAttempts} attempts (${maxAttempts * pollInterval / 1000} seconds)`);
                }
            } else {
                // Session needs to be started (both fresh sessions and existing sessions)
                // The server-side logic will handle whether to create fresh or resume based on claude_code_session_id
                console.log(`Starting session ${sessionId} (current state: ${session.state})`);
                await this.apiRequest(`/api/sessions/${sessionId}/start`, { method: 'POST' });

                // Wait for session to be fully active before connecting WebSocket
                let attempts = 0;
                const maxAttempts = 15; // Increased from 10 to allow for longer SDK initialization
                const pollInterval = 1000; // Increased from 200ms to 1 second
                while (attempts < maxAttempts) {
                    await new Promise(resolve => setTimeout(resolve, pollInterval));
                    await this.loadSessionInfo();
                    const updatedSession = this.sessions.get(sessionId);
                    if (updatedSession && updatedSession.state === 'error') {
                        console.log(`Session ${sessionId} entered error state during startup, stopping wait`);
                        break;
                    } else if (updatedSession && (updatedSession.state === 'active' || updatedSession.state === 'running')) {
                        console.log(`Session ${sessionId} is now active, connecting WebSocket`);
                        this.connectSessionWebSocket();
                        break;
                    }
                    attempts++;
                    console.log(`Waiting for session ${sessionId} to become active... (attempt ${attempts}/${maxAttempts})`);
                }

                if (attempts >= maxAttempts) {
                    console.warn(`Session ${sessionId} did not become active after ${maxAttempts} attempts (${maxAttempts * pollInterval / 1000} seconds)`);
                }
            }
        }

        // Load messages after session is ready
        this.loadMessages();

        // Load session info to get current processing state from backend
        this.loadSessionInfo();
    }

    renderSessions() {
        const container = document.getElementById('sessions-container');
        container.innerHTML = '';

        if (this.orderedSessions.length === 0) {
            container.innerHTML = '<p class="text-muted">No sessions available</p>';
            return;
        }

        this.orderedSessions.forEach((session) => {
            const sessionId = session.session_id;
            const sessionElement = document.createElement('div');
            sessionElement.className = 'session-item';

            // Add active class if this is the currently selected session
            if (sessionId === this.currentSessionId) {
                sessionElement.classList.add('active');
            }

            sessionElement.setAttribute('data-session-id', sessionId);

            // Add drag-and-drop attributes
            sessionElement.draggable = true;
            sessionElement.setAttribute('data-order', session.order || 999999);
            sessionElement.addEventListener('click', (e) => {
                // Don't select session if clicking on input field or name display during editing
                if (e.target.tagName === 'INPUT') return;
                if (e.target.classList.contains('session-name-display') &&
                    e.target.parentElement.querySelector('.session-name-edit').style.display !== 'none') {
                    return; // Name is being edited, ignore click
                }
                this.selectSession(sessionId);
            });

            // Add drag-and-drop event listeners
            this.addDragDropListeners(sessionElement, sessionId);

            // Create status indicator - show processing state if is_processing is true
            const isProcessing = session.is_processing || false;
            const displayState = isProcessing ? 'processing' : session.state;
            const statusIndicator = this.createStatusIndicator(displayState, 'session', session.state);

            // Use session name if available, fallback to session ID
            const displayName = session.name || sessionId;

            sessionElement.innerHTML = `
                <div class="session-header">
                    <div class="session-name" title="${sessionId}">
                        <span class="session-name-display">${this.escapeHtml(displayName)}</span>
                        <input class="session-name-edit" type="text" value="${this.escapeHtml(displayName)}" style="display: none;">
                    </div>
                </div>
            `;

            // Insert status indicator at the beginning
            const sessionHeader = sessionElement.querySelector('.session-header');
            sessionHeader.insertBefore(statusIndicator, sessionHeader.firstChild);

            // Add double-click editing functionality
            const nameDisplay = sessionElement.querySelector('.session-name-display');
            const nameInput = sessionElement.querySelector('.session-name-edit');

            nameDisplay.addEventListener('dblclick', (e) => {
                e.stopPropagation();
                this.startEditingSessionName(sessionId, nameDisplay, nameInput);
            });

            this.setupSessionNameInput(sessionId, nameDisplay, nameInput);

            container.appendChild(sessionElement);
        });
    }

    startEditingSessionName(sessionId, nameDisplay, nameInput) {
        // Hide display, show input
        nameDisplay.style.display = 'none';
        nameInput.style.display = 'inline-block';
        nameInput.focus();
        nameInput.select();
    }

    setupSessionNameInput(sessionId, nameDisplay, nameInput) {
        // Handle Enter key to save
        nameInput.addEventListener('keydown', (e) => {
            e.stopPropagation();
            if (e.key === 'Enter') {
                this.saveSessionName(sessionId, nameDisplay, nameInput);
            } else if (e.key === 'Escape') {
                this.cancelEditingSessionName(nameDisplay, nameInput);
            }
        });

        // Handle click outside to cancel
        nameInput.addEventListener('blur', () => {
            this.cancelEditingSessionName(nameDisplay, nameInput);
        });
    }

    async saveSessionName(sessionId, nameDisplay, nameInput) {
        const newName = nameInput.value.trim();
        if (!newName) {
            this.cancelEditingSessionName(nameDisplay, nameInput);
            return;
        }

        try {
            const response = await this.apiRequest(`/api/sessions/${sessionId}/name`, {
                method: 'PUT',
                body: JSON.stringify({ name: newName })
            });

            if (response.success) {
                // Update local session data
                if (this.sessions.has(sessionId)) {
                    const session = this.sessions.get(sessionId);
                    session.name = newName;

                    // Update session data consistently (with re-render since we're done editing)
                    this.updateSessionData(sessionId, session);
                }

                // Update display
                nameDisplay.textContent = newName;
                nameDisplay.style.display = 'inline-block';
                nameInput.style.display = 'none';

                // Update header if this is the current session
                if (sessionId === this.currentSessionId) {
                    this.updateSessionHeaderName(newName);
                }
            } else {
                throw new Error('Failed to update session name');
            }
        } catch (error) {
            console.error('Failed to save session name:', error);
            this.cancelEditingSessionName(nameDisplay, nameInput);
            this.showError('Failed to update session name');
        }
    }

    cancelEditingSessionName(nameDisplay, nameInput) {
        // Reset input value to original
        nameInput.value = nameDisplay.textContent;
        // Show display, hide input
        nameDisplay.style.display = 'inline-block';
        nameInput.style.display = 'none';
    }

    updateSessionHeaderName(name) {
        // Update the header to display session name instead of ID
        const currentSessionIdElement = document.getElementById('current-session-id');
        if (currentSessionIdElement) {
            currentSessionIdElement.textContent = name;
        }
    }

    updateSessionInfo(sessionData) {
        // Display session name if available, fallback to session ID
        const sessionName = sessionData.session.name || this.currentSessionId;
        document.getElementById('current-session-id').textContent = sessionName;

        // Update session state indicator
        const stateContainer = document.getElementById('current-session-state');
        stateContainer.innerHTML = '';

        // Create new status dot - show processing state if is_processing is true
        const isProcessing = sessionData.session.is_processing || false;
        const displayState = isProcessing ? 'processing' : sessionData.session.state;
        const statusDot = this.createStatusIndicator(displayState, 'session', sessionData.session.state);
        stateContainer.appendChild(statusDot);
        stateContainer.className = `session-state-indicator ${displayState}`;

        // Handle error state display
        const sessionInfoBar = document.getElementById('session-info-bar');
        const errorMessageElement = document.getElementById('session-error-message');

        if (sessionData.session.state === 'error' && sessionData.session.error_message) {
            // Show error message in top bar
            errorMessageElement.textContent = sessionData.session.error_message;
            errorMessageElement.classList.remove('hidden');
            sessionInfoBar.classList.add('error');
            console.log('Displaying error message in top bar:', sessionData.session.error_message);

            // For error state: clear any processing indicator and disable input controls
            this.updateProcessingState(false);
            this.setInputControlsEnabled(false);
        } else {
            // Hide error message and remove error styling
            errorMessageElement.classList.add('hidden');
            sessionInfoBar.classList.remove('error');

            // Check processing state from backend and update UI accordingly
            const backendProcessingState = sessionData.session.is_processing || false;
            this.updateProcessingState(backendProcessingState);

            // For non-error sessions: enable controls if not processing, keep disabled if processing
            if (!backendProcessingState) {
                this.setInputControlsEnabled(true);
            }
        }

        // Update the sessions Map with current session state
        if (this.currentSessionId && this.sessions.has(this.currentSessionId)) {
            const existingSession = this.sessions.get(this.currentSessionId);
            existingSession.state = sessionData.session.state;
            existingSession.error_message = sessionData.session.error_message;
            existingSession.is_processing = sessionData.session.is_processing || false;

            // Update session data consistently (with automatic re-render)
            this.updateSessionData(this.currentSessionId, existingSession);
        }
    }

    renderMessages(messages) {
        const messagesArea = document.getElementById('messages-area');
        messagesArea.innerHTML = '';

        // Clear the tool call manager for fresh loading
        this.toolCallManager = new ToolCallManager();

        // Single-pass processing for historical messages using unified logic
        console.log(`Processing ${messages.length} historical messages with unified single-pass approach`);

        let toolUseCount = 0;
        messages.forEach(message => {
            // Process each message in order - tool calls will be created as needed
            const result = this.processMessage(message, 'historical');

            // Count tool uses for logging
            if (message.type === 'assistant' && message.metadata && message.metadata.tool_uses) {
                toolUseCount += message.metadata.tool_uses.length;
            }
        });

        console.log(`Single-pass processing complete: Found ${toolUseCount} tool uses in ${messages.length} messages`);
        this.smartScrollToBottom();
    }

    addMessageToUI(message, scroll = true) {
        const messagesArea = document.getElementById('messages-area');
        const messageElement = document.createElement('div');

        // Use metadata for enhanced styling if available
        const subtype = message.subtype || message.metadata?.subtype;
        const messageClass = subtype ? `message ${message.type} ${subtype}` : `message ${message.type}`;
        messageElement.className = messageClass;

        // Add tooltip for client_launched messages
        if (message.type === 'system' && subtype === 'client_launched') {
            messageElement.title = `Session ID: ${message.session_id || 'Unknown'}`;
        }

        const timestamp = new Date(message.timestamp).toLocaleTimeString();

        // Build content using standardized approach
        let contentHtml = '';
        const content = message.content || '';

        // Determine display mode based on message type and metadata
        const shouldShowMetadata = this._shouldShowMetadata(message);
        const shouldShowJsonContent = this.isJsonContent(content);

        if (shouldShowMetadata || shouldShowJsonContent) {
            // Show primary text content
            if (content) {
                contentHtml += `<div class="message-content">${this.escapeHtml(content)}</div>`;
            }

            // Show metadata or JSON content for debugging/system messages
            if (shouldShowMetadata) {
                const debugData = this._getDisplayMetadata(message);
                if (debugData) {
                    contentHtml += `<div class="message-json">${this.formatJson(debugData)}</div>`;
                }
            } else if (shouldShowJsonContent) {
                const jsonData = this.tryParseJson(content);
                if (jsonData) {
                    contentHtml += `<div class="message-json">${this.formatJson(jsonData)}</div>`;
                }
            }
        } else {
            // Regular text content with smart formatting
            contentHtml = this._formatMessageContent(message, content);
        }

        // Special handling for session state messages (client_launched, interrupt)
        if (message.type === 'system' && (subtype === 'client_launched' || subtype === 'interrupt')) {
            // Simple one-liner for session state messages - just the content
            messageElement.innerHTML = `${contentHtml}`;
        } else {
            // Regular message format with header and timestamp
            const headerText = this._getMessageHeader(message);
            messageElement.innerHTML = `
                <div class="message-header">${headerText}</div>
                ${contentHtml}
                <div class="message-timestamp">${timestamp}</div>
            `;
        }

        messagesArea.appendChild(messageElement);

        if (scroll) {
            this.smartScrollToBottom();
        }
    }

    /**
     * Determine if metadata should be shown for debugging purposes
     */
    _shouldShowMetadata(message) {
        // Show metadata for result messages (internal completion data)
        if (message.type === 'result') {
            return true;
        }

        // For system messages, only show metadata for debugging/error types
        if (message.type === 'system') {
            const subtype = message.subtype || message.metadata?.subtype;
            // Show metadata for internal/debug system messages, not user-facing ones
            if (subtype === 'interrupt' || subtype === 'client_launched' || subtype === 'init') {
                return false; // User-facing system messages should be clean
            }
            return true; // Other system messages (errors, etc.) show metadata
        }

        // Don't show metadata for regular user and assistant messages
        if (message.type === 'user' || message.type === 'assistant') {
            return false;
        }

        // Show metadata for other message types or unusual cases
        return false;
    }

    /**
     * Get relevant metadata for display (excluding raw data)
     */
    _getDisplayMetadata(message) {
        if (!message.metadata) {
            return null;
        }

        // Create clean metadata object excluding raw SDK data
        const displayMetadata = {};

        for (const [key, value] of Object.entries(message.metadata)) {
            // Skip raw SDK data fields
            if (key === 'raw_sdk_message' || key === 'raw_sdk_response' || key === 'sdk_message') {
                continue;
            }

            // Skip processing metadata
            if (key === 'source' || key === 'processed_at') {
                continue;
            }

            // Include relevant metadata
            displayMetadata[key] = value;
        }

        return Object.keys(displayMetadata).length > 0 ? displayMetadata : null;
    }

    /**
     * Format message content with smart handling
     */
    _formatMessageContent(message, content) {
        // Handle empty content
        if (!content) {
            // Show placeholder for empty user messages that might have been tool results
            if (message.type === 'user' && message.metadata?.has_tool_results) {
                return `<div class="message-content message-empty">[Tool results handled by tool call UI]</div>`;
            }
            return `<div class="message-content message-empty">[Empty message]</div>`;
        }

        // Regular content with proper escaping
        return `<div class="message-content">${this.escapeHtml(content)}</div>`;
    }

    /**
     * Get enhanced message header with subtype information
     */
    _getMessageHeader(message) {
        const subtype = message.subtype || message.metadata?.subtype;

        if (subtype) {
            return `${message.type} (${subtype})`;
        }

        return message.type;
    }

    handleStateChange(stateData) {
        console.log('Session state changed:', stateData);

        // Update specific session in real-time instead of reloading all sessions
        const sessionId = stateData.session_id;
        const sessionInfo = stateData.session;

        // Skip processing if this session is being deleted
        if (this.deletingSessions.has(sessionId)) {
            console.log(`Ignoring state change for session ${sessionId} - deletion in progress`);
            return;
        }

        if (sessionInfo) {
            // Update session data consistently (with automatic re-render)
            this.updateSessionData(sessionId, sessionInfo);

            // If this is the current session, update the session info display
            if (sessionId === this.currentSessionId) {
                this.updateSessionInfo({ session: sessionInfo });
            }
        }
    }

    updateSpecificSession(sessionId, sessionInfo) {
        // Skip if session is being deleted
        if (this.deletingSessions.has(sessionId)) {
            return;
        }

        // Update session data consistently (with automatic re-render)
        this.updateSessionData(sessionId, sessionInfo);

        if (sessionId === this.currentSessionId) {
            this.updateSessionInfo({ session: sessionInfo });
        }
    }

    handleSessionDeleted(sessionId) {
        // Clean up a session that was deleted externally
        console.log(`Handling external deletion of session ${sessionId}`);

        // Remove from sessions map
        this.sessions.delete(sessionId);

        // Remove from orderedSessions array
        this.orderedSessions = this.orderedSessions.filter(s => s.session_id !== sessionId);

        // If it was the current session, exit it
        if (this.currentSessionId === sessionId) {
            this.exitSession();
        }

        // Re-render sessions
        this.renderSessions();
    }

    updateConnectionStatus(status) {
        const indicatorContainer = document.getElementById('connection-indicator');

        // Clear existing indicator
        indicatorContainer.innerHTML = '';

        // Create new status dot
        const statusDot = this.createStatusIndicator(status, 'websocket', status);
        indicatorContainer.appendChild(statusDot);

        // Keep the container class for any legacy styling
        indicatorContainer.className = `connection-status-indicator ${status}`;
    }

    // Modal Management
    showCreateSessionModal() {
        document.getElementById('working-directory').value = '.';
        document.getElementById('create-session-modal').classList.remove('hidden');
    }

    hideCreateSessionModal() {
        document.getElementById('create-session-modal').classList.add('hidden');
        document.getElementById('create-session-form').reset();
    }

    handleCreateSession(event) {
        event.preventDefault();

        const formData = new FormData(event.target);
        const data = Object.fromEntries(formData.entries());

        this.createSession(data)
            .then(() => {
                this.hideCreateSessionModal();
            })
            .catch(error => {
                console.error('Session creation failed:', error);
            });
    }

    browseDirectory() {
        // In a real implementation, this would open a directory picker
        // For now, we'll just suggest using the current directory
        const input = document.getElementById('working-directory');
        if (!input.value) {
            input.value = prompt('Enter working directory:', '/') || input.value;
        }
    }

    showDeleteSessionModal() {
        if (!this.currentSessionId) return;

        // Get session info to display the name
        const session = this.sessions.get(this.currentSessionId);
        const sessionName = session?.name || this.currentSessionId;

        // Update modal content
        document.getElementById('delete-session-name').textContent = sessionName;
        document.getElementById('delete-session-modal').classList.remove('hidden');
    }

    hideDeleteSessionModal() {
        document.getElementById('delete-session-modal').classList.add('hidden');
    }

    async confirmDeleteSession() {
        if (!this.currentSessionId) return;

        const sessionIdToDelete = this.currentSessionId;

        try {
            this.showLoading(true);

            // Mark session as being deleted to prevent race conditions
            this.deletingSessions.add(sessionIdToDelete);

            // Remove from local sessions map immediately to prevent further operations
            this.sessions.delete(sessionIdToDelete);

            const response = await this.apiRequest(`/api/sessions/${sessionIdToDelete}`, {
                method: 'DELETE'
            });

            if (response.success) {
                // Session successfully deleted
                this.hideDeleteSessionModal();

                // If this was the current session, exit it
                if (this.currentSessionId === sessionIdToDelete) {
                    this.exitSession();
                }

                // Refresh the sessions list
                this.renderSessions();

                console.log(`Session ${sessionIdToDelete} deleted successfully`);
            } else {
                // Restore session to map if deletion failed
                const sessionData = await this.apiRequest(`/api/sessions/${sessionIdToDelete}`).catch(() => null);
                if (sessionData) {
                    this.sessions.set(sessionIdToDelete, sessionData.session);
                }
                throw new Error('Failed to delete session');
            }
        } catch (error) {
            console.error('Failed to delete session:', error);
            this.showError(`Failed to delete session: ${error.message}`);

            // Try to restore session to map if deletion failed
            try {
                const sessionData = await this.apiRequest(`/api/sessions/${sessionIdToDelete}`);
                if (sessionData && sessionData.session) {
                    this.sessions.set(sessionIdToDelete, sessionData.session);
                    this.renderSessions();
                }
            } catch (restoreError) {
                console.log('Could not restore session data after failed deletion');
            }
        } finally {
            // Always remove from deleting set
            this.deletingSessions.delete(sessionIdToDelete);
            this.showLoading(false);
        }
    }

    // Utility Methods
    showLoading(show) {
        const overlay = document.getElementById('loading-overlay');
        if (show) {
            overlay.classList.remove('hidden');
        } else {
            overlay.classList.add('hidden');
        }
    }

    showError(message) {
        alert(`Error: ${message}`);
    }

    setInputControlsEnabled(enabled) {
        const messageInput = document.getElementById('message-input');
        const sendButton = document.getElementById('send-btn');

        if (enabled) {
            // Enable input controls
            messageInput.disabled = false;
            sendButton.disabled = false;
            messageInput.placeholder = "Type your message to Claude Code...";
            console.log('Input controls enabled');
        } else {
            // Disable input controls for error state
            messageInput.disabled = true;
            sendButton.disabled = true;
            messageInput.placeholder = "Session is in error state - input disabled";
            console.log('Input controls disabled due to error state');
        }
    }

    // Auto-scroll functionality
    toggleAutoScroll() {
        this.autoScrollEnabled = !this.autoScrollEnabled;
        const button = document.getElementById('auto-scroll-toggle');

        if (this.autoScrollEnabled) {
            button.textContent = '📜 Auto-scroll: ON';
            button.className = 'btn btn-small btn-secondary auto-scroll-enabled';
            this.smartScrollToBottom();
        } else {
            button.textContent = '📜 Auto-scroll: OFF';
            button.className = 'btn btn-small btn-secondary auto-scroll-disabled';
        }
    }

    handleScroll(event) {
        if (this.scrollTimeout) {
            clearTimeout(this.scrollTimeout);
        }

        // Only mark as user scrolling if they scroll away from the bottom
        if (!this.isAtBottom()) {
            this.isUserScrolling = true;
        } else {
            // If user scrolls to bottom, don't consider it user scrolling
            this.isUserScrolling = false;
        }

        // Reset user scrolling flag after a delay
        this.scrollTimeout = setTimeout(() => {
            this.isUserScrolling = false;
        }, 1500);
    }

    isAtBottom() {
        const messagesArea = document.getElementById('messages-area');
        const threshold = 100; // pixels from bottom
        return messagesArea.scrollTop + messagesArea.clientHeight >= messagesArea.scrollHeight - threshold;
    }

    smartScrollToBottom() {
        if (!this.autoScrollEnabled) {
            return;
        }

        // Always scroll if user is not actively scrolling
        // Or if user is scrolling but near the bottom
        if (!this.isUserScrolling || this.isAtBottom()) {
            // Use requestAnimationFrame for smoother scrolling
            requestAnimationFrame(() => {
                this.scrollToBottom();
            });
        }
    }

    scrollToBottom() {
        const messagesArea = document.getElementById('messages-area');
        messagesArea.scrollTop = messagesArea.scrollHeight;
    }

    // Message formatting utilities
    isJsonContent(content) {
        if (typeof content !== 'string') return false;
        try {
            JSON.parse(content);
            return content.trim().startsWith('{') || content.trim().startsWith('[');
        } catch {
            return false;
        }
    }

    tryParseJson(content) {
        try {
            return JSON.parse(content);
        } catch {
            return null;
        }
    }

    formatJson(obj) {
        try {
            return JSON.stringify(obj, null, 2);
        } catch {
            return String(obj);
        }
    }

    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    // Session Data Management Helper
    updateSessionData(sessionId, sessionInfo, skipRender = false) {
        // Update sessions Map
        this.sessions.set(sessionId, sessionInfo);

        // Update orderedSessions array
        const index = this.orderedSessions.findIndex(s => s.session_id === sessionId);
        if (index !== -1) {
            this.orderedSessions[index] = sessionInfo;
        } else {
            // Session not found in ordered list, add it
            console.warn(`Session ${sessionId} not found in orderedSessions, adding it`);
            this.orderedSessions.push(sessionInfo);
        }

        // Optionally trigger re-render
        if (!skipRender) {
            this.renderSessions();
        }
    }

    // Drag and Drop Methods
    addDragDropListeners(sessionElement, sessionId) {
        // Store drag state
        if (!this.dragState) {
            this.dragState = {
                draggedElement: null,
                draggedSessionId: null,
                dropIndicator: null,
                insertBefore: false
            };
        }

        sessionElement.addEventListener('dragstart', (e) => {
            this.dragState.draggedElement = sessionElement;
            this.dragState.draggedSessionId = sessionId;
            sessionElement.classList.add('dragging');

            // Set drag data
            e.dataTransfer.effectAllowed = 'move';
            e.dataTransfer.setData('text/plain', sessionId);
        });

        sessionElement.addEventListener('dragend', (e) => {
            sessionElement.classList.remove('dragging');
            this.removeDragVisualEffects();
        });

        sessionElement.addEventListener('dragover', (e) => {
            e.preventDefault();
            e.dataTransfer.dropEffect = 'move';

            if (this.dragState.draggedElement && sessionElement !== this.dragState.draggedElement) {
                this.showDropIndicator(sessionElement, e);
            }
        });

        sessionElement.addEventListener('dragenter', (e) => {
            e.preventDefault();
        });

        sessionElement.addEventListener('dragleave', (e) => {
            // Only hide indicator if we're actually leaving the element
            if (!sessionElement.contains(e.relatedTarget)) {
                this.hideDropIndicator();
            }
        });

        sessionElement.addEventListener('drop', (e) => {
            e.preventDefault();
            this.handleSessionDrop(sessionElement, sessionId);
        });
    }

    showDropIndicator(targetElement, event) {
        this.hideDropIndicator();

        const rect = targetElement.getBoundingClientRect();
        const midpoint = rect.top + rect.height / 2;
        const insertBefore = event.clientY < midpoint;

        // Create drop indicator
        const indicator = document.createElement('div');
        indicator.className = 'drop-indicator';
        indicator.textContent = ''; // Empty line to show where drop will occur

        this.dragState.dropIndicator = indicator;
        this.dragState.insertBefore = insertBefore; // Store the insert position

        if (insertBefore) {
            targetElement.parentNode.insertBefore(indicator, targetElement);
        } else {
            targetElement.parentNode.insertBefore(indicator, targetElement.nextSibling);
        }
    }

    hideDropIndicator() {
        if (this.dragState.dropIndicator) {
            this.dragState.dropIndicator.remove();
            this.dragState.dropIndicator = null;
            this.dragState.insertBefore = false;
        }
    }

    removeDragVisualEffects() {
        this.hideDropIndicator();
        // Remove any other drag effects
        document.querySelectorAll('.session-item.dragging').forEach(el => {
            el.classList.remove('dragging');
        });
    }

    async handleSessionDrop(targetElement, targetSessionId) {
        if (!this.dragState.draggedSessionId || this.dragState.draggedSessionId === targetSessionId) {
            this.removeDragVisualEffects();
            return;
        }

        const draggedSessionId = this.dragState.draggedSessionId;

        try {
            // Calculate new order based on drop position
            const newOrder = this.calculateNewOrder(draggedSessionId, targetSessionId);

            // Call reorder API
            await this.reorderSessions(newOrder);

        } catch (error) {
            console.error('Failed to reorder sessions:', error);
            this.showError('Failed to reorder sessions');
        } finally {
            this.removeDragVisualEffects();
        }
    }

    calculateNewOrder(draggedSessionId, targetSessionId) {
        // Get current session IDs in order
        const currentOrder = this.orderedSessions.map(s => s.session_id);

        // Remove dragged session from current position
        const filteredOrder = currentOrder.filter(id => id !== draggedSessionId);

        // Find target position
        const targetIndex = filteredOrder.indexOf(targetSessionId);

        // Use the stored insert position from showDropIndicator
        const insertBefore = this.dragState.insertBefore || false;

        // Insert dragged session at new position
        const newIndex = insertBefore ? targetIndex : targetIndex + 1;
        filteredOrder.splice(newIndex, 0, draggedSessionId);

        return filteredOrder;
    }

    async reorderSessions(sessionIds) {
        try {
            const response = await this.apiRequest('/api/sessions/reorder', {
                method: 'PUT',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    session_ids: sessionIds,
                    project_id: null // For now, no project grouping
                })
            });

            if (response.success) {
                console.log('Sessions reordered successfully');
                // Explicitly refresh session list to ensure immediate update
                await this.refreshSessions();
            } else {
                throw new Error('Reorder request failed');
            }
        } catch (error) {
            console.error('Failed to reorder sessions via API:', error);
            throw error;
        }
    }

    // Sidebar Management
    toggleSidebar() {
        const sidebar = document.getElementById('sidebar');
        this.sidebarCollapsed = !this.sidebarCollapsed;

        if (this.sidebarCollapsed) {
            // Store current width before collapsing
            this.sidebarWidth = sidebar.offsetWidth;
            // Force collapse width via inline style to override any previous inline styles
            sidebar.style.width = '50px';
            sidebar.classList.add('collapsed');
            document.getElementById('sidebar-collapse-btn').title = 'Expand sidebar';
        } else {
            // Restore the previous width
            sidebar.style.width = `${this.sidebarWidth}px`;
            sidebar.classList.remove('collapsed');
            document.getElementById('sidebar-collapse-btn').title = 'Collapse sidebar';
        }
    }

    startResize(e) {
        // Don't allow resize when collapsed
        if (this.sidebarCollapsed) return;

        this.isResizing = true;
        document.addEventListener('mousemove', this.handleResize.bind(this));
        document.addEventListener('mouseup', this.stopResize.bind(this));
        e.preventDefault();
    }

    handleResize(e) {
        if (!this.isResizing) return;

        const sidebar = document.getElementById('sidebar');
        const containerRect = document.querySelector('.main-content').getBoundingClientRect();
        const newWidth = e.clientX - containerRect.left;

        // Enforce constraints: min 200px, max 30% of viewport width
        const minWidth = 200;
        const maxWidth = window.innerWidth * 0.3;
        const constrainedWidth = Math.max(minWidth, Math.min(newWidth, maxWidth));

        this.sidebarWidth = constrainedWidth;
        sidebar.style.width = `${constrainedWidth}px`;
    }

    stopResize() {
        this.isResizing = false;
        document.removeEventListener('mousemove', this.handleResize.bind(this));
        document.removeEventListener('mouseup', this.stopResize.bind(this));
    }

    handleWindowResize() {
        if (!this.sidebarCollapsed) {
            const sidebar = document.getElementById('sidebar');
            const maxWidth = window.innerWidth * 0.3;

            // Ensure sidebar doesn't exceed 30% of new window width
            if (this.sidebarWidth > maxWidth) {
                this.sidebarWidth = maxWidth;
                sidebar.style.width = `${maxWidth}px`;
            }
        }
    }
}

// Initialize the application when the DOM is loaded
document.addEventListener('DOMContentLoaded', () => {
    window.claudeWebUI = new ClaudeWebUI();
    window.app = window.claudeWebUI; // Make app available globally for onclick handlers
});

// Handle page unload
window.addEventListener('beforeunload', () => {
    if (window.claudeWebUI) {
        if (window.claudeWebUI.sessionWebsocket) {
            window.claudeWebUI.disconnectSessionWebSocket();
        }
        if (window.claudeWebUI.uiWebsocket) {
            window.claudeWebUI.uiWebsocket.close();
        }
    }
});