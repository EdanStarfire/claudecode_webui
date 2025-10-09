/**
 * Standardized logging utility for consistent log formatting
 * Uses browser console's native log levels for filtering
 */
const Logger = {
    /**
     * Format a log message with timestamp and category
     * @param {string} category - Log category (e.g., 'TOOL_MANAGER', 'SESSION', 'WS')
     * @param {string} message - The log message
     * @param {*} data - Optional structured data to append
     * @returns {string} Formatted log message
     */
    formatMessage(category, message, data = null) {
        const timestamp = new Date().toISOString();
        const baseMsg = `${timestamp} - [${category}] ${message}`;

        if (data !== null && data !== undefined) {
            // For objects/arrays, return base message (data will be passed separately to console)
            return baseMsg;
        }

        return baseMsg;
    },

    /**
     * Debug level - verbose debugging, filtered by browser settings
     */
    debug(category, message, data = null) {
        if (data !== null && data !== undefined) {
            console.debug(this.formatMessage(category, message), data);
        } else {
            console.debug(this.formatMessage(category, message));
        }
    },

    /**
     * Info level - important events (connections, actions, completions)
     */
    info(category, message, data = null) {
        if (data !== null && data !== undefined) {
            console.info(this.formatMessage(category, message), data);
        } else {
            console.info(this.formatMessage(category, message));
        }
    },

    /**
     * Warning level - warnings (retries, fallbacks, deprecated paths)
     */
    warn(category, message, data = null) {
        if (data !== null && data !== undefined) {
            console.warn(this.formatMessage(category, message), data);
        } else {
            console.warn(this.formatMessage(category, message));
        }
    },

    /**
     * Error level - errors (failures, exceptions)
     */
    error(category, message, data = null) {
        if (data !== null && data !== undefined) {
            console.error(this.formatMessage(category, message), data);
        } else {
            console.error(this.formatMessage(category, message));
        }
    }
};
