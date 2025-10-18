// MCP Tool Handler - displays MCP server tool calls
class McpToolHandler {
    /**
     * Parse MCP tool name into components
     * Format: mcp__<server-name>__<tool-name>
     */
    parseMcpToolName(toolName) {
        if (!toolName.startsWith('mcp__')) {
            return null;
        }

        // Remove 'mcp__' prefix and split by '__'
        const parts = toolName.substring(5).split('__');
        if (parts.length < 2) {
            return null;
        }

        const serverName = parts[0];
        const toolNamePart = parts.slice(1).join('__'); // Handle tool names with __ in them

        return {
            serverName,
            toolName: toolNamePart
        };
    }

    renderParameters(toolCall, escapeHtmlFn) {
        const parsed = this.parseMcpToolName(toolCall.name);
        if (!parsed) {
            return `<div class="tool-parameters">Invalid MCP tool name format</div>`;
        }

        const { serverName, toolName } = parsed;
        const input = toolCall.input || {};
        const hasParams = Object.keys(input).length > 0;

        let html = '<div class="tool-parameters tool-mcp-params">';

        // Header with MCP server and tool info
        html += '<div class="mcp-header">';
        html += '<span class="mcp-icon">🔌</span>';
        html += '<div class="mcp-header-content">';
        html += '<div class="mcp-server-row">';
        html += '<span class="mcp-label">MCP Server:</span> ';
        html += `<code class="mcp-server-name">${escapeHtmlFn(serverName)}</code>`;
        html += '</div>';
        html += '<div class="mcp-tool-row">';
        html += '<span class="mcp-label">Tool:</span> ';
        html += `<code class="mcp-tool-name">${escapeHtmlFn(toolName)}</code>`;
        html += '</div>';
        html += '</div>';
        html += '</div>';

        // Parameters section (if any)
        if (hasParams) {
            html += '<div class="mcp-params-section">';
            html += '<div class="mcp-params-label"><strong>Parameters:</strong></div>';
            html += '<pre class="mcp-params-json">';
            html += escapeHtmlFn(JSON.stringify(input, null, 2));
            html += '</pre>';
            html += '</div>';
        } else {
            html += '<div class="mcp-no-params">No parameters</div>';
        }

        html += '</div>';
        return html;
    }

    renderResult(toolCall, escapeHtmlFn) {
        if (!toolCall.result) return '';

        const resultClass = toolCall.result.error ? 'tool-result-error' : 'tool-result-success';
        const content = toolCall.result.content || '';

        if (toolCall.result.error) {
            return `
                <div class="tool-result ${resultClass}">
                    <strong>MCP Tool Error:</strong>
                    <pre class="tool-result-content">${escapeHtmlFn(content || 'Unknown error')}</pre>
                </div>
            `;
        }

        // Try to parse as JSON for pretty display
        let displayContent = content;
        let isJson = false;

        try {
            const parsed = JSON.parse(content);
            displayContent = JSON.stringify(parsed, null, 2);
            isJson = true;
        } catch (e) {
            // Not JSON, display as-is
        }

        return `
            <div class="tool-result ${resultClass}">
                <div class="mcp-success-header">
                    <span class="success-icon">✅</span>
                    <strong>MCP Tool Result</strong>
                </div>
                <pre class="tool-result-content ${isJson ? 'json-content' : ''}">${escapeHtmlFn(displayContent)}</pre>
            </div>
        `;
    }

    getCollapsedSummary(toolCall) {
        const parsed = this.parseMcpToolName(toolCall.name);
        if (!parsed) {
            return '🔌 MCP Tool';
        }

        const { serverName, toolName } = parsed;

        const statusIcon = {
            'pending': '🔄',
            'permission_required': '❓',
            'executing': '⚡',
            'completed': toolCall.permissionDecision === 'deny' ? '❌' : '✅',
            'error': '💥'
        }[toolCall.status] || '🔧';

        let statusText = '';
        if (toolCall.status === 'completed' && !toolCall.result?.error) {
            statusText = 'Success';
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

        return `${statusIcon} MCP (${serverName}): ${toolName} - ${statusText}`;
    }
}
