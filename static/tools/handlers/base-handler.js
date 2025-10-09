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
