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
