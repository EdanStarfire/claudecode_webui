// Bash Tool Handler - custom display for Bash tool (command execution)
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

// BashOutput Tool Handler - custom display for BashOutput tool (checking background process)
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

// KillShell Tool Handler - custom display for KillShell tool (terminating background process)
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
