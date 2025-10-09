// Grep Tool Handler - custom display for Grep tool (content search)
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
