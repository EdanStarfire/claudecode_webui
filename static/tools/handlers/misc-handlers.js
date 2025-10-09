// Task Tool Handler - custom display for Task tool (subagent execution)
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
