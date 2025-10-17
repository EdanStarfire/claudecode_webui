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

    countChangedLines(oldString, newString) {
        // Count actual changed lines using diff library
        if (typeof Diff === 'undefined') {
            // Fallback: count all lines in both strings (additions + deletions)
            const oldLines = oldString.split('\n').filter(line => line.length > 0).length;
            const newLines = newString.split('\n').filter(line => line.length > 0).length;
            return { added: newLines, removed: oldLines };
        }

        // Use Diff.diffLines to get actual line-level changes
        const changes = Diff.diffLines(oldString, newString);
        let added = 0;
        let removed = 0;

        changes.forEach(change => {
            if (change.added) {
                added += change.count || 0;
            } else if (change.removed) {
                removed += change.count || 0;
            }
        });

        return { added, removed };
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

        let statusText = '';
        if (toolCall.status === 'completed' && !toolCall.result?.error) {
            // Count actual changed lines (additions and deletions separately)
            const { added, removed } = this.countChangedLines(
                toolCall.input.old_string || '',
                toolCall.input.new_string || ''
            );
            statusText = `${added} added, ${removed} removed`;
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

    countChangedLines(oldString, newString) {
        // Count actual changed lines using diff library
        if (typeof Diff === 'undefined') {
            // Fallback: count all lines in both strings (additions + deletions)
            const oldLines = oldString.split('\n').filter(line => line.length > 0).length;
            const newLines = newString.split('\n').filter(line => line.length > 0).length;
            return { added: newLines, removed: oldLines };
        }

        // Use Diff.diffLines to get actual line-level changes
        const changes = Diff.diffLines(oldString, newString);
        let added = 0;
        let removed = 0;

        changes.forEach(change => {
            if (change.added) {
                added += change.count || 0;
            } else if (change.removed) {
                removed += change.count || 0;
            }
        });

        return { added, removed };
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
            // Count total changed lines across all edits
            const edits = toolCall.input.edits || [];
            const totals = edits.reduce((acc, edit) => {
                const { added, removed } = this.countChangedLines(edit.old_string || '', edit.new_string || '');
                return { added: acc.added + added, removed: acc.removed + removed };
            }, { added: 0, removed: 0 });
            statusText = `${editCount} edits, ${totals.added} added, ${totals.removed} removed`;
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
