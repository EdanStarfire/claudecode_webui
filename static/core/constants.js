/**
 * Application-wide constants and configuration
 */

/**
 * Status color configuration for session and WebSocket states
 */
const STATUS_COLORS = {
    // Session states
    session: {
        'CREATED': { color: 'grey', animate: false, text: 'Created' },
        'created': { color: 'grey', animate: false, text: 'Created' },
        'Starting': { color: 'green', animate: true, text: 'Starting' },
        'starting': { color: 'green', animate: true, text: 'Starting' },
        'running': { color: 'green', animate: false, text: 'Running' },
        'active': { color: 'green', animate: false, text: 'Active' },
        'processing': { color: 'purple', animate: true, text: 'Processing' },
        'completed': { color: 'grey', animate: false, text: 'Completed' },
        'failed': { color: 'red', animate: false, text: 'Failed' },
        'error': { color: 'red', animate: false, text: 'Failed' },
        'terminated': { color: 'grey', animate: false, text: 'Terminated' },
        'paused': { color: 'grey', animate: false, text: 'Paused' },
        'unknown': { color: 'purple', animate: true, text: 'Unknown' }
    },
    // WebSocket states
    websocket: {
        'connecting': { color: 'green', animate: true, text: 'Connecting' },
        'connected': { color: 'green', animate: false, text: 'Connected' },
        'disconnected': { color: 'red', animate: false, text: 'Disconnected' },
        'unknown': { color: 'purple', animate: true, text: 'Unknown' }
    }
};

/**
 * WebSocket retry configuration
 */
const WEBSOCKET_CONFIG = {
    maxSessionRetries: 5,
    maxUIRetries: 10
};

/**
 * Sidebar configuration
 */
const SIDEBAR_CONFIG = {
    mobileBreakpoint: 768,  // pixels
    minWidth: 200,          // pixels
    maxWidthPercent: 0.3,   // 30% of viewport
    defaultWidth: 300       // pixels
};
