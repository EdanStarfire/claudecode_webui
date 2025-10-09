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

        // Parse file content and show preview with diff-style formatting
        const content = toolCall.result.content || '';
        const lines = content.split('\n');
        const previewLimit = 100;
        const hasMore = lines.length > previewLimit;
        const previewLines = lines.slice(0, previewLimit);
        const previewContent = previewLines.join('\n');

        // Generate diff HTML using same approach as Edit tool
        let diffHtml = '';
        try {
            if (typeof Diff !== 'undefined' && typeof Diff2Html !== 'undefined') {
                // Create a patch showing content as context (unchanged) - from empty to content
                const patch = Diff.createPatch('file', '', previewContent, '', '', { context: 999999 });

                diffHtml = Diff2Html.html(patch, {
                    drawFileList: false,
                    matching: 'lines',
                    outputFormat: 'line-by-line',
                    highlight: false
                });
            }
        } catch (e) {
            // Fallback to plain text if diff fails
            diffHtml = `<pre class="tool-result-content read-content-preview">${escapeHtmlFn(previewContent)}</pre>`;
        }

        return `
            <div class="tool-result ${resultClass}">
                <div class="read-result-header">
                    <span class="diff-label">Content Preview:</span>
                    <span class="read-line-count">${lines.length} lines</span>
                    ${hasMore ? `<span class="read-preview-note">(showing first ${previewLimit})</span>` : ''}
                </div>
                <div class="read-diff-container">
                    ${diffHtml}
                </div>
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
