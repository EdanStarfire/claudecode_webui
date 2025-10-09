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
