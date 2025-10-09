// Write Tool Handler - custom display for Write tool (creating new files)
class WriteToolHandler {
    renderParameters(toolCall, escapeHtmlFn) {
        const filePath = toolCall.input.file_path || 'Unknown';
        const content = toolCall.input.content || '';
        const lines = content.split('\n');
        const previewLimit = 100;
        const hasMore = lines.length > previewLimit;
        const previewLines = lines.slice(0, previewLimit);
        const previewContent = previewLines.join('\n');

        // Generate diff HTML using same approach as Edit tool
        let diffHtml = '';
        try {
            if (typeof Diff !== 'undefined' && typeof Diff2Html !== 'undefined') {
                // Create a patch showing content as added (from empty to content)
                const patch = Diff.createPatch('file', '', previewContent, '', '', { context: 3 });

                diffHtml = Diff2Html.html(patch, {
                    drawFileList: false,
                    matching: 'lines',
                    outputFormat: 'line-by-line',
                    highlight: false
                });
            }
        } catch (e) {
            // Fallback to plain text if diff fails
            diffHtml = `<pre class="write-content-preview">${escapeHtmlFn(previewContent)}</pre>`;
        }

        return `
            <div class="tool-parameters tool-write-params">
                <div class="write-file-path">
                    <span class="write-icon">📝</span>
                    <strong>Writing:</strong>
                    <code class="file-path">${escapeHtmlFn(filePath)}</code>
                </div>
                <div class="write-diff-container">
                    <div class="write-content-header">
                        <span class="diff-label">Content:</span>
                        <span class="write-line-count">${lines.length} lines</span>
                        ${hasMore ? `<span class="write-preview-note">(showing first ${previewLimit})</span>` : ''}
                    </div>
                    ${diffHtml}
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
