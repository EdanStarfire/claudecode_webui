/**
 * Tool Handler Registry for custom tool display rendering
 * Manages handlers for tool-specific rendering (parameters, results, summaries)
 */
class ToolHandlerRegistry {
    constructor() {
        this.handlers = new Map(); // toolName -> handler
        this.patternHandlers = []; // Array of {pattern: RegExp, handler}
    }

    /**
     * Register a handler for a specific tool name
     * @param {string} toolName - Name of the tool (e.g., "Read", "Edit")
     * @param {Object} handler - Handler object with render methods
     */
    registerHandler(toolName, handler) {
        this.handlers.set(toolName, handler);
    }

    /**
     * Register a handler for tools matching a pattern
     * @param {RegExp|string} pattern - Pattern to match tool names (e.g., /^mcp__/, "mcp__*")
     * @param {Object} handler - Handler object with render methods
     */
    registerPatternHandler(pattern, handler) {
        const regex = typeof pattern === 'string'
            ? new RegExp('^' + pattern.replace(/\*/g, '.*') + '$')
            : pattern;
        this.patternHandlers.push({ pattern: regex, handler });
    }

    /**
     * Get handler for a specific tool name
     * @param {string} toolName - Name of the tool
     * @returns {Object|null} Handler object or null if no handler found
     */
    getHandler(toolName) {
        // Check exact match first
        if (this.handlers.has(toolName)) {
            return this.handlers.get(toolName);
        }

        // Check pattern matches
        for (const { pattern, handler } of this.patternHandlers) {
            if (pattern.test(toolName)) {
                return handler;
            }
        }

        return null;
    }

    /**
     * Check if a handler exists for a tool
     * @param {string} toolName - Name of the tool
     * @returns {boolean}
     */
    hasHandler(toolName) {
        return this.getHandler(toolName) !== null;
    }
}
