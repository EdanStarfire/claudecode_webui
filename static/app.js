// Claude Code WebUI JavaScript Application

/**
 * Standardized logging utility for consistent log formatting
 * Uses browser console's native log levels for filtering
 */
const Logger = {
    /**
     * Format a log message with timestamp and category
     * @param {string} category - Log category (e.g., 'TOOL_MANAGER', 'SESSION', 'WS')
     * @param {string} message - The log message
     * @param {*} data - Optional structured data to append
     * @returns {string} Formatted log message
     */
    formatMessage(category, message, data = null) {
        const timestamp = new Date().toISOString();
        const baseMsg = `${timestamp} - [${category}] ${message}`;

        if (data !== null && data !== undefined) {
            // For objects/arrays, return base message (data will be passed separately to console)
            return baseMsg;
        }

        return baseMsg;
    },

    /**
     * Debug level - verbose debugging, filtered by browser settings
     */
    debug(category, message, data = null) {
        if (data !== null && data !== undefined) {
            console.debug(this.formatMessage(category, message), data);
        } else {
            console.debug(this.formatMessage(category, message));
        }
    },

    /**
     * Info level - important events (connections, actions, completions)
     */
    info(category, message, data = null) {
        if (data !== null && data !== undefined) {
            console.info(this.formatMessage(category, message), data);
        } else {
            console.info(this.formatMessage(category, message));
        }
    },

    /**
     * Warning level - warnings (retries, fallbacks, deprecated paths)
     */
    warn(category, message, data = null) {
        if (data !== null && data !== undefined) {
            console.warn(this.formatMessage(category, message), data);
        } else {
            console.warn(this.formatMessage(category, message));
        }
    },

    /**
     * Error level - errors (failures, exceptions)
     */
    error(category, message, data = null) {
        if (data !== null && data !== undefined) {
            console.error(this.formatMessage(category, message), data);
        } else {
            console.error(this.formatMessage(category, message));
        }
    }
};

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
        // Check if diff libraries are loaded
        if (typeof Diff === 'undefined' || typeof Diff2Html === 'undefined') {
            console.error('Diff libraries not loaded. Using fallback diff view.');
            return this.generateFallbackDiffView(oldString, newString, escapeHtmlFn);
        }

        try {
            // Use diff library to create unified diff format
            const patch = Diff.createPatch('file', oldString, newString, '', '', { context: 3 });

            // Use diff2html to render the diff
            const diffHtml = Diff2Html.html(patch, {
                drawFileList: false,
                matching: 'lines',
                outputFormat: 'line-by-line',
                highlight: false // Disable syntax highlighting for plain text diffs
            });

            return diffHtml;
        } catch (error) {
            console.error('Error generating diff with diff2html:', error);
            return this.generateFallbackDiffView(oldString, newString, escapeHtmlFn);
        }
    }

    generateFallbackDiffView(oldString, newString, escapeHtmlFn) {
        // Simple fallback diff view
        const oldLines = oldString.split('\n');
        const newLines = newString.split('\n');
        const maxLines = Math.max(oldLines.length, newLines.length);

        let diffHtml = '<div class="simple-diff-view" style="font-family: monospace; font-size: 0.85rem; background: #f8f9fa; border-radius: 0.25rem; padding: 0.5rem;">';

        for (let i = 0; i < maxLines; i++) {
            const oldLine = i < oldLines.length ? oldLines[i] : null;
            const newLine = i < newLines.length ? newLines[i] : null;

            if (oldLine !== null && newLine !== null && oldLine === newLine) {
                diffHtml += `<div style="padding: 0.125rem 0;"> ${escapeHtmlFn(oldLine)}</div>`;
            } else {
                if (oldLine !== null) {
                    diffHtml += `<div style="background-color: #f8d7da; padding: 0.125rem 0;"><span style="color: #dc3545; font-weight: bold;">-</span> ${escapeHtmlFn(oldLine)}</div>`;
                }
                if (newLine !== null) {
                    diffHtml += `<div style="background-color: #d4edda; padding: 0.125rem 0;"><span style="color: #28a745; font-weight: bold;">+</span> ${escapeHtmlFn(newLine)}</div>`;
                }
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
        // Check if diff libraries are loaded
        if (typeof Diff === 'undefined' || typeof Diff2Html === 'undefined') {
            console.error('Diff libraries not loaded. Using fallback diff view.');
            return this.generateFallbackDiffView(oldString, newString, escapeHtmlFn);
        }

        try {
            // Use diff library to create unified diff format
            const patch = Diff.createPatch('file', oldString, newString, '', '', { context: 3 });

            // Use diff2html to render the diff
            const diffHtml = Diff2Html.html(patch, {
                drawFileList: false,
                matching: 'lines',
                outputFormat: 'line-by-line',
                highlight: false // Disable syntax highlighting for plain text diffs
            });

            return diffHtml;
        } catch (error) {
            console.error('Error generating diff with diff2html:', error);
            return this.generateFallbackDiffView(oldString, newString, escapeHtmlFn);
        }
    }

    generateFallbackDiffView(oldString, newString, escapeHtmlFn) {
        // Simple fallback diff view
        const oldLines = oldString.split('\n');
        const newLines = newString.split('\n');
        const maxLines = Math.max(oldLines.length, newLines.length);

        let diffHtml = '<div class="simple-diff-view" style="font-family: monospace; font-size: 0.85rem; background: #f8f9fa; border-radius: 0.25rem; padding: 0.5rem;">';

        for (let i = 0; i < maxLines; i++) {
            const oldLine = i < oldLines.length ? oldLines[i] : null;
            const newLine = i < newLines.length ? newLines[i] : null;

            if (oldLine !== null && newLine !== null && oldLine === newLine) {
                diffHtml += `<div style="padding: 0.125rem 0;"> ${escapeHtmlFn(oldLine)}</div>`;
            } else {
                if (oldLine !== null) {
                    diffHtml += `<div style="background-color: #f8d7da; padding: 0.125rem 0;"><span style="color: #dc3545; font-weight: bold;">-</span> ${escapeHtmlFn(oldLine)}</div>`;
                }
                if (newLine !== null) {
                    diffHtml += `<div style="background-color: #d4edda; padding: 0.125rem 0;"><span style="color: #28a745; font-weight: bold;">+</span> ${escapeHtmlFn(newLine)}</div>`;
                }
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

// ExitPlanMode Tool Handler - displays plan and handles mode transition
class ExitPlanModeToolHandler {
    renderParameters(toolCall, escapeHtmlFn) {
        const plan = toolCall.input.plan || '';

        let html = '<div class="tool-exitplan-params">';

        // Header with plan icon
        html += '<div class="exitplan-header">';
        html += '<span class="exitplan-icon">📋</span>';
        html += '<div class="exitplan-header-content">';
        html += '<div><strong>Plan Submitted for Approval:</strong></div>';
        html += '</div>';
        html += '</div>';

        // Plan section
        if (plan) {
            html += '<div class="exitplan-plan-section">';
            html += '<div class="exitplan-plan-content">';
            html += escapeHtmlFn(plan);
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

        if (toolCall.result.error) {
            return `
                <div class="tool-result ${resultClass}">
                    <strong>Plan Rejected:</strong>
                    <div class="exitplan-result-content">${escapeHtmlFn(content)}</div>
                </div>
            `;
        }

        // Successful plan approval
        return `
            <div class="tool-result ${resultClass}">
                <div class="exitplan-approved">
                    <strong>✅ Plan Approved</strong>
                    <div class="exitplan-mode-change">Permission mode changed to: <strong>default</strong></div>
                </div>
            </div>
        `;
    }

    getCollapsedSummary(toolCall, escapeHtmlFn) {
        // Status-based icons
        const statusIcon = {
            'pending': '⏳',
            'permission_required': '❓',
            'executing': '📋',
            'completed': toolCall.result?.error ? '❌' : '✅',
            'error': '❌'
        }[toolCall.status] || '📋';

        let statusText = '';
        if (toolCall.status === 'completed' && !toolCall.result?.error) {
            statusText = 'Plan Approved - Mode: default';
        } else if (toolCall.result?.error) {
            statusText = 'Plan Rejected';
        } else {
            statusText = {
                'pending': 'Pending',
                'permission_required': 'Awaiting Approval',
                'executing': 'Submitting',
                'completed': 'Completed',
                'error': 'Error'
            }[toolCall.status] || 'Unknown';
        }

        return `${statusIcon} ExitPlanMode: ${statusText}`;
    }
}

// Project Manager for hierarchical organization
class ProjectManager {
    constructor(webui) {
        this.webui = webui;
        this.projects = new Map(); // project_id -> ProjectData
        this.orderedProjects = []; // Maintains project order from backend
    }

    async loadProjects() {
        try {
            const response = await fetch('/api/projects');
            const data = await response.json();

            this.projects.clear();
            this.orderedProjects = [];

            for (const project of data.projects) {
                this.projects.set(project.project_id, project);
                this.orderedProjects.push(project.project_id);
            }

            Logger.info('PROJECT', `Loaded ${this.projects.size} projects`);
            return data.projects;
        } catch (error) {
            Logger.error('PROJECT', 'Failed to load projects', error);
            throw error;
        }
    }

    async createProject(name, workingDirectory) {
        try {
            const response = await fetch('/api/projects', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    name: name,
                    working_directory: workingDirectory
                })
            });

            if (!response.ok) {
                const error = await response.json();
                throw new Error(error.detail || 'Failed to create project');
            }

            const data = await response.json();
            const project = data.project;

            this.projects.set(project.project_id, project);
            this.orderedProjects.unshift(project.project_id); // Add to top

            Logger.info('PROJECT', `Created project ${project.project_id}`, project);
            return project;
        } catch (error) {
            Logger.error('PROJECT', 'Failed to create project', error);
            throw error;
        }
    }

    async getProjectWithSessions(projectId) {
        try {
            const response = await fetch(`/api/projects/${projectId}`);
            if (!response.ok) {
                throw new Error('Project not found');
            }

            const data = await response.json();
            return data;
        } catch (error) {
            Logger.error('PROJECT', `Failed to get project ${projectId}`, error);
            throw error;
        }
    }

    async updateProject(projectId, updates) {
        try {
            const response = await fetch(`/api/projects/${projectId}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(updates)
            });

            if (!response.ok) {
                throw new Error('Failed to update project');
            }

            // Update local cache
            const project = this.projects.get(projectId);
            if (project) {
                Object.assign(project, updates);
            }

            Logger.info('PROJECT', `Updated project ${projectId}`, updates);
            return true;
        } catch (error) {
            Logger.error('PROJECT', `Failed to update project ${projectId}`, error);
            throw error;
        }
    }

    async deleteProject(projectId) {
        try {
            const response = await fetch(`/api/projects/${projectId}`, {
                method: 'DELETE'
            });

            if (!response.ok) {
                throw new Error('Failed to delete project');
            }

            this.projects.delete(projectId);
            this.orderedProjects = this.orderedProjects.filter(id => id !== projectId);

            Logger.info('PROJECT', `Deleted project ${projectId}`);
            return true;
        } catch (error) {
            Logger.error('PROJECT', `Failed to delete project ${projectId}`, error);
            throw error;
        }
    }

    async toggleExpansion(projectId) {
        try {
            const response = await fetch(`/api/projects/${projectId}/toggle-expansion`, {
                method: 'PUT'
            });

            if (!response.ok) {
                throw new Error('Failed to toggle expansion');
            }

            const data = await response.json();

            // Update local cache
            const project = this.projects.get(projectId);
            if (project) {
                project.is_expanded = data.is_expanded;
            }

            Logger.info('PROJECT', `Toggled expansion for project ${projectId}`, data.is_expanded);
            return data.is_expanded;
        } catch (error) {
            Logger.error('PROJECT', `Failed to toggle expansion for ${projectId}`, error);
            throw error;
        }
    }

    async reorderProjects(projectIds) {
        try {
            const response = await fetch('/api/projects/reorder', {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ project_ids: projectIds })
            });

            if (!response.ok) {
                throw new Error('Failed to reorder projects');
            }

            this.orderedProjects = projectIds;
            Logger.info('PROJECT', 'Reordered projects', projectIds);
            return true;
        } catch (error) {
            Logger.error('PROJECT', 'Failed to reorder projects', error);
            throw error;
        }
    }

    async reorderSessionsInProject(projectId, sessionIds) {
        try {
            const response = await fetch(`/api/projects/${projectId}/sessions/reorder`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ session_ids: sessionIds })
            });

            if (!response.ok) {
                throw new Error('Failed to reorder sessions');
            }

            Logger.info('PROJECT', `Reordered sessions in project ${projectId}`, sessionIds);
            return true;
        } catch (error) {
            Logger.error('PROJECT', `Failed to reorder sessions in ${projectId}`, error);
            throw error;
        }
    }

    formatPath(absolutePath) {
        if (!absolutePath) return '';

        // Split path by forward or backward slashes
        const parts = absolutePath.split(/[/\\]/).filter(p => p);

        if (parts.length === 0) return '/';
        if (parts.length === 1) return `/${parts[0]}`;
        if (parts.length === 2) return `/${parts.join('/')}`;

        // 3+ folders: show ellipsis + last 2
        return `.../${parts.slice(-2).join('/')}`;
    }

    getProject(projectId) {
        return this.projects.get(projectId);
    }

    getAllProjects() {
        return this.orderedProjects.map(id => this.projects.get(id)).filter(p => p);
    }
}

class ClaudeWebUI {
    constructor() {
        this.currentSessionId = null;
        this.sessions = new Map();
        this.orderedSessions = []; // Maintains session order from backend

        // Project management
        this.projectManager = new ProjectManager(this);
        this.currentProjectId = null;

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

        // Permission mode tracking
        this.currentPermissionMode = 'default'; // default, acceptEdits, plan, etc.

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
        this.toolHandlerRegistry.registerHandler('ExitPlanMode', new ExitPlanModeToolHandler());

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

    async init() {
        this.setupEventListeners();
        this.connectUIWebSocket();
        this.updateConnectionStatus('disconnected');

        // Load projects and sessions on startup
        await this.loadSessions();
    }

    setupEventListeners() {
        // Project controls
        document.getElementById('create-project-btn').addEventListener('click', () => this.showCreateProjectModal());
        document.getElementById('refresh-sessions-btn').addEventListener('click', () => this.refreshSessions());

        // Project modal controls (Bootstrap modals handle close/cancel via data-bs-dismiss)
        document.getElementById('create-project-form').addEventListener('submit', (e) => this.handleCreateProject(e));
        document.getElementById('browse-project-directory').addEventListener('click', () => this.browseProjectDirectory());

        // Session modal controls (Bootstrap modals handle close/cancel via data-bs-dismiss)
        document.getElementById('create-session-form').addEventListener('submit', (e) => this.handleCreateSession(e));

        // Browse directory button
        document.getElementById('browse-directory').addEventListener('click', () => this.browseDirectory());

        // Session actions
        document.getElementById('delete-session-btn').addEventListener('click', () => this.showDeleteSessionModal());
        document.getElementById('exit-session-btn').addEventListener('click', () => this.exitSession());

        // Delete modal controls (Bootstrap modals handle close/cancel via data-bs-dismiss)
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

        // Permission mode cycling - click on icon/text area to cycle
        document.getElementById('permission-mode-clickable').addEventListener('click', () => this.cyclePermissionMode());

        // Messages area scroll detection
        document.getElementById('messages-area').addEventListener('scroll', (e) => this.handleScroll(e));

        // Bootstrap modals handle backdrop clicks automatically, no custom listeners needed

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
            Logger.error('API', 'API request failed', error);
            this.showError(`API request failed: ${error.message}`);
            throw error;
        }
    }

    // Session Management
    async loadSessions() {
        try {
            this.showLoading(true);

            // Load projects first
            await this.projectManager.loadProjects();

            // Load all sessions into cache
            const data = await this.apiRequest('/api/sessions');
            this.sessions.clear();
            this.orderedSessions = [];

            data.sessions.forEach(session => {
                this.sessions.set(session.session_id, session);
                this.orderedSessions.push(session);
            });

            await this.renderSessions();
        } catch (error) {
            Logger.error('SESSION', 'Failed to load sessions', error);
        } finally {
            this.showLoading(false);
        }
    }

    async createSession(formData) {
        try {
            this.showLoading(true);

            const tools = formData.tools ? formData.tools.split(',').map(t => t.trim()).filter(t => t) : [];

            const payload = {
                project_id: this.currentProjectId, // Use current project ID
                permission_mode: formData.permission_mode,
                system_prompt: formData.system_prompt || null,
                tools: tools,
                model: formData.model || null,
                name: formData.name || null
            };

            const data = await this.apiRequest('/api/sessions', {
                method: 'POST',
                body: JSON.stringify(payload)
            });

            // GRANULAR UPDATE: Fetch the new session data and add it to DOM directly
            // This is faster than waiting for WebSocket and avoids race conditions
            const sessionData = await this.apiRequest(`/api/sessions/${data.session_id}`);

            // Add session to project in DOM
            await this.addSessionToProjectDOM(this.currentProjectId, sessionData.session);

            // Select the new session
            await this.selectSession(data.session_id);

            return data.session_id;
        } catch (error) {
            Logger.error('SESSION', 'Failed to create session', error);
            throw error;
        } finally {
            this.showLoading(false);
        }
    }

    exitSession() {
        if (!this.currentSessionId) return;

        Logger.info('SESSION', `Exiting session ${this.currentSessionId}`);

        // Clean disconnect from WebSocket
        this.disconnectSessionWebSocket();

        // Clear current session
        this.currentSessionId = null;

        // Reset UI to no session selected state
        document.getElementById('no-session-selected').classList.remove('d-none');
        document.getElementById('chat-container').classList.add('d-none');

        // Remove active state from all session items
        document.querySelectorAll('.list-group-item').forEach(item => {
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
            Logger.error('MESSAGE', 'Failed to send message', error);
        }
    }

    async sendInterrupt() {
        if (!this.currentSessionId || !this.isProcessing) {
            Logger.debug('INTERRUPT', 'sendInterrupt() called but conditions not met', {
                currentSessionId: this.currentSessionId,
                isProcessing: this.isProcessing
            });
            return;
        }

        try {
            Logger.info('INTERRUPT', 'Sending interrupt request for session', this.currentSessionId);
            Logger.debug('INTERRUPT', 'WebSocket state check', {
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
                Logger.debug('INTERRUPT', 'Sending interrupt message via WebSocket', interruptMessage);
                this.sessionWebsocket.send(JSON.stringify(interruptMessage));
                Logger.debug('INTERRUPT', 'Interrupt message sent successfully');
            } else {
                Logger.warn('INTERRUPT', 'WebSocket not connected, cannot send interrupt');
                Logger.debug('INTERRUPT', 'WebSocket connection details', {
                    sessionWebsocket: !!this.sessionWebsocket,
                    readyState: this.sessionWebsocket?.readyState,
                    expectedState: WebSocket.OPEN
                });
                // Fallback: we could add a REST API endpoint for interrupt if needed
                this.hideProcessingIndicator();
            }

        } catch (error) {
            Logger.error('INTERRUPT', 'Failed to send interrupt', error);
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
            progressElement.classList.remove('d-none');
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
            progressElement.classList.add('d-none');
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
        Logger.debug('MESSAGE', `Processing message from ${source}`, message);

        // Handle progress indicator for init messages (real-time only)
        if (source === 'websocket' && message.type === 'system' && message.subtype === 'init') {
            if (!this.isProcessing) {
                this.showProcessingIndicator();
            }

            // Extract and store permission mode from init message
            this.extractPermissionMode(message);
        }

        // Check if this is a tool-related message and handle it
        const toolHandled = this.handleToolRelatedMessage(message, source);

        // Check if this is a thinking block message and handle it
        const thinkingHandled = this.handleThinkingBlockMessage(message);


        // If it's a tool-related or thinking block message, don't show it in the regular message flow
        if (toolHandled || thinkingHandled) {
            return { handled: true, displayed: false };
        }

        // Use the unified filtering logic to determine if message should be displayed
        if (this.shouldDisplayMessage(message)) {
            Logger.debug('MESSAGE', `Adding ${source} message to UI`, message.type);
            this.addMessageToUI(message, source !== 'historical');
            return { handled: true, displayed: true };
        }

        return { handled: true, displayed: false };
    }

    handleIncomingMessage(message) {
        // Use unified processing for real-time messages
        this.processMessage(message, 'websocket');
    }

    /**
     * Extract permission mode from system init messages
     */
    extractPermissionMode(message) {
        try {
            // Try to get permission mode from metadata or raw SDK message
            let permissionMode = null;

            // Check metadata first
            if (message.metadata && message.metadata.raw_sdk_message) {
                const rawMsg = message.metadata.raw_sdk_message;

                // Parse if it's a string
                if (typeof rawMsg === 'string') {
                    try {
                        const parsed = JSON.parse(rawMsg);
                        if (parsed.data && parsed.data.permissionMode) {
                            permissionMode = parsed.data.permissionMode;
                        }
                    } catch (e) {
                        // Try to extract from string representation
                        const match = rawMsg.match(/'permissionMode':\s*'([^']+)'/);
                        if (match) {
                            permissionMode = match[1];
                        }
                    }
                } else if (typeof rawMsg === 'object' && rawMsg.data && rawMsg.data.permissionMode) {
                    permissionMode = rawMsg.data.permissionMode;
                }
            }

            if (permissionMode && permissionMode !== this.currentPermissionMode) {
                Logger.info('PERMISSION', 'Permission mode changed', {from: this.currentPermissionMode, to: permissionMode});
                this.currentPermissionMode = permissionMode;
                this.updatePermissionModeUI(permissionMode);
            }
        } catch (error) {
            Logger.error('PERMISSION', 'Error extracting permission mode', error);
        }
    }

    /**
     * Update UI to reflect current permission mode
     */
    updatePermissionModeUI(mode) {
        Logger.debug('PERMISSION', 'Updating UI - Current permission mode', mode);

        // Update permission mode text in the status bar
        const permissionModeText = document.getElementById('permission-mode-text');
        const permissionModeIcon = document.getElementById('permission-mode-icon');
        const permissionModeClickable = document.getElementById('permission-mode-clickable');

        if (permissionModeText) {
            permissionModeText.textContent = `Mode: ${mode}`;
        }

        // Update icon and title based on mode
        if (permissionModeIcon && permissionModeClickable) {
            const modeConfig = {
                'default': {
                    icon: '🔒',
                    title: 'Click to cycle modes • Requires approval for most tools'
                },
                'acceptEdits': {
                    icon: '✅',
                    title: 'Click to cycle modes • Auto-approves Read, Edit, Write, MultiEdit'
                },
                'plan': {
                    icon: '📋',
                    title: 'Click to cycle modes • Planning mode - must approve plan to proceed'
                }
            };

            const config = modeConfig[mode] || modeConfig['default'];
            permissionModeIcon.textContent = config.icon;
            permissionModeClickable.title = config.title;
        }
    }

    handleToolRelatedMessage(message, source = 'websocket') {
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

                        // Check if this is ExitPlanMode completing successfully (real-time only, backend handles it)
                        if (source === 'websocket' && toolCall.name === 'ExitPlanMode' && toolCall.status === 'completed' && !toolCall.result?.error) {
                            Logger.info('PERMISSION', 'ExitPlanMode completed - updating permission mode to default');
                            this.setPermissionMode('default');
                        }
                    }
                });

                return hasToolResult;
            }

            return false;
        } catch (error) {
            Logger.error('TOOL_MANAGER', 'Error handling tool-related message', {error, message});
            return false;
        }
    }

    handleThinkingBlockMessage(message) {
        try {
            // Check if this is an assistant message with thinking blocks
            if (message.type === 'assistant' && message.metadata && message.metadata.thinking_blocks && Array.isArray(message.metadata.thinking_blocks)) {
                const thinkingBlocks = message.metadata.thinking_blocks;

                if (thinkingBlocks.length > 0) {
                    Logger.debug('MESSAGE', 'Processing thinking blocks', thinkingBlocks);

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
            Logger.error('MESSAGE', 'Error handling thinking block message', {error, message});
            return false;
        }
    }

    renderToolCall(toolCall) {
        Logger.debug('UI', 'Rendering tool call', toolCall);

        const messagesArea = document.getElementById('messages-area');

        // Wrap tool call in two-column layout
        const wrapper = document.createElement('div');
        wrapper.className = 'message-row row py-1 tool-call';
        wrapper.id = `tool-call-wrapper-${toolCall.id}`;

        const timestamp = new Date().toLocaleTimeString();

        wrapper.innerHTML = `
            <div class="col-auto message-speaker text-end pe-3" title="${timestamp}">
                agent
            </div>
            <div class="col message-content-column" id="tool-call-content-${toolCall.id}">
            </div>
        `;

        // Add to DOM
        messagesArea.appendChild(wrapper);

        // Insert the actual tool call element into the content column
        const contentColumn = document.getElementById(`tool-call-content-${toolCall.id}`);
        const toolCallElement = this.createToolCallElement(toolCall);
        contentColumn.appendChild(toolCallElement);

        this.smartScrollToBottom();
    }

    updateToolCall(toolCall, scroll = true) {
        Logger.debug('UI', 'Updating tool call', toolCall);

        const existingContentColumn = document.getElementById(`tool-call-content-${toolCall.id}`);
        if (existingContentColumn) {
            // Replace the tool call element inside the content column
            const updatedElement = this.createToolCallElement(toolCall);
            existingContentColumn.innerHTML = '';
            existingContentColumn.appendChild(updatedElement);

            if (scroll) {
                this.smartScrollToBottom();
            }
        } else {
            this.renderToolCall(toolCall);
        }
    }

    createToolCallElement(toolCall) {
        const element = document.createElement('div');
        element.className = 'accordion';
        element.id = `tool-call-${toolCall.id}`;

        // Use unified accordion template
        element.innerHTML = this.createToolCallHTML(toolCall);

        // Add event delegation for permission button clicks only
        this.setupToolCallEventListeners(element, toolCall);

        return element;
    }

    createToolCallHTML(toolCall) {
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

        // Generate summary for accordion button
        let summary;
        if (handler && handler.getCollapsedSummary) {
            const customSummary = handler.getCollapsedSummary(toolCall);
            summary = customSummary !== null ? customSummary : this.toolCallManager.generateCollapsedSummary(toolCall);
        } else {
            summary = this.toolCallManager.generateCollapsedSummary(toolCall);
        }

        // Determine if accordion should be expanded
        const collapseClass = toolCall.isExpanded ? 'accordion-collapse collapse show' : 'accordion-collapse collapse';
        const buttonClass = toolCall.isExpanded ? 'accordion-button' : 'accordion-button collapsed';

        // Build accordion body content
        let bodyContent = `
            <div class="tool-call-details">
                ${handler.renderParameters(toolCall, this.escapeHtml.bind(this))}
            </div>
        `;

        // Add permission prompt if needed
        if (toolCall.status === 'permission_required') {
            bodyContent += `
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
            bodyContent += handler.renderResult(toolCall, this.escapeHtml.bind(this));
        }

        // Add explanation if available
        if (toolCall.explanation) {
            bodyContent += `
                <div class="tool-explanation">
                    <strong>Explanation:</strong>
                    <div class="tool-explanation-content">${this.escapeHtml(toolCall.explanation)}</div>
                </div>
            `;
        }

        // Return Bootstrap accordion structure
        return `
            <div class="accordion-item ${statusClass}">
                <h2 class="accordion-header">
                    <button class="${buttonClass}" type="button" data-bs-toggle="collapse" data-bs-target="#collapse-${toolCall.id}" aria-expanded="${toolCall.isExpanded}" aria-controls="collapse-${toolCall.id}">
                        ${this.escapeHtml(summary)}
                    </button>
                </h2>
                <div id="collapse-${toolCall.id}" class="${collapseClass}">
                    <div class="accordion-body">
                        ${bodyContent}
                    </div>
                </div>
            </div>
        `;
    }

    setupToolCallEventListeners(element, toolCall) {
        // Listen to Bootstrap collapse events to keep state in sync
        const collapseElement = element.querySelector(`#collapse-${toolCall.id}`);
        if (collapseElement) {
            collapseElement.addEventListener('shown.bs.collapse', () => {
                // Update internal state when accordion expands
                this.toolCallManager.setToolExpansion(toolCall.id, true);
            });

            collapseElement.addEventListener('hidden.bs.collapse', () => {
                // Update internal state when accordion collapses
                this.toolCallManager.setToolExpansion(toolCall.id, false);
            });
        }

        // Handle permission button clicks
        const approveBtn = element.querySelector('.permission-approve');
        const denyBtn = element.querySelector('.permission-deny');

        if (approveBtn) {
            approveBtn.addEventListener('click', () => {
                // Disable both buttons immediately to prevent duplicate clicks
                if (approveBtn.disabled || denyBtn.disabled) return;

                approveBtn.disabled = true;
                denyBtn.disabled = true;

                // Update button text to show submission
                approveBtn.textContent = '⏳ Submitting...';

                const requestId = approveBtn.getAttribute('data-request-id');
                this.handlePermissionDecision(requestId, 'allow', approveBtn, denyBtn);
            });
        }

        if (denyBtn) {
            denyBtn.addEventListener('click', () => {
                // Disable both buttons immediately to prevent duplicate clicks
                if (approveBtn.disabled || denyBtn.disabled) return;

                approveBtn.disabled = true;
                denyBtn.disabled = true;

                // Update button text to show submission
                denyBtn.textContent = '⏳ Submitting...';

                const requestId = denyBtn.getAttribute('data-request-id');
                this.handlePermissionDecision(requestId, 'deny', approveBtn, denyBtn);
            });
        }
    }

    toggleToolCallExpansion(toolUseId) {
        // Bootstrap accordion handles the UI toggle via data-bs-toggle
        // Just update the internal state
        this.toolCallManager.toggleToolExpansion(toolUseId);
        // No need to call updateToolCall - Bootstrap handles the DOM changes
    }

    handlePermissionDecision(requestId, decision, approveBtn, denyBtn) {
        Logger.info('PERMISSION', 'Permission decision', {requestId, decision});

        // Send permission response to backend
        if (this.sessionWebsocket && this.sessionWebsocket.readyState === WebSocket.OPEN) {
            const response = {
                type: 'permission_response',
                request_id: requestId,
                decision: decision,
                timestamp: new Date().toISOString()
            };

            this.sessionWebsocket.send(JSON.stringify(response));

            // Update button to show submitted state
            if (decision === 'allow' && approveBtn) {
                approveBtn.textContent = '✅ Approved';
                approveBtn.classList.add('submitted');
            } else if (decision === 'deny' && denyBtn) {
                denyBtn.textContent = '❌ Denied';
                denyBtn.classList.add('submitted');
            }
        } else {
            // Re-enable buttons if WebSocket is not available
            Logger.error('PERMISSION', 'Cannot send permission response: WebSocket not connected');
            if (approveBtn) {
                approveBtn.disabled = false;
                approveBtn.textContent = '✅ Approve';
            }
            if (denyBtn) {
                denyBtn.disabled = false;
                denyBtn.textContent = '❌ Deny';
            }
        }
    }

    renderThinkingBlock(thinkingBlock) {
        Logger.debug('UI', 'Rendering thinking block', thinkingBlock);

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
            Logger.debug('SESSION', 'Skipping loadSessionInfo - deletion in progress', this.currentSessionId);
            return;
        }

        // Skip if session no longer exists in our local map
        if (!this.sessions.has(this.currentSessionId)) {
            Logger.debug('SESSION', 'Skipping loadSessionInfo - not in local sessions map', this.currentSessionId);
            return;
        }

        try {
            const data = await this.apiRequest(`/api/sessions/${this.currentSessionId}`);
            this.updateSessionInfo(data);
        } catch (error) {
            // If it's a 404, the session was likely deleted - handle gracefully
            if (error.message.includes('404')) {
                Logger.info('SESSION', 'Session not found (404) - likely deleted, clearing from UI', this.currentSessionId);
                this.handleSessionDeleted(this.currentSessionId);
            } else {
                Logger.error('SESSION', 'Failed to load session info', error);
            }
        }
    }

    async setPermissionMode(mode) {
        if (!this.currentSessionId) {
            Logger.error('PERMISSION', 'Cannot set permission mode: no active session');
            return;
        }

        try {
            Logger.info('PERMISSION', 'Setting permission mode', mode);

            // Update local state immediately for responsive UI
            this.currentPermissionMode = mode;
            this.updatePermissionModeUI(mode);

            // Persist to backend
            const response = await this.apiRequest(`/api/sessions/${this.currentSessionId}/permission-mode`, {
                method: 'POST',
                body: JSON.stringify({ mode: mode })
            });

            if (response.success) {
                Logger.info('PERMISSION', 'Permission mode successfully set', response.mode);
            } else {
                Logger.error('PERMISSION', 'Failed to set permission mode on backend');
            }
        } catch (error) {
            Logger.error('PERMISSION', 'Error setting permission mode', error);
            // Optionally revert UI if backend call fails
            // this.extractPermissionMode(lastKnownState);
        }
    }

    async loadMessages() {
        if (!this.currentSessionId) return;

        try {
            Logger.debug('MESSAGE', 'Loading all messages with pagination');
            const allMessages = [];
            let offset = 0;
            const pageSize = 50;
            let hasMore = true;

            // Load all messages using pagination
            while (hasMore) {
                Logger.debug('MESSAGE', 'Loading messages page', {offset, limit: pageSize});
                const response = await this.apiRequest(
                    `/api/sessions/${this.currentSessionId}/messages?limit=${pageSize}&offset=${offset}`
                );

                // Add messages from this page
                allMessages.push(...response.messages);

                // Check if there are more pages
                hasMore = response.has_more;
                offset += pageSize;

                Logger.debug('MESSAGE', 'Loaded messages page', {loaded: response.messages.length, total: allMessages.length, hasMore});
            }

            Logger.debug('MESSAGE', 'Finished loading all messages', {total: allMessages.length});
            this.renderMessages(allMessages);
        } catch (error) {
            Logger.error('MESSAGE', 'Failed to load messages', error);
        }
    }

    // UI WebSocket Management (for session state updates)
    connectUIWebSocket() {
        if (this.uiWebsocket && this.uiWebsocket.readyState === WebSocket.OPEN) {
            Logger.debug('WS_UI', 'UI WebSocket already connected');
            return;
        }

        Logger.info('WS_UI', 'Connecting to UI WebSocket');
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${protocol}//${window.location.host}/ws/ui`;

        this.uiWebsocket = new WebSocket(wsUrl);

        this.uiWebsocket.onopen = () => {
            Logger.info('WS_UI', 'UI WebSocket connected successfully');
            this.uiConnectionRetryCount = 0;
        };

        this.uiWebsocket.onmessage = (event) => {
            try {
                const data = JSON.parse(event.data);
                this.handleUIWebSocketMessage(data);
            } catch (error) {
                Logger.error('WS_UI', 'Error parsing UI WebSocket message', error);
            }
        };

        this.uiWebsocket.onclose = (event) => {
            Logger.info('WS_UI', 'UI WebSocket disconnected', {code: event.code, reason: event.reason});
            this.uiWebsocket = null;

            // Auto-reconnect UI WebSocket (it should always stay connected)
            if (this.uiConnectionRetryCount < this.maxUIRetries) {
                this.uiConnectionRetryCount++;
                const delay = Math.min(1000 * Math.pow(2, this.uiConnectionRetryCount), 30000);
                Logger.info('WS_UI', 'Reconnecting UI WebSocket', {delay, attempt: this.uiConnectionRetryCount, max: this.maxUIRetries});

                setTimeout(() => {
                    this.connectUIWebSocket();
                }, delay);
            } else {
                Logger.warn('WS_UI', 'Max UI WebSocket reconnection attempts reached');
            }
        };

        this.uiWebsocket.onerror = (error) => {
            Logger.error('WS_UI', 'UI WebSocket error', error);
        };
    }

    handleUIWebSocketMessage(data) {
        Logger.debug('WS_UI', 'UI WebSocket message received', data.type);

        switch (data.type) {
            case 'sessions_list':
                // Initial sessions list on connection
                this.updateSessionsList(data.data.sessions);
                break;
            case 'state_change':
                // Real-time session state change
                this.handleStateChange(data.data);
                break;
            case 'project_updated':
                // Project was updated (name, expansion state, etc.)
                this.handleProjectUpdated(data.data);
                break;
            case 'project_deleted':
                // Project was deleted
                this.handleProjectDeleted(data.data);
                break;
            case 'ping':
                // Respond to server ping
                if (this.uiWebsocket && this.uiWebsocket.readyState === WebSocket.OPEN) {
                    this.uiWebsocket.send(JSON.stringify({type: 'pong', timestamp: new Date().toISOString()}));
                }
                break;
            case 'pong':
                // Server responded to our ping
                Logger.debug('WS_UI', 'UI WebSocket pong received');
                break;
            default:
                Logger.warn('WS_UI', 'Unknown UI WebSocket message type', data.type);
        }
    }

    updateSessionsList(sessions) {
        Logger.debug('SESSION', 'Updating sessions list', {count: sessions.length});
        this.sessions.clear();
        // Store sessions in order received from backend (which is sorted by order field)
        this.orderedSessions = [];
        sessions.forEach(session => {
            this.sessions.set(session.session_id, session);
            this.orderedSessions.push(session);
        });
        this.renderSessions();
    }

    handleProjectUpdated(data) {
        Logger.debug('PROJECT', 'Project updated via WebSocket', data);
        const project = data.project;

        // Update local project cache
        const existingProject = this.projectManager.projects.get(project.project_id);

        if (existingProject) {
            // Check if this is a new session being added
            const hadSessions = existingProject.session_ids ? existingProject.session_ids.length : 0;
            const hasSessions = project.session_ids ? project.session_ids.length : 0;
            const isSessionAdded = hasSessions > hadSessions;

            // Update cache
            this.projectManager.projects.set(project.project_id, project);

            // Update ordered list if needed
            if (!this.projectManager.orderedProjects.includes(project.project_id)) {
                this.projectManager.orderedProjects.push(project.project_id);
            }

            // GRANULAR UPDATE: Only update what changed
            if (isSessionAdded) {
                // A session was added - we'll get the session data via state_change
                // Just update the status line here
                this.updateProjectStatusLine(project.project_id);
            } else {
                // Other metadata changed (name, expansion, etc.)
                this.updateProjectInDOM(project.project_id, 'metadata-changed');
            }
        } else {
            // New project - need full render
            this.projectManager.projects.set(project.project_id, project);
            if (!this.projectManager.orderedProjects.includes(project.project_id)) {
                this.projectManager.orderedProjects.push(project.project_id);
            }
            this.renderSessions();
        }
    }

    handleProjectDeleted(data) {
        Logger.debug('PROJECT', 'Project deleted via WebSocket', data);
        const projectId = data.project_id;

        // Remove from local cache
        this.projectManager.projects.delete(projectId);
        this.projectManager.orderedProjects = this.projectManager.orderedProjects.filter(id => id !== projectId);

        // If any sessions from this project are loaded, clear them
        // Re-render to remove from UI
        this.renderSessions();
    }

    async refreshSessions() {
        Logger.debug('SESSION', 'Refreshing sessions via API fallback');
        // Fallback to API call if UI WebSocket is not available
        await this.loadSessions();
    }

    // Session WebSocket Management (for message streaming)
    connectSessionWebSocket() {
        if (!this.currentSessionId) return;

        // If there's already a connection in CONNECTING or OPEN state, don't create another
        if (this.sessionWebsocket && (this.sessionWebsocket.readyState === WebSocket.CONNECTING || this.sessionWebsocket.readyState === WebSocket.OPEN)) {
            Logger.debug('WS_SESSION', 'WebSocket already exists in state:', this.sessionWebsocket.readyState);
            return;
        }

        // Reset intentional disconnect flag for new connections
        this.intentionalSessionDisconnect = false;

        Logger.info('WS_SESSION', 'Connecting session WebSocket', this.currentSessionId);
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${protocol}//${window.location.host}/ws/session/${this.currentSessionId}`;

        try {
            this.sessionWebsocket = new WebSocket(wsUrl);

            this.sessionWebsocket.onopen = () => {
                Logger.info('WS_SESSION', 'WebSocket connected');
                this.updateConnectionStatus('connected');
                this.sessionConnectionRetryCount = 0;
            };

            this.sessionWebsocket.onmessage = (event) => {
                try {
                    const data = JSON.parse(event.data);
                    this.handleWebSocketMessage(data);
                } catch (error) {
                    Logger.error('WS_SESSION', 'Failed to parse WebSocket message', error);
                }
            };

            this.sessionWebsocket.onclose = (event) => {
                Logger.info('WS_SESSION', 'WebSocket disconnected', {code: event.code, reason: event.reason});
                this.updateConnectionStatus('disconnected');

                // Don't retry if this was an intentional disconnect
                if (this.intentionalSessionDisconnect) {
                    Logger.debug('WS_SESSION', 'WebSocket closed intentionally, not retrying');
                    return;
                }

                // Don't retry on specific error codes (session invalid/inactive)
                if (event.code === 4404 || event.code === 4003 || event.code === 4500) {
                    Logger.info('WS_SESSION', 'WebSocket closed with error code, not retrying', event.code);
                    return;
                }

                this.scheduleReconnect();
            };

            this.sessionWebsocket.onerror = (error) => {
                Logger.error('WS_SESSION', 'WebSocket error', error);
                this.updateConnectionStatus('disconnected');
            };

        } catch (error) {
            Logger.error('WS_SESSION', 'Failed to create WebSocket', error);
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
            Logger.debug('WS_SESSION', 'Reconnect cancelled due to intentional disconnect');
            return;
        }

        if (this.sessionConnectionRetryCount < this.maxSessionRetries) {
            this.sessionConnectionRetryCount++;
            const delay = Math.min(1000 * Math.pow(2, this.sessionConnectionRetryCount), 30000);

            Logger.info('WS_SESSION', 'Scheduling WebSocket reconnect', {delay, attempt: this.sessionConnectionRetryCount});
            setTimeout(() => {
                if (this.currentSessionId && !this.intentionalSessionDisconnect) {
                    this.connectSessionWebSocket();
                }
            }, delay);
        } else {
            Logger.warn('WS_SESSION', 'Max reconnection attempts reached');
        }
    }

    handleWebSocketMessage(data) {
        Logger.debug('WS_SESSION', 'WebSocket message received', data);

        switch (data.type) {
            case 'message':
                this.handleIncomingMessage(data.data);
                break;
            case 'state_change':
                this.handleStateChange(data.data);
                break;
            case 'connection_established':
                Logger.info('WS_SESSION', 'WebSocket connection confirmed for session', data.session_id);
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
                Logger.warn('WS_SESSION', 'Unknown WebSocket message type', data.type);
        }
    }

    handleInterruptResponse(data) {
        Logger.info('INTERRUPT', 'Interrupt response received', data);

        if (data.success) {
            Logger.info('INTERRUPT', 'Interrupt successful', data.message);
            // Interrupt was successful, hide processing indicators
            this.hideProcessingIndicator();
        } else {
            Logger.warn('INTERRUPT', 'Interrupt failed', data.message);
            // Interrupt failed, return to processing state (not stopping state)
            this.showProcessingIndicator();
        }
    }

    // UI Updates
    async selectSession(sessionId) {
        // If already connected to this session, don't reconnect
        if (this.currentSessionId === sessionId && this.sessionWebsocket && this.sessionWebsocket.readyState === WebSocket.OPEN) {
            Logger.debug('SESSION', 'Already connected to session', sessionId);
            return;
        }

        // Show loading screen immediately when switching sessions
        this.showLoading(true);

        try {
            // Clean disconnect from previous session
            if (this.currentSessionId && this.currentSessionId !== sessionId) {
                Logger.info('SESSION', 'Switching sessions', {from: this.currentSessionId, to: sessionId});
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
            document.getElementById('no-session-selected').classList.add('d-none');
            document.getElementById('chat-container').classList.remove('d-none');

            // Load session info first to check state
            await this.loadSessionInfo();

            // Check if session needs to be started or is ready for use
            const session = this.sessions.get(sessionId);
            if (session) {
                if (session.state === 'error') {
                    // Session is in error state, skip WebSocket initialization
                    Logger.info('SESSION', 'Session is in error state, skipping WebSocket connection', sessionId);
                    // Just load messages without attempting to connect
                } else if (session.state === 'active' || session.state === 'running') {
                    // Session is already active, just connect WebSocket
                    Logger.info('SESSION', 'Session is already active, connecting WebSocket', sessionId);
                    this.connectSessionWebSocket();
                } else if (session.state === 'starting') {
                    // Session is starting, wait for it to become active
                    Logger.info('SESSION', 'Session is starting, waiting for it to become active', sessionId);
                    let attempts = 0;
                    const maxAttempts = 15;
                    const pollInterval = 1000;
                    while (attempts < maxAttempts) {
                        await new Promise(resolve => setTimeout(resolve, pollInterval));
                        await this.loadSessionInfo();
                        const updatedSession = this.sessions.get(sessionId);
                        if (updatedSession && updatedSession.state === 'error') {
                            Logger.info('SESSION', 'Session entered error state during startup, stopping wait', sessionId);
                            break;
                        } else if (updatedSession && (updatedSession.state === 'active' || updatedSession.state === 'running')) {
                            Logger.info('SESSION', 'Session is now active, connecting WebSocket', sessionId);
                            this.connectSessionWebSocket();
                            break;
                        }
                        attempts++;
                        Logger.debug('SESSION', 'Waiting for session to become active', {sessionId, attempt: attempts, max: maxAttempts});
                    }

                    if (attempts >= maxAttempts) {
                        Logger.warn('SESSION', 'Session did not become active', {sessionId, maxAttempts, seconds: maxAttempts * pollInterval / 1000});
                    }
                } else {
                    // Session needs to be started (both fresh sessions and existing sessions)
                    // The server-side logic will handle whether to create fresh or resume based on claude_code_session_id
                    Logger.info('SESSION', 'Starting session', {sessionId, currentState: session.state});
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
                            Logger.info('SESSION', 'Session entered error state during startup, stopping wait', sessionId);
                            break;
                        } else if (updatedSession && (updatedSession.state === 'active' || updatedSession.state === 'running')) {
                            Logger.info('SESSION', 'Session is now active, connecting WebSocket', sessionId);
                            this.connectSessionWebSocket();
                            break;
                        }
                        attempts++;
                        Logger.debug('SESSION', 'Waiting for session to become active', {sessionId, attempt: attempts, max: maxAttempts});
                    }

                    if (attempts >= maxAttempts) {
                        Logger.warn('SESSION', 'Session did not become active', {sessionId, maxAttempts, seconds: maxAttempts * pollInterval / 1000});
                    }
                }
            }

            // Load messages after session is ready
            this.loadMessages();

            // Load session info to get current processing state from backend
            this.loadSessionInfo();
        } catch (error) {
            Logger.error('SESSION', 'Error selecting session', error);
        } finally {
            // Hide loading screen when session switch is complete
            this.showLoading(false);
        }
    }

    async renderSessions() {
        const container = document.getElementById('sessions-container');
        container.innerHTML = '';

        const projects = this.projectManager.getAllProjects();

        if (projects.length === 0) {
            container.innerHTML = '<p class="text-muted">No projects available. Create a project to get started.</p>';
            return;
        }

        for (const project of projects) {
            const projectElement = await this.createProjectElement(project);
            container.appendChild(projectElement);
        }
    }

    async createProjectElement(project) {
        const projectElement = document.createElement('div');
        projectElement.className = 'card mb-2';
        projectElement.setAttribute('data-project-id', project.project_id);

        // Project header (card header)
        const projectHeader = document.createElement('div');
        projectHeader.className = 'card-header bg-white d-flex align-items-center gap-2 p-2 cursor-pointer';
        projectHeader.style.cursor = 'pointer';

        // Expansion arrow
        const expansionArrow = document.createElement('span');
        expansionArrow.className = 'text-muted';
        expansionArrow.textContent = project.is_expanded ? '▼' : '▶';

        // Project name and path
        const projectInfo = document.createElement('div');
        projectInfo.className = 'flex-grow-1';
        const formattedPath = this.projectManager.formatPath(project.working_directory);
        projectInfo.innerHTML = `
            <div class="fw-semibold">${this.escapeHtml(project.name)}</div>
            <small class="text-muted font-monospace" title="${this.escapeHtml(project.working_directory)}">${this.escapeHtml(formattedPath)}</small>
        `;

        // Add session button
        const addSessionBtn = document.createElement('button');
        addSessionBtn.className = 'btn btn-sm btn-outline-primary';
        addSessionBtn.innerHTML = '+';
        addSessionBtn.title = 'Add session to project';
        addSessionBtn.addEventListener('click', async (e) => {
            e.stopPropagation();
            await this.showCreateSessionModalForProject(project.project_id);
        });

        projectHeader.appendChild(expansionArrow);
        projectHeader.appendChild(projectInfo);
        projectHeader.appendChild(addSessionBtn);

        // Click header to toggle expansion
        projectHeader.addEventListener('click', async () => {
            await this.toggleProjectExpansion(project.project_id);
        });

        projectElement.appendChild(projectHeader);

        // Project status line (progress bar style)
        const statusLine = await this.createProjectStatusLine(project);
        projectElement.appendChild(statusLine);

        // Sessions container (collapsed or expanded) - use list group
        if (project.is_expanded && project.session_ids && project.session_ids.length > 0) {
            const sessionsContainer = document.createElement('div');
            sessionsContainer.className = 'list-group list-group-flush';

            // Use cached session data instead of fetching from API
            const sessions = project.session_ids
                .map(sid => this.sessions.get(sid))
                .filter(s => s); // Filter out any sessions that aren't in cache yet

            for (const session of sessions) {
                const sessionElement = this.createSessionElement(session, project.project_id);
                sessionsContainer.appendChild(sessionElement);
            }

            projectElement.appendChild(sessionsContainer);
        }

        return projectElement;
    }

    async createProjectStatusLine(project) {
        const statusLine = document.createElement('div');
        statusLine.className = 'progress' ;
        statusLine.style.height = '4px';
        statusLine.style.borderRadius = '0';

        if (!project.session_ids || project.session_ids.length === 0) {
            // Empty project - show single gray segment
            const emptySegment = document.createElement('div');
            emptySegment.className = 'progress-bar bg-secondary';
            emptySegment.style.width = '100%';
            statusLine.appendChild(emptySegment);
            return statusLine;
        }

        // Use cached session data instead of fetching from API
        const sessions = project.session_ids
            .map(sid => this.sessions.get(sid))
            .filter(s => s); // Filter out any sessions that aren't in cache yet

        const segmentWidth = `${100 / sessions.length}%`;

        for (const session of sessions) {
            const segment = document.createElement('div');
            segment.className = 'progress-bar';
            segment.style.width = segmentWidth;

            // Determine color based on session state
            const isProcessing = session.is_processing || false;
            const displayState = isProcessing ? 'processing' : session.state;
            const bgClass = this.getSessionStateBgClass(displayState);

            segment.classList.add(bgClass);

            // Add animation for active states
            if (displayState === 'starting' || displayState === 'processing') {
                segment.classList.add('progress-bar-striped', 'progress-bar-animated');
            }

            statusLine.appendChild(segment);
        }

        return statusLine;
    }

    getSessionStateBgClass(state) {
        // Map states to Bootstrap background classes
        const bgMap = {
            'created': 'bg-secondary',
            'CREATED': 'bg-secondary',
            'starting': 'bg-success',
            'Starting': 'bg-success',
            'running': 'bg-success',
            'active': 'bg-success',
            'processing': 'bg-primary',
            'paused': 'bg-secondary',
            'terminated': 'bg-secondary',
            'error': 'bg-danger',
            'failed': 'bg-danger'
        };
        return bgMap[state] || 'bg-secondary';
    }

    getSessionStateColor(state) {
        // Match the colors from session indicator dots for consistency
        const colorMap = {
            'created': '#6c757d',      // grey (matches status-dot-grey border)
            'CREATED': '#6c757d',
            'starting': '#28a745',     // green (matches status-dot-green border for starting/blinking)
            'Starting': '#28a745',
            'running': '#28a745',      // green (matches status-dot-green)
            'active': '#28a745',       // green (matches status-dot-green for active)
            'processing': '#6f42c1',   // purple (matches status-dot-purple border for processing/blinking)
            'paused': '#6c757d',       // grey (matches status-dot-grey)
            'terminated': '#6c757d',   // grey (matches status-dot-grey)
            'error': '#dc3545',        // red (matches status-dot-red border)
            'failed': '#dc3545'        // red (matches status-dot-red)
        };
        return colorMap[state] || '#6c757d'; // default grey
    }

    createSessionElement(session, projectId) {
        const sessionId = session.session_id;
        const sessionElement = document.createElement('div');
        sessionElement.className = 'list-group-item list-group-item-action';

        // Add active class if this is the currently selected session
        if (sessionId === this.currentSessionId) {
            sessionElement.classList.add('active');
        }

        sessionElement.setAttribute('data-session-id', sessionId);
        sessionElement.setAttribute('data-project-id', projectId);

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

        // Add drag-and-drop event listeners (project-aware)
        this.addDragDropListeners(sessionElement, sessionId, projectId);

        // Create status indicator - show processing state if is_processing is true
        const isProcessing = session.is_processing || false;
        const displayState = isProcessing ? 'processing' : session.state;
        const statusIndicator = this.createStatusIndicator(displayState, 'session', session.state);

        // Use session name if available, fallback to session ID
        const displayName = session.name || sessionId;

        sessionElement.innerHTML = `
            <div class="d-flex align-items-center gap-2">
                <div class="flex-grow-1" title="${sessionId}">
                    <span class="session-name-display">${this.escapeHtml(displayName)}</span>
                    <input class="form-control form-control-sm session-name-edit" type="text" value="${this.escapeHtml(displayName)}" style="display: none;">
                </div>
            </div>
        `;

        // Insert status indicator at the beginning
        const sessionHeader = sessionElement.querySelector('.d-flex');
        sessionHeader.insertBefore(statusIndicator, sessionHeader.firstChild);

        // Add double-click editing functionality
        const nameDisplay = sessionElement.querySelector('.session-name-display');
        const nameInput = sessionElement.querySelector('.session-name-edit');

        nameDisplay.addEventListener('dblclick', (e) => {
            e.stopPropagation();
            this.startEditingSessionName(sessionId, nameDisplay, nameInput);
        });

        this.setupSessionNameInput(sessionId, nameDisplay, nameInput);

        return sessionElement;
    }

    async toggleProjectExpansion(projectId) {
        try {
            const isExpanded = await this.projectManager.toggleExpansion(projectId);

            // If project was collapsed and current session belongs to this project, exit the session
            if (!isExpanded && this.currentSessionId) {
                const sessionProjectId = this._findProjectForSession(this.currentSessionId);
                if (sessionProjectId === projectId) {
                    this.exitSession();
                }
            }

            await this.updateProjectInDOM(projectId, 'expansion-toggled');
        } catch (error) {
            Logger.error('PROJECT', `Failed to toggle expansion for ${projectId}`, error);
        }
    }

    // ==================== GRANULAR DOM UPDATE METHODS ====================

    _findProjectForSession(sessionId) {
        /**
         * Find which project a session belongs to
         */
        for (const [projectId, project] of this.projectManager.projects) {
            if (project.session_ids && project.session_ids.includes(sessionId)) {
                return projectId;
            }
        }
        return null;
    }

    async updateProjectInDOM(projectId, updateType) {
        /**
         * Update a project element in the DOM without full re-render
         * @param {string} projectId - The project ID
         * @param {string} updateType - Type of update: 'session-added', 'session-removed',
         *                               'session-state-changed', 'expansion-toggled', 'metadata-changed'
         */
        const projectElement = document.querySelector(`[data-project-id="${projectId}"]`);
        if (!projectElement) {
            Logger.warn('DOM', `Project element not found for ${projectId}`);
            return;
        }

        const project = this.projectManager.projects.get(projectId);
        if (!project) {
            Logger.warn('DOM', `Project data not found for ${projectId}`);
            return;
        }

        switch (updateType) {
            case 'expansion-toggled':
                // Update arrow
                const arrow = projectElement.querySelector('.text-muted');
                if (arrow) {
                    arrow.textContent = project.is_expanded ? '▼' : '▶';
                }

                // Show or hide sessions container (using Bootstrap list-group class)
                let sessionsContainer = projectElement.querySelector('.list-group.list-group-flush');

                if (project.is_expanded) {
                    // Need to show sessions - create container if doesn't exist
                    if (!sessionsContainer && project.session_ids && project.session_ids.length > 0) {
                        sessionsContainer = document.createElement('div');
                        sessionsContainer.className = 'list-group list-group-flush';

                        // Use cached session data instead of fetching from API
                        const sessions = project.session_ids
                            .map(sid => this.sessions.get(sid))
                            .filter(s => s); // Filter out any sessions that aren't in cache yet

                        for (const session of sessions) {
                            const sessionElement = this.createSessionElement(session, projectId);
                            sessionsContainer.appendChild(sessionElement);
                        }

                        projectElement.appendChild(sessionsContainer);
                    }
                } else {
                    // Collapse - remove sessions container
                    if (sessionsContainer) {
                        sessionsContainer.remove();
                    }
                }
                break;

            case 'session-added':
            case 'session-removed':
            case 'session-state-changed':
                // Re-render status line for these changes
                await this.updateProjectStatusLine(projectId);
                break;

            case 'metadata-changed':
                // Update project name and path in the new Bootstrap structure
                const projectInfo = projectElement.querySelector('.flex-grow-1');
                if (projectInfo) {
                    const formattedPath = this.projectManager.formatPath(project.working_directory);
                    projectInfo.innerHTML = `
                        <div class="fw-semibold">${this.escapeHtml(project.name)}</div>
                        <small class="text-muted font-monospace" title="${this.escapeHtml(project.working_directory)}">${this.escapeHtml(formattedPath)}</small>
                    `;
                }
                break;
        }
    }

    updateSessionInDOM(sessionId, sessionData) {
        /**
         * Update a session element in the DOM without full re-render
         * Updates status indicator and name only
         * ALWAYS updates cache, even if DOM element doesn't exist (collapsed project)
         */
        // ALWAYS update cached session data first (even if DOM element doesn't exist)
        this.sessions.set(sessionId, sessionData);
        const index = this.orderedSessions.findIndex(s => s.session_id === sessionId);
        if (index !== -1) {
            this.orderedSessions[index] = sessionData;
        }

        // Now update DOM if element exists (project is expanded)
        const sessionElement = document.querySelector(`[data-session-id="${sessionId}"]`);
        if (!sessionElement) {
            Logger.debug('DOM', `Session element not in DOM (project collapsed): ${sessionId} - cache updated`);
            return;
        }

        // Update status indicator
        const sessionHeader = sessionElement.querySelector('.session-header');
        if (sessionHeader) {
            // Remove old status indicator
            const oldIndicator = sessionHeader.querySelector('.status-indicator');
            if (oldIndicator) {
                oldIndicator.remove();
            }

            // Create new status indicator
            const isProcessing = sessionData.is_processing || false;
            const displayState = isProcessing ? 'processing' : sessionData.state;
            const statusIndicator = this.createStatusIndicator(displayState, 'session', sessionData.state);

            // Insert at beginning of header
            sessionHeader.insertBefore(statusIndicator, sessionHeader.firstChild);
        }

        // Update session name if changed
        const nameDisplay = sessionElement.querySelector('.session-name-display');
        const nameInput = sessionElement.querySelector('.session-name-edit');
        if (nameDisplay && sessionData.name) {
            const displayName = sessionData.name || sessionId;
            if (nameDisplay.textContent !== displayName) {
                nameDisplay.textContent = displayName;
                if (nameInput) {
                    nameInput.value = displayName;
                }
            }
        }
    }

    async updateProjectStatusLine(projectId) {
        /**
         * Update just the project status line (multi-segment bar)
         * without re-rendering the entire project
         */
        const projectElement = document.querySelector(`[data-project-id="${projectId}"]`);
        if (!projectElement) {
            Logger.warn('DOM', `Project element not found for status line update: ${projectId}`);
            return;
        }

        const project = this.projectManager.projects.get(projectId);
        if (!project) {
            Logger.warn('DOM', `Project data not found for status line update: ${projectId}`);
            return;
        }

        // Find existing status line
        const oldStatusLine = projectElement.querySelector('.project-status-line');
        if (!oldStatusLine) {
            Logger.warn('DOM', `Status line element not found for ${projectId}`);
            return;
        }

        // Create new status line
        const newStatusLine = await this.createProjectStatusLine(project);

        // Replace old with new
        oldStatusLine.replaceWith(newStatusLine);
    }

    async addSessionToProjectDOM(projectId, sessionData) {
        /**
         * Add a new session element to a project's sessions container
         * Updates status line as well
         */
        const projectElement = document.querySelector(`[data-project-id="${projectId}"]`);
        if (!projectElement) {
            Logger.warn('DOM', `Project element not found for adding session: ${projectId}`);
            return;
        }

        const project = this.projectManager.projects.get(projectId);
        if (!project) {
            Logger.warn('DOM', `Project data not found for adding session: ${projectId}`);
            return;
        }

        // Update cached session data
        this.sessions.set(sessionData.session_id, sessionData);
        if (!this.orderedSessions.find(s => s.session_id === sessionData.session_id)) {
            this.orderedSessions.push(sessionData);
        }

        // Update project's session_ids if not already present
        if (!project.session_ids.includes(sessionData.session_id)) {
            project.session_ids.push(sessionData.session_id);
        }

        // If project is expanded, add session element to container
        if (project.is_expanded) {
            let sessionsContainer = projectElement.querySelector('.list-group.list-group-flush');

            // Create container if it doesn't exist
            if (!sessionsContainer) {
                sessionsContainer = document.createElement('div');
                sessionsContainer.className = 'list-group list-group-flush';
                projectElement.appendChild(sessionsContainer);
            }

            // Create and append session element
            const sessionElement = this.createSessionElement(sessionData, projectId);
            sessionsContainer.appendChild(sessionElement);
        }

        // Update status line to reflect new session
        await this.updateProjectStatusLine(projectId);
    }

    async showCreateSessionModalForProject(projectId) {
        this.currentProjectId = projectId;
        // Show modal without working directory field (project determines this)
        const modalElement = document.getElementById('create-session-modal');
        const workingDirGroup = document.getElementById('working-directory-group');
        workingDirGroup.style.display = 'none'; // Hide working directory field
        const modal = new bootstrap.Modal(modalElement);
        modal.show();
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
            Logger.error('SESSION', 'Failed to save session name', error);
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
            errorMessageElement.classList.remove('d-none');
            sessionInfoBar.classList.add('error');
            Logger.info('UI', 'Displaying error message in top bar', sessionData.session.error_message);

            // For error state: clear any processing indicator and disable input controls
            this.updateProcessingState(false);
            this.setInputControlsEnabled(false);
        } else {
            // Hide error message and remove error styling
            errorMessageElement.classList.add('d-none');
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
            existingSession.current_permission_mode = sessionData.session.current_permission_mode || 'acceptEdits';

            // Sync local permission mode state with backend (source of truth)
            this.currentPermissionMode = existingSession.current_permission_mode;

            // Update session data consistently (with automatic re-render)
            this.updateSessionData(this.currentSessionId, existingSession);
        }

        // Update permission mode display
        this.updatePermissionModeDisplay(sessionData.session);
    }

    updatePermissionModeDisplay(session) {
        const statusBar = document.getElementById('status-bar');
        const permissionModeClickable = document.getElementById('permission-mode-clickable');
        const permissionModeIcon = document.getElementById('permission-mode-icon');
        const permissionModeText = document.getElementById('permission-mode-text');

        const currentMode = session.current_permission_mode || 'acceptEdits';
        const isActive = session.state === 'active';

        // Show/hide status bar based on session state
        if (isActive) {
            statusBar.classList.remove('d-none');
        } else {
            statusBar.classList.add('d-none');
            return;
        }

        // Update mode display
        const modeConfig = {
            'default': {
                icon: '🔒',
                label: 'Mode: default',
                description: 'Click to cycle modes • Requires approval for most tools',
                color: 'grey'
            },
            'acceptEdits': {
                icon: '✏️',
                label: 'Mode: acceptEdits',
                description: 'Click to cycle modes • Auto-approves file edits',
                color: 'green'
            },
            'plan': {
                icon: '📋',
                label: 'Mode: plan',
                description: 'Click to cycle modes • Read-only mode',
                color: 'blue'
            }
        };

        const config = modeConfig[currentMode] || modeConfig['acceptEdits'];

        permissionModeIcon.textContent = config.icon;
        permissionModeText.textContent = config.label;

        // Set description as tooltip on the clickable area
        permissionModeClickable.title = config.description;
    }

    async cyclePermissionMode() {
        if (!this.currentSessionId) return;

        const session = this.sessions.get(this.currentSessionId);
        if (!session || session.state !== 'active') {
            Logger.debug('PERMISSION', 'Cannot cycle permission mode - session not active');
            return;
        }

        // Define cycle order (excluding bypassPermissions for safety)
        const modeOrder = ['default', 'acceptEdits', 'plan'];
        const currentMode = session.current_permission_mode || 'acceptEdits';
        const currentIndex = modeOrder.indexOf(currentMode);
        const nextIndex = (currentIndex + 1) % modeOrder.length;
        const nextMode = modeOrder[nextIndex];

        Logger.info('PERMISSION', 'Cycling permission mode', {from: currentMode, to: nextMode});

        try {
            const response = await this.apiRequest(`/api/sessions/${this.currentSessionId}/permission-mode`, {
                method: 'POST',
                body: JSON.stringify({ mode: nextMode })
            });

            if (response.success) {
                Logger.info('PERMISSION', 'Successfully changed permission mode', nextMode);
                // Update local session data
                session.current_permission_mode = nextMode;
                this.updatePermissionModeDisplay(session);
            }
        } catch (error) {
            Logger.error('PERMISSION', 'Failed to cycle permission mode', error);
            this.showError(`Failed to change permission mode: ${error.message}`);
        }
    }

    renderMessages(messages) {
        const messagesArea = document.getElementById('messages-area');
        messagesArea.innerHTML = '';

        // Clear the tool call manager for fresh loading
        this.toolCallManager = new ToolCallManager();

        // Single-pass processing for historical messages using unified logic
        Logger.debug('MESSAGE', 'Processing historical messages with unified single-pass approach', {count: messages.length});

        let toolUseCount = 0;
        messages.forEach(message => {
            // Process each message in order - tool calls will be created as needed
            const result = this.processMessage(message, 'historical');

            // Count tool uses for logging
            if (message.type === 'assistant' && message.metadata && message.metadata.tool_uses) {
                toolUseCount += message.metadata.tool_uses.length;
            }
        });

        Logger.debug('MESSAGE', 'Single-pass processing complete', {toolUseCount, messageCount: messages.length});
        this.smartScrollToBottom();
    }

    addMessageToUI(message, scroll = true) {
        const messagesArea = document.getElementById('messages-area');
        const messageElement = document.createElement('div');

        // Use metadata for enhanced styling if available
        const subtype = message.subtype || message.metadata?.subtype;
        const messageClass = subtype ? `message-row row py-1 ${message.type} ${subtype}` : `message-row row py-1 ${message.type}`;
        messageElement.className = messageClass;

        const timestamp = new Date(message.timestamp).toLocaleTimeString();

        // Determine speaker label
        let speakerLabel = message.type;
        if (message.type === 'assistant') {
            speakerLabel = 'agent';
        }

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

        // Two-column layout: speaker | content
        messageElement.innerHTML = `
            <div class="col-auto message-speaker text-end pe-3" title="${timestamp}">
                ${speakerLabel}
            </div>
            <div class="col message-content-column">
                ${contentHtml}
            </div>
        `;

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

        // Trim leading/trailing whitespace (internal newlines preserved by CSS white-space: pre-wrap)
        return `<div class="message-content">${this.escapeHtml(content.trim())}</div>`;
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
        Logger.debug('SESSION', 'Session state changed', stateData);

        // Update specific session in real-time instead of reloading all sessions
        const sessionId = stateData.session_id;
        const sessionInfo = stateData.session;

        // Skip processing if this session is being deleted
        if (this.deletingSessions.has(sessionId)) {
            Logger.debug('SESSION', 'Ignoring state change - deletion in progress', sessionId);
            return;
        }

        if (sessionInfo) {
            // GRANULAR UPDATE: Update session in DOM without full re-render
            this.updateSessionInDOM(sessionId, sessionInfo);

            // Update project status line to reflect state change
            const projectId = this._findProjectForSession(sessionId);
            if (projectId) {
                this.updateProjectStatusLine(projectId);
            }

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
        Logger.info('SESSION', 'Handling external deletion of session', sessionId);

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
        const modalElement = document.getElementById('create-session-modal');
        const modal = new bootstrap.Modal(modalElement);
        modal.show();
    }

    hideCreateSessionModal() {
        const modalElement = document.getElementById('create-session-modal');
        const modal = bootstrap.Modal.getInstance(modalElement);
        if (modal) {
            modal.hide();
        }
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
                Logger.error('SESSION', 'Session creation failed', error);
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

    // Project Modal Methods
    showCreateProjectModal() {
        const modalElement = document.getElementById('create-project-modal');
        const modal = new bootstrap.Modal(modalElement);
        modal.show();
    }

    hideCreateProjectModal() {
        const modalElement = document.getElementById('create-project-modal');
        const modal = bootstrap.Modal.getInstance(modalElement);
        if (modal) {
            modal.hide();
        }
        document.getElementById('create-project-form').reset();
    }

    async handleCreateProject(event) {
        event.preventDefault();

        const formData = new FormData(event.target);
        const data = Object.fromEntries(formData.entries());

        try {
            const project = await this.projectManager.createProject(data.name, data.working_directory);
            this.hideCreateProjectModal();
            await this.refreshSessions(); // Reload to show new project
            Logger.info('PROJECT', 'Project created successfully', project);
        } catch (error) {
            Logger.error('PROJECT', 'Project creation failed', error);
            this.showError('Failed to create project: ' + error.message);
        }
    }

    browseProjectDirectory() {
        // In a real implementation, this would open a directory picker
        const input = document.getElementById('project-working-directory');
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
        const modalElement = document.getElementById('delete-session-modal');
        const modal = new bootstrap.Modal(modalElement);
        modal.show();
    }

    hideDeleteSessionModal() {
        const modalElement = document.getElementById('delete-session-modal');
        const modal = bootstrap.Modal.getInstance(modalElement);
        if (modal) {
            modal.hide();
        }
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

            // Remove from orderedSessions array immediately
            this.orderedSessions = this.orderedSessions.filter(s => s.session_id !== sessionIdToDelete);

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

                Logger.info('SESSION', 'Session deleted successfully', sessionIdToDelete);
            } else {
                // Restore session to map and orderedSessions if deletion failed
                const sessionData = await this.apiRequest(`/api/sessions/${sessionIdToDelete}`).catch(() => null);
                if (sessionData && sessionData.session) {
                    this.sessions.set(sessionIdToDelete, sessionData.session);
                    // Re-add to orderedSessions if not already present
                    if (!this.orderedSessions.find(s => s.session_id === sessionIdToDelete)) {
                        this.orderedSessions.push(sessionData.session);
                    }
                    this.renderSessions();
                }
                throw new Error('Failed to delete session');
            }
        } catch (error) {
            Logger.error('SESSION', 'Failed to delete session', error);
            this.showError(`Failed to delete session: ${error.message}`);

            // Try to restore session to map and orderedSessions if deletion failed
            try {
                const sessionData = await this.apiRequest(`/api/sessions/${sessionIdToDelete}`);
                if (sessionData && sessionData.session) {
                    this.sessions.set(sessionIdToDelete, sessionData.session);
                    // Re-add to orderedSessions if not already present
                    if (!this.orderedSessions.find(s => s.session_id === sessionIdToDelete)) {
                        this.orderedSessions.push(sessionData.session);
                    }
                    this.renderSessions();
                }
            } catch (restoreError) {
                Logger.debug('SESSION', 'Could not restore session data after failed deletion');
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
            overlay.classList.remove('d-none');
        } else {
            overlay.classList.add('d-none');
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
            Logger.debug('UI', 'Input controls enabled');
        } else {
            // Disable input controls for error state
            messageInput.disabled = true;
            sendButton.disabled = true;
            messageInput.placeholder = "Session is in error state - input disabled";
            Logger.debug('UI', 'Input controls disabled due to error state');
        }
    }

    // Auto-scroll functionality
    toggleAutoScroll() {
        this.autoScrollEnabled = !this.autoScrollEnabled;
        const button = document.getElementById('auto-scroll-toggle');

        if (this.autoScrollEnabled) {
            button.textContent = '📜 Auto-scroll: ON';
            button.className = 'btn btn-sm btn-outline-secondary';
            this.smartScrollToBottom();
        } else {
            button.textContent = '📜 Auto-scroll: OFF';
            button.className = 'btn btn-sm btn-secondary';
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
            Logger.warn('SESSION', 'Session not found in orderedSessions, adding it', sessionId);
            this.orderedSessions.push(sessionInfo);
        }

        // Optionally trigger re-render
        if (!skipRender) {
            this.renderSessions();
        }
    }

    // Drag and Drop Methods
    addDragDropListeners(sessionElement, sessionId, projectId) {
        // Store drag state
        if (!this.dragState) {
            this.dragState = {
                draggedElement: null,
                draggedSessionId: null,
                draggedProjectId: null,
                dropIndicator: null,
                insertBefore: false
            };
        }

        sessionElement.addEventListener('dragstart', (e) => {
            this.dragState.draggedElement = sessionElement;
            this.dragState.draggedSessionId = sessionId;
            this.dragState.draggedProjectId = projectId;
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
            // Check if target is in same project
            const targetProjectId = sessionElement.getAttribute('data-project-id');

            if (this.dragState.draggedProjectId !== targetProjectId) {
                // Cross-project drag - show not-allowed cursor
                e.dataTransfer.dropEffect = 'none';
                return;
            }

            e.preventDefault();
            e.dataTransfer.dropEffect = 'move';

            if (this.dragState.draggedElement && sessionElement !== this.dragState.draggedElement) {
                this.showDropIndicator(sessionElement, e);
            }
        });

        sessionElement.addEventListener('dragenter', (e) => {
            const targetProjectId = sessionElement.getAttribute('data-project-id');
            if (this.dragState.draggedProjectId === targetProjectId) {
                e.preventDefault();
            }
        });

        sessionElement.addEventListener('dragleave', (e) => {
            // Only hide indicator if we're actually leaving the element
            if (!sessionElement.contains(e.relatedTarget)) {
                this.hideDropIndicator();
            }
        });

        sessionElement.addEventListener('drop', (e) => {
            e.preventDefault();

            // Validate same project
            const targetProjectId = sessionElement.getAttribute('data-project-id');
            if (this.dragState.draggedProjectId !== targetProjectId) {
                Logger.warn('SESSION', 'Cannot move session to different project');
                this.removeDragVisualEffects();
                return;
            }

            this.handleSessionDrop(sessionElement, sessionId, projectId);
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

    async handleSessionDrop(targetElement, targetSessionId, projectId) {
        if (!this.dragState.draggedSessionId || this.dragState.draggedSessionId === targetSessionId) {
            this.removeDragVisualEffects();
            return;
        }

        const draggedSessionId = this.dragState.draggedSessionId;

        try {
            // Get project to find its sessions
            const project = this.projectManager.getProject(projectId);
            if (!project) {
                throw new Error('Project not found');
            }

            // Calculate new order based on drop position (within project sessions only)
            const newOrder = this.calculateNewOrder(draggedSessionId, targetSessionId, project.session_ids);

            // Call project-specific reorder API
            await this.reorderProjectSessions(projectId, newOrder);

        } catch (error) {
            Logger.error('SESSION', 'Failed to reorder sessions', error);
            this.showError('Failed to reorder sessions');
        } finally {
            this.removeDragVisualEffects();
        }
    }

    calculateNewOrder(draggedSessionId, targetSessionId, projectSessionIds) {
        // Get current session IDs in order (from project)
        const currentOrder = [...projectSessionIds];

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

    async reorderProjectSessions(projectId, sessionIds) {
        try {
            await this.projectManager.reorderSessionsInProject(projectId, sessionIds);

            // Update local project cache
            const project = this.projectManager.getProject(projectId);
            if (project) {
                project.session_ids = sessionIds;
            }

            // Re-render to show new order
            await this.renderSessions();

            Logger.info('SESSION', `Reordered sessions in project ${projectId}`);
        } catch (error) {
            Logger.error('SESSION', 'Failed to reorder sessions in project', error);
            throw error;
        }
    }

    async reorderSessions(sessionIds) {
        // Legacy method - no longer used with project-based organization
        Logger.warn('SESSION', 'reorderSessions called - this method is deprecated in favor of reorderProjectSessions');
    }

    // Sidebar Management
    toggleSidebar() {
        const sidebar = document.getElementById('sidebar');
        const collapseBtn = document.getElementById('sidebar-collapse-btn');
        this.sidebarCollapsed = !this.sidebarCollapsed;

        if (this.sidebarCollapsed) {
            // Hide sidebar completely using Bootstrap's d-none
            sidebar.classList.add('d-none');
            collapseBtn.innerHTML = '›'; // Change arrow direction
            collapseBtn.title = 'Show sidebar';
        } else {
            // Show sidebar using Bootstrap
            sidebar.classList.remove('d-none');
            collapseBtn.innerHTML = '‹'; // Change arrow direction
            collapseBtn.title = 'Hide sidebar';
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
        // Get the main content container (parent of sidebar)
        const containerRect = sidebar.parentElement.getBoundingClientRect();
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