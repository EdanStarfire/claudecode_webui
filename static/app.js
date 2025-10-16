// Claude Code WebUI JavaScript Application


// External modules are loaded via index.html:
// - Logger (core/logger.js)
// - APIClient (core/api-client.js)
// - Constants (core/constants.js)
// - ProjectManager (core/project-manager.js)
// - ToolCallManager (tools/tool-call-manager.js)
// - ToolHandlerRegistry (tools/tool-handler-registry.js)
// - All tool handlers (tools/handlers/*.js)

class ClaudeWebUI {
    constructor() {
        this.currentSessionId = null;
        this.sessions = new Map();
        this.orderedSessions = []; // Maintains session order from backend

        // Project management
        this.projectManager = new ProjectManager(this);
        this.currentProjectId = null;

        // Session-specific WebSocket for message streaming
        this.sessionWebsocket = null;
        this.sessionConnectionRetryCount = 0;
        this.maxSessionRetries = 5;
        this.intentionalSessionDisconnect = false;

        // Global UI WebSocket for session state updates
        this.uiWebsocket = null;
        this.uiConnectionRetryCount = 0;
        this.maxUIRetries = 10;

        // Auto-scroll functionality
        this.autoScrollEnabled = true;
        this.isUserScrolling = false;
        this.scrollTimeout = null;

        // Processing state management
        this.isProcessing = false;

        // Status indicator configuration
        this.statusColors = this.initializeStatusColors();

        // Sidebar state management
        // On mobile (<768px), sidebar starts collapsed (hidden via CSS transform)
        this.sidebarCollapsed = window.innerWidth < 768;
        this.sidebarWidth = 300;
        this.isResizing = false;

        // Session deletion state tracking
        this.deletingSessions = new Set();

        // Tool call management
        this.toolCallManager = new ToolCallManager();

        // Tool handler registry for custom tool display
        this.toolHandlerRegistry = new ToolHandlerRegistry();
        this.defaultToolHandler = new DefaultToolHandler();
        this.initializeToolHandlers();

        // Permission mode tracking
        this.currentPermissionMode = 'default'; // default, acceptEdits, plan, etc.

        // Session input cache for preserving input box content per session
        this.sessionInputCache = new Map(); // session_id -> input text

        // Session init data tracking for session info modal
        this.sessionInitData = new Map(); // session_id -> init message data

        this.init();
    }

    initializeToolHandlers() {
        // Register built-in tool handlers
        this.toolHandlerRegistry.registerHandler('Read', new ReadToolHandler());
        this.toolHandlerRegistry.registerHandler('Edit', new EditToolHandler());
        this.toolHandlerRegistry.registerHandler('MultiEdit', new MultiEditToolHandler());
        this.toolHandlerRegistry.registerHandler('Write', new WriteToolHandler());
        this.toolHandlerRegistry.registerHandler('TodoWrite', new TodoWriteToolHandler());
        this.toolHandlerRegistry.registerHandler('Grep', new GrepToolHandler());
        this.toolHandlerRegistry.registerHandler('Glob', new GlobToolHandler());
        this.toolHandlerRegistry.registerHandler('WebFetch', new WebFetchToolHandler());
        this.toolHandlerRegistry.registerHandler('WebSearch', new WebSearchToolHandler());
        this.toolHandlerRegistry.registerHandler('Bash', new BashToolHandler());
        this.toolHandlerRegistry.registerHandler('BashOutput', new BashOutputToolHandler());
        this.toolHandlerRegistry.registerHandler('KillShell', new KillShellToolHandler());
        this.toolHandlerRegistry.registerHandler('Task', new TaskToolHandler());
        this.toolHandlerRegistry.registerHandler('ExitPlanMode', new ExitPlanModeToolHandler());

        // Register pattern handlers for MCP tools (can be customized later)
        // this.toolHandlerRegistry.registerPatternHandler('mcp__*', new McpToolHandler());
    }

    initializeStatusColors() {
        return {
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
    }

    createStatusIndicator(state, type, actualState = null) {
        const config = this.statusColors[type] && this.statusColors[type][state]
            ? this.statusColors[type][state]
            : this.statusColors[type]['unknown'];

        const indicator = document.createElement('span');
        indicator.className = `status-dot status-dot-${config.color}`;

        if (config.animate) {
            indicator.classList.add('status-dot-blink');
        }

        // Set hover text - show actual state for unknown states
        const hoverText = actualState && state === 'unknown'
            ? `Unknown state: ${actualState}`
            : config.text;
        indicator.title = hoverText;

        return indicator;
    }

    async init() {
        this.setupEventListeners();
        this.connectUIWebSocket();
        this.updateConnectionStatus('disconnected');

        // Load projects and sessions on startup
        await this.loadSessions();
    }

    setupEventListeners() {
        // Project controls
        document.getElementById('create-project-btn').addEventListener('click', () => this.showCreateProjectModal());
        document.getElementById('refresh-sessions-btn').addEventListener('click', () => this.refreshSessions());

        // Project modal controls (Bootstrap modals handle close/cancel via data-bs-dismiss)
        document.getElementById('create-project-form').addEventListener('submit', (e) => this.handleCreateProject(e));
        document.getElementById('browse-project-directory').addEventListener('click', () => this.browseProjectDirectory());

        // Session modal controls (Bootstrap modals handle close/cancel via data-bs-dismiss)
        document.getElementById('create-session-form').addEventListener('submit', (e) => this.handleCreateSession(e));

        // Browse directory button
        document.getElementById('browse-directory').addEventListener('click', () => this.browseDirectory());

        // Session actions
        document.getElementById('manage-session-btn').addEventListener('click', () => this.showManageSessionModal());
        document.getElementById('restart-session-btn').addEventListener('click', () => this.restartSession());
        document.getElementById('reset-session-btn').addEventListener('click', () => this.resetSession());
        document.getElementById('end-session-btn').addEventListener('click', () => this.endSession());

        // Project edit modal controls
        document.getElementById('save-project-btn').addEventListener('click', () => this.confirmProjectRename());
        document.getElementById('delete-project-btn').addEventListener('click', () => this.showDeleteProjectConfirmation());
        document.getElementById('confirm-delete-project').addEventListener('click', () => this.confirmDeleteProject());

        // Session edit modal controls
        document.getElementById('save-session-btn').addEventListener('click', () => this.confirmSessionRename());
        document.getElementById('delete-session-from-edit-btn').addEventListener('click', () => this.showDeleteSessionConfirmation());
        document.getElementById('confirm-delete-session').addEventListener('click', () => this.confirmDeleteSession());

        // Folder browser modal controls
        document.getElementById('folder-browser-up').addEventListener('click', () => {
            const currentPath = document.getElementById('folder-browser-path').value;
            // Extract parent path by going up one level
            const pathParts = currentPath.split(/[/\\]/);
            pathParts.pop();
            const parentPath = pathParts.join('/') || '/';
            this.browseFolderPath(parentPath);
        });
        document.getElementById('folder-browser-go').addEventListener('click', () => {
            const manualPath = document.getElementById('folder-browser-manual-path').value;
            if (manualPath) {
                this.browseFolderPath(manualPath);
            }
        });
        document.getElementById('folder-browser-manual-path').addEventListener('keydown', (e) => {
            if (e.key === 'Enter') {
                e.preventDefault();
                const manualPath = document.getElementById('folder-browser-manual-path').value;
                if (manualPath) {
                    this.browseFolderPath(manualPath);
                }
            }
        });
        document.getElementById('folder-browser-select').addEventListener('click', () => this.selectCurrentFolder());

        // Sidebar controls
        document.getElementById('sidebar-toggle-btn').addEventListener('click', () => this.toggleSidebar());
        document.getElementById('sidebar-resize-handle').addEventListener('mousedown', (e) => this.startResize(e));

        // Mobile backdrop click to close sidebar
        document.getElementById('sidebar-backdrop').addEventListener('click', () => {
            if (!this.sidebarCollapsed && window.innerWidth < 768) {
                this.toggleSidebar();
            }
        });

        // Message sending
        document.getElementById('send-btn').addEventListener('click', () => this.handleSendButtonClick());
        document.getElementById('interrupt-send-btn').addEventListener('click', () => this.handleInterruptSendClick());
        const messageInput = document.getElementById('message-input');
        messageInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                this.handleSendButtonClick();
            }
        });

        // Auto-expand textarea
        messageInput.addEventListener('input', (e) => this.autoExpandTextarea(e.target));

        // Auto-scroll toggle
        document.getElementById('auto-scroll-toggle').addEventListener('click', () => this.toggleAutoScroll());

        // Permission mode cycling - click on icon/text area to cycle
        document.getElementById('permission-mode-clickable').addEventListener('click', () => this.cyclePermissionMode());

        // Session info modal button
        document.getElementById('session-info-btn').addEventListener('click', () => this.showSessionInfoModal());

        // Messages area scroll detection
        document.getElementById('messages-area').addEventListener('scroll', (e) => this.handleScroll(e));

        // Bootstrap modals handle backdrop clicks automatically, no custom listeners needed

        // Window resize handling for sidebar constraints
        window.addEventListener('resize', () => this.handleWindowResize());
    }

    // API Methods
    async apiRequest(endpoint, options = {}) {
        try {
            const response = await fetch(endpoint, {
                headers: {
                    'Content-Type': 'application/json',
                    ...options.headers
                },
                ...options
            });

            if (!response.ok) {
                throw new Error(`HTTP ${response.status}: ${response.statusText}`);
            }

            return await response.json();
        } catch (error) {
            Logger.error('API', 'API request failed', error);
            this.showError(`API request failed: ${error.message}`);
            throw error;
        }
    }

    // Session Management
    async loadSessions() {
        try {
            this.showLoading(true);

            // Load projects first
            await this.projectManager.loadProjects();

            // Load all sessions into cache
            const data = await this.apiRequest('/api/sessions');
            this.sessions.clear();
            this.orderedSessions = [];

            data.sessions.forEach(session => {
                this.sessions.set(session.session_id, session);
                this.orderedSessions.push(session);
            });

            await this.renderSessions();
        } catch (error) {
            Logger.error('SESSION', 'Failed to load sessions', error);
        } finally {
            this.showLoading(false);
        }
    }

    async createSession(formData) {
        try {
            this.showLoading(true);

            const tools = formData.tools ? formData.tools.split(',').map(t => t.trim()).filter(t => t) : [];

            const payload = {
                project_id: this.currentProjectId, // Use current project ID
                permission_mode: formData.permission_mode,
                system_prompt: formData.system_prompt || null,
                tools: tools,
                model: formData.model || null,
                name: formData.name || null
            };

            const data = await this.apiRequest('/api/sessions', {
                method: 'POST',
                body: JSON.stringify(payload)
            });

            // GRANULAR UPDATE: Fetch the new session data and add it to DOM directly
            // This is faster than waiting for WebSocket and avoids race conditions
            const sessionData = await this.apiRequest(`/api/sessions/${data.session_id}`);

            // Add session to project in DOM
            await this.addSessionToProjectDOM(this.currentProjectId, sessionData.session);

            // Cache current session's input before creating new session
            if (this.currentSessionId) {
                const messageInput = document.getElementById('message-input');
                if (messageInput) {
                    this.sessionInputCache.set(this.currentSessionId, messageInput.value);
                }
            }

            // Select the new session
            await this.selectSession(data.session_id);

            return data.session_id;
        } catch (error) {
            Logger.error('SESSION', 'Failed to create session', error);
            throw error;
        } finally {
            this.showLoading(false);
        }
    }

    showManageSessionModal() {
        if (!this.currentSessionId) return;

        // Store original modal content on first access
        const modalEl = document.getElementById('manage-session-modal');
        if (!this.manageSessionModalOriginalContent) {
            this.manageSessionModalOriginalContent = {
                title: modalEl.querySelector('.modal-title').innerHTML,
                body: modalEl.querySelector('.modal-body').innerHTML,
                footer: modalEl.querySelector('.modal-footer').innerHTML
            };
        }

        // Restore original content before showing
        this.restoreManageSessionModal();

        const modal = new bootstrap.Modal(modalEl);
        modal.show();
    }

    restoreManageSessionModal() {
        if (!this.manageSessionModalOriginalContent) return;

        const modalEl = document.getElementById('manage-session-modal');
        const modalTitle = modalEl.querySelector('.modal-title');
        const modalBody = modalEl.querySelector('.modal-body');
        const modalFooter = modalEl.querySelector('.modal-footer');

        modalTitle.innerHTML = this.manageSessionModalOriginalContent.title;
        modalBody.innerHTML = this.manageSessionModalOriginalContent.body;
        modalFooter.innerHTML = this.manageSessionModalOriginalContent.footer;

        // Reattach event listeners after restoring content
        this.attachManageSessionListeners();
    }

    attachManageSessionListeners() {
        // Reattach event listeners for manage session modal buttons
        const restartBtn = document.getElementById('restart-session-btn');
        const resetBtn = document.getElementById('reset-session-btn');
        const endBtn = document.getElementById('end-session-btn');

        if (restartBtn) restartBtn.addEventListener('click', () => this.restartSession());
        if (resetBtn) resetBtn.addEventListener('click', () => this.resetSession());
        if (endBtn) endBtn.addEventListener('click', () => this.endSession());
    }

    async restartSession() {
        if (!this.currentSessionId) return;

        const modalEl = document.getElementById('manage-session-modal');
        const modalBody = modalEl.querySelector('.modal-body');
        const modalFooter = modalEl.querySelector('.modal-footer');

        try {
            Logger.info('SESSION', 'Restarting session', this.currentSessionId);

            const sessionId = this.currentSessionId;

            // Show loading state in modal
            modalBody.innerHTML = `
                <div class="text-center py-4">
                    <div class="spinner-border text-primary mb-3" role="status">
                        <span class="visually-hidden">Loading...</span>
                    </div>
                    <p class="text-muted">Restarting session...</p>
                </div>
            `;
            modalFooter.innerHTML = ''; // Remove buttons

            // Call restart endpoint
            const response = await this.apiRequest(`/api/sessions/${sessionId}/restart`, {
                method: 'POST'
            });

            if (response.success) {
                Logger.info('SESSION', 'Session restarted successfully, reconnecting');
                // Disconnect WebSocket before calling selectSession to force reconnection
                this.disconnectSessionWebSocket();
                // Use selectSession to handle reconnection and state polling
                await this.selectSession(sessionId);

                // Close modal after successful restart
                const modal = bootstrap.Modal.getInstance(modalEl);
                if (modal) modal.hide();
            } else {
                Logger.error('SESSION', 'Failed to restart session');
                alert('Failed to restart session');
                // Restore modal on error
                this.restoreManageSessionModal();
            }
        } catch (error) {
            Logger.error('SESSION', 'Error restarting session', error);
            alert('Error restarting session');
            // Restore modal on error
            this.restoreManageSessionModal();
        }
    }

    async resetSession() {
        if (!this.currentSessionId) return;

        // Show inline confirmation in modal
        this.showResetSessionConfirmation();
    }

    showResetSessionConfirmation() {
        const modalEl = document.getElementById('manage-session-modal');
        const modalBody = modalEl.querySelector('.modal-body');
        const modalTitle = modalEl.querySelector('.modal-title');
        const modalFooter = modalEl.querySelector('.modal-footer');

        // Update modal title
        modalTitle.innerHTML = 'Reset Session';

        // Replace modal body with confirmation warning
        modalBody.innerHTML = `
            <div class="alert alert-danger mb-3">
                <strong>⚠️ Warning: This action cannot be undone</strong>
            </div>
            <p class="text-muted">This will permanently delete all messages and conversation history for this session.</p>
            <p class="text-muted">The session settings and configuration will be preserved, but you will start with a completely fresh conversation.</p>
        `;

        // Replace modal footer with confirm/cancel buttons
        modalFooter.innerHTML = `
            <button type="button" class="btn btn-secondary" id="reset-cancel-btn">Cancel</button>
            <button type="button" class="btn btn-danger" id="reset-confirm-btn">Confirm Reset</button>
        `;

        // Add event listeners
        document.getElementById('reset-cancel-btn').addEventListener('click', () => {
            // Restore original modal content
            this.restoreManageSessionModal();
        });

        document.getElementById('reset-confirm-btn').addEventListener('click', async () => {
            await this.confirmResetSession();
        });
    }

    async confirmResetSession() {
        if (!this.currentSessionId) return;

        const modalEl = document.getElementById('manage-session-modal');
        const modalBody = modalEl.querySelector('.modal-body');
        const modalFooter = modalEl.querySelector('.modal-footer');

        try {
            Logger.info('SESSION', 'Resetting session', this.currentSessionId);

            const sessionId = this.currentSessionId;

            // Show loading state in modal
            modalBody.innerHTML = `
                <div class="text-center py-4">
                    <div class="spinner-border text-primary mb-3" role="status">
                        <span class="visually-hidden">Loading...</span>
                    </div>
                    <p class="text-muted">Resetting session...</p>
                </div>
            `;
            modalFooter.innerHTML = ''; // Remove buttons

            // Call reset endpoint
            const response = await this.apiRequest(`/api/sessions/${sessionId}/reset`, {
                method: 'POST'
            });

            if (response.success) {
                Logger.info('SESSION', 'Session reset successfully, reconnecting');
                // Clear UI messages before reconnecting
                document.getElementById('messages-area').innerHTML = '';
                // Disconnect WebSocket before calling selectSession to force reconnection
                this.disconnectSessionWebSocket();
                // Use selectSession to handle reconnection and state polling
                await this.selectSession(sessionId);

                // Close modal after successful reset
                const modal = bootstrap.Modal.getInstance(modalEl);
                if (modal) modal.hide();
            } else {
                Logger.error('SESSION', 'Failed to reset session');
                alert('Failed to reset session');
                // Restore modal on error
                this.restoreManageSessionModal();
            }
        } catch (error) {
            Logger.error('SESSION', 'Error resetting session', error);
            alert('Error resetting session');
            // Restore modal on error
            this.restoreManageSessionModal();
        }
    }

    async endSession() {
        if (!this.currentSessionId) return;

        try {
            // Close modal first
            const modalEl = document.getElementById('manage-session-modal');
            const modal = bootstrap.Modal.getInstance(modalEl);
            if (modal) modal.hide();

            Logger.info('SESSION', 'Ending session', this.currentSessionId);

            // Call terminate endpoint to properly set session state
            await this.apiRequest(`/api/sessions/${this.currentSessionId}/terminate`, {
                method: 'POST'
            });

            // Use existing exitSession logic to clean up UI
            this.exitSession();

        } catch (error) {
            Logger.error('SESSION', 'Error ending session', error);
            // Still exit UI even if terminate fails
            this.exitSession();
        }
    }

    exitSession() {
        if (!this.currentSessionId) return;

        Logger.info('SESSION', `Exiting session ${this.currentSessionId}`);

        // Clean disconnect from WebSocket
        this.disconnectSessionWebSocket();

        // Clear cached input for exited session
        if (this.currentSessionId) {
            this.sessionInputCache.delete(this.currentSessionId);
        }

        // Update session info button state before clearing session
        this.updateSessionInfoButton();

        // Clear current session
        this.currentSessionId = null;

        // Reset UI to no session selected state
        document.getElementById('no-session-selected').classList.remove('d-none');
        document.getElementById('chat-container').classList.add('d-none');

        // Remove active state from all session items
        document.querySelectorAll('.list-group-item').forEach(item => {
            item.classList.remove('active');
        });

        // Clear messages area
        document.getElementById('messages-area').innerHTML = '';

        // Reset processing state when exiting session
        this.hideProcessingIndicator();

        // Re-enable input controls when exiting session
        this.setInputControlsEnabled(true);
    }

    handleSendButtonClick() {
        // Always send message (queue if processing, send immediately if not)
        this.sendMessage();
    }

    async sendMessage() {
        const input = document.getElementById('message-input');
        const message = input.value.trim();

        // Allow sending while processing (for queuing)
        if (!message || !this.currentSessionId) return;

        try {
            // Disable input temporarily while submitting
            input.disabled = true;

            // Send via WebSocket if connected
            if (this.websocket && this.sessionWebsocket.readyState === WebSocket.OPEN) {
                this.sessionWebsocket.send(JSON.stringify({
                    type: 'send_message',
                    content: message
                }));
            } else {
                // Fallback to REST API
                await this.apiRequest(`/api/sessions/${this.currentSessionId}/messages`, {
                    method: 'POST',
                    body: JSON.stringify({ message })
                });
            }

            // Add user message to UI immediately
            this.addMessageToUI({
                type: 'user',
                content: message,
                timestamp: new Date().toISOString()
            });

            input.value = '';
            this.resetTextareaHeight(input);

            // Re-enable input after message is sent
            input.disabled = false;

            // Clear cached input for this session since message was sent
            if (this.currentSessionId) {
                this.sessionInputCache.delete(this.currentSessionId);
            }
        } catch (error) {
            Logger.error('MESSAGE', 'Failed to send message', error);
            // Re-enable input on error
            input.disabled = false;
        }
    }

    async handleInterruptSendClick() {
        if (!this.currentSessionId || !this.isProcessing) {
            Logger.debug('INTERRUPT', 'handleInterruptSendClick() called but conditions not met');
            return;
        }

        try {
            Logger.info('INTERRUPT', 'User clicked Interrupt & Send');

            // Disable interrupt button while processing
            const interruptSendBtn = document.getElementById('interrupt-send-btn');
            if (interruptSendBtn) {
                interruptSendBtn.disabled = true;
                interruptSendBtn.textContent = 'Interrupting...';
            }

            // Send interrupt first
            await this.sendInterrupt();

            // Wait a moment for interrupt to process
            await new Promise(resolve => setTimeout(resolve, 300));

            // Then send the queued message
            await this.sendMessage();

            Logger.info('INTERRUPT', 'Interrupt and message sent successfully');

        } catch (error) {
            Logger.error('INTERRUPT', 'Failed interrupt and send operation', error);
            this.hideProcessingIndicator();
        }
    }

    async sendInterrupt() {
        if (!this.currentSessionId || !this.isProcessing) {
            Logger.debug('INTERRUPT', 'sendInterrupt() called but conditions not met', {
                currentSessionId: this.currentSessionId,
                isProcessing: this.isProcessing
            });
            return;
        }

        try {
            Logger.info('INTERRUPT', 'Sending interrupt request for session', this.currentSessionId);
            Logger.debug('INTERRUPT', 'WebSocket state check', {
                sessionWebsocket: !!this.sessionWebsocket,
                readyState: this.sessionWebsocket?.readyState,
                OPEN: WebSocket.OPEN
            });

            // Update button to "Stopping..." state
            this.showStoppingIndicator();

            // Send interrupt via WebSocket if connected
            if (this.sessionWebsocket && this.sessionWebsocket.readyState === WebSocket.OPEN) {
                const interruptMessage = {
                    type: 'interrupt_session'
                };
                Logger.debug('INTERRUPT', 'Sending interrupt message via WebSocket', interruptMessage);
                this.sessionWebsocket.send(JSON.stringify(interruptMessage));
                Logger.debug('INTERRUPT', 'Interrupt message sent successfully');
            } else {
                Logger.warn('INTERRUPT', 'WebSocket not connected, cannot send interrupt');
                Logger.debug('INTERRUPT', 'WebSocket connection details', {
                    sessionWebsocket: !!this.sessionWebsocket,
                    readyState: this.sessionWebsocket?.readyState,
                    expectedState: WebSocket.OPEN
                });
                // Fallback: we could add a REST API endpoint for interrupt if needed
                this.hideProcessingIndicator();
            }

        } catch (error) {
            Logger.error('INTERRUPT', 'Failed to send interrupt', error);
            this.hideProcessingIndicator();
        }
    }

    showProcessingIndicator() {
        this.isProcessing = true;
        this.isInterrupting = false;

        const sendBtn = document.getElementById('send-btn');
        const interruptSendBtn = document.getElementById('interrupt-send-btn');
        const messageInput = document.getElementById('message-input');
        const progressElement = document.getElementById('claude-progress');

        if (sendBtn) {
            sendBtn.textContent = 'Queue';
            sendBtn.title = 'Queue message to send after current operation completes';
        }

        if (interruptSendBtn) {
            interruptSendBtn.classList.remove('d-none'); // Show interrupt button
        }

        if (messageInput) {
            messageInput.disabled = false; // Keep input enabled
            messageInput.placeholder = "Queue next message or interrupt...";
        }

        // Show processing indicator in status bar
        if (progressElement) {
            progressElement.classList.remove('d-none');
        }
    }

    hideProcessingIndicator() {
        this.isProcessing = false;
        this.isInterrupting = false;

        const sendBtn = document.getElementById('send-btn');
        const interruptSendBtn = document.getElementById('interrupt-send-btn');
        const messageInput = document.getElementById('message-input');
        const progressElement = document.getElementById('claude-progress');

        if (sendBtn) {
            sendBtn.textContent = 'Send';
            sendBtn.title = '';
            sendBtn.disabled = false;
        }

        if (interruptSendBtn) {
            interruptSendBtn.classList.add('d-none'); // Hide interrupt button
            interruptSendBtn.disabled = false;
        }

        if (messageInput) {
            messageInput.disabled = false;
            messageInput.placeholder = "Type your message to Claude Code...";
        }

        // Hide processing indicator
        if (progressElement) {
            progressElement.classList.add('d-none');
        }

        // Re-enable controls only if current session is not in error state
        this.updateControlsBasedOnSessionState();
    }

    showStoppingIndicator() {
        this.isInterrupting = true;
        const sendButton = document.getElementById('send-btn');

        if (sendButton) {
            sendButton.disabled = true;
            sendButton.textContent = 'Stopping...';
            sendButton.className = 'btn btn-warning'; // Change to orange/warning color for stopping
        }
    }

    updateProcessingState(isProcessing) {
        this.isProcessing = isProcessing;

        if (isProcessing) {
            this.showProcessingIndicator();
        } else {
            this.hideProcessingIndicator();
        }
    }

    updateControlsBasedOnSessionState() {
        if (!this.currentSessionId) {
            // No session selected, enable controls
            this.setInputControlsEnabled(true);
            return;
        }

        const session = this.sessions.get(this.currentSessionId);
        if (session && session.state === 'error') {
            // Session is in error state, keep controls disabled
            this.setInputControlsEnabled(false);
        } else {
            // Session is not in error state, enable controls
            this.setInputControlsEnabled(true);
        }
    }

    // Frontend processing detection methods removed - now using backend state propagation

    shouldDisplayMessage(message) {
        // Get subtype from metadata or root level for backward compatibility
        const subtype = message.subtype || message.metadata?.subtype;

        // === SYSTEM MESSAGE FILTERING ===
        if (message.type === 'system') {
            // Filter out init messages (internal SDK initialization, not user-visible)
            if (subtype === 'init') {
                return false;
            }

            // Allow user-relevant system messages
            if (subtype === 'client_launched' || subtype === 'interrupt') {
                return true;
            }

            // Allow other system messages (errors, status, etc.)
            return true;
        }

        // === INFRASTRUCTURE MESSAGE FILTERING ===
        // Filter out result messages (internal completion markers)
        if (message.type === 'result') {
            return false;
        }

        // Filter out permission messages (handled by tool call UI)
        if (message.type === 'permission_request' || message.type === 'permission_response') {
            return false;
        }

        // === TOOL-RELATED MESSAGE FILTERING ===
        // Filter out assistant messages with tool uses (replaced by tool call cards)
        if (message.type === 'assistant' && this._hasToolUses(message)) {
            return false;
        }

        // Filter out user messages with tool results (replaced by tool call cards)
        if (message.type === 'user' && this._hasToolResults(message)) {
            return false;
        }

        // === USER MESSAGE FILTERING ===
        if (message.type === 'user') {
            // Filter out interrupt messages (shown as system messages instead)
            if (message.content === '[Request interrupted by user]' ||
                message.content === '[Request interrupted by user for tool use]' ||
                subtype === 'interrupt') {
                return false;
            }

            // Keep local command responses visible (they'll be rendered as system messages)
            if (message.metadata?.is_local_command_response || subtype === 'local_command_response') {
                return true;
            }
        }

        // === DEFAULT ===
        // Display all other message types (regular user/assistant messages, etc.)
        return true;
    }

    /**
     * Check if message has tool uses using metadata
     */
    _hasToolUses(message) {
        return message.metadata &&
               message.metadata.has_tool_uses === true &&
               message.metadata.tool_uses &&
               Array.isArray(message.metadata.tool_uses) &&
               message.metadata.tool_uses.length > 0;
    }


    /**
     * Check if message has tool results using metadata
     */
    _hasToolResults(message) {
        return message.metadata &&
               message.metadata.has_tool_results === true &&
               message.metadata.tool_results &&
               Array.isArray(message.metadata.tool_results) &&
               message.metadata.tool_results.length > 0;
    }

    /**
     * Unified message processing function for both real-time and historical messages
     * @param {Object} message - The message to process
     * @param {string} source - Source of message: 'websocket' or 'historical'
     */
    processMessage(message, source = 'websocket') {
        Logger.debug('MESSAGE', `Processing message from ${source}`, message);

        // Handle progress indicator for init messages (real-time only)
        const subtype = message.subtype || message.metadata?.subtype;
        if (source === 'websocket' && message.type === 'system' && subtype === 'init') {
            Logger.info('SESSION_INFO', 'Init message received', message);

            if (!this.isProcessing) {
                this.showProcessingIndicator();
            }

            // Extract and store permission mode from init message
            this.extractPermissionMode(message);

            // Store init data for session info modal
            const initData = message.metadata?.init_data;

            if (initData) {
                this.sessionInitData.set(this.currentSessionId, initData);
                Logger.info('SESSION_INFO', 'Stored init data for session', this.currentSessionId);

                // Enable session info button
                this.updateSessionInfoButton();
            } else {
                Logger.warn('SESSION_INFO', 'Init message received but no init_data in metadata', message);
            }
        }

        // Check if this is a tool-related message and handle it
        const toolHandled = this.handleToolRelatedMessage(message, source);

        // Check if this is a thinking block message and handle it
        const thinkingHandled = this.handleThinkingBlockMessage(message);


        // If it's a tool-related or thinking block message, don't show it in the regular message flow
        if (toolHandled || thinkingHandled) {
            return { handled: true, displayed: false };
        }

        // Use the unified filtering logic to determine if message should be displayed
        if (this.shouldDisplayMessage(message)) {
            Logger.debug('MESSAGE', `Adding ${source} message to UI`, message.type);
            this.addMessageToUI(message, source !== 'historical');
            return { handled: true, displayed: true };
        }

        return { handled: true, displayed: false };
    }

    handleIncomingMessage(message) {
        // Use unified processing for real-time messages
        this.processMessage(message, 'websocket');
    }

    /**
     * Extract permission mode from system init messages
     */
    extractPermissionMode(message) {
        try {
            // Try to get permission mode from metadata or raw SDK message
            let permissionMode = null;

            // Check metadata first
            if (message.metadata && message.metadata.raw_sdk_message) {
                const rawMsg = message.metadata.raw_sdk_message;

                // Parse if it's a string
                if (typeof rawMsg === 'string') {
                    try {
                        const parsed = JSON.parse(rawMsg);
                        if (parsed.data && parsed.data.permissionMode) {
                            permissionMode = parsed.data.permissionMode;
                        }
                    } catch (e) {
                        // Try to extract from string representation
                        const match = rawMsg.match(/'permissionMode':\s*'([^']+)'/);
                        if (match) {
                            permissionMode = match[1];
                        }
                    }
                } else if (typeof rawMsg === 'object' && rawMsg.data && rawMsg.data.permissionMode) {
                    permissionMode = rawMsg.data.permissionMode;
                }
            }

            if (permissionMode && permissionMode !== this.currentPermissionMode) {
                Logger.info('PERMISSION', 'Permission mode changed', {from: this.currentPermissionMode, to: permissionMode});
                this.currentPermissionMode = permissionMode;
                this.updatePermissionModeUI(permissionMode);
            }
        } catch (error) {
            Logger.error('PERMISSION', 'Error extracting permission mode', error);
        }
    }

    /**
     * Update UI to reflect current permission mode
     */
    updatePermissionModeUI(mode) {
        Logger.debug('PERMISSION', 'Updating UI - Current permission mode', mode);

        // Update permission mode text in the status bar
        const permissionModeText = document.getElementById('permission-mode-text');
        const permissionModeIcon = document.getElementById('permission-mode-icon');
        const permissionModeClickable = document.getElementById('permission-mode-clickable');

        if (permissionModeText) {
            permissionModeText.textContent = `Mode: ${mode}`;
        }

        // Update icon and title based on mode
        if (permissionModeIcon && permissionModeClickable) {
            const modeConfig = {
                'default': {
                    icon: '🔒',
                    title: 'Click to cycle modes • Requires approval for most tools'
                },
                'acceptEdits': {
                    icon: '✅',
                    title: 'Click to cycle modes • Auto-approves Read, Edit, Write, MultiEdit'
                },
                'plan': {
                    icon: '📋',
                    title: 'Click to cycle modes • Planning mode - must approve plan to proceed'
                }
            };

            const config = modeConfig[mode] || modeConfig['default'];
            permissionModeIcon.textContent = config.icon;
            permissionModeClickable.title = config.title;
        }
    }

    updateSessionInfoButton() {
        const sessionInfoBtn = document.getElementById('session-info-btn');
        if (!sessionInfoBtn) return;

        if (this.currentSessionId && this.sessionInitData.has(this.currentSessionId)) {
            sessionInfoBtn.disabled = false;
            sessionInfoBtn.title = 'View session configuration';
        } else {
            sessionInfoBtn.disabled = true;
            sessionInfoBtn.title = 'Session configuration not yet available (waiting for init message)';
        }
    }

    showSessionInfoModal() {
        if (!this.currentSessionId) return;

        const initData = this.sessionInitData.get(this.currentSessionId);
        if (!initData) {
            Logger.warn('SESSION_INFO', 'No init data available for session', this.currentSessionId);
            return;
        }

        try {
            // Populate session ID
            const sessionIdEl = document.getElementById('session-info-session-id');
            if (sessionIdEl) {
                sessionIdEl.textContent = initData.session_id || this.currentSessionId;
            }

            // Populate model
            const modelEl = document.getElementById('session-info-model');
            if (modelEl) {
                modelEl.textContent = initData.model || 'Default';
            }

            // Populate tools (render as badges)
            const toolsEl = document.getElementById('session-info-tools');
            if (toolsEl && initData.tools) {
                const toolsHtml = initData.tools.map(tool =>
                    `<span class="badge bg-primary me-1 mb-1">${this.escapeHtml(tool)}</span>`
                ).join('');
                toolsEl.innerHTML = toolsHtml || '<span class="text-muted">No tools configured</span>';
            }

            // Populate commands (render as list)
            const commandsEl = document.getElementById('session-info-commands');
            if (commandsEl && initData.commands) {
                const commandsHtml = Object.entries(initData.commands).map(([name, desc]) =>
                    `<div class="mb-1"><code>/${name}</code> - ${this.escapeHtml(desc)}</div>`
                ).join('');
                commandsEl.innerHTML = commandsHtml || '<span class="text-muted">No commands configured</span>';
            }

            // Populate settings (pretty-print JSON)
            const settingsEl = document.getElementById('session-info-settings');
            if (settingsEl) {
                // Filter out tools and commands since they're displayed separately
                const settingsToDisplay = { ...initData };
                delete settingsToDisplay.tools;
                delete settingsToDisplay.commands;
                delete settingsToDisplay.session_id;
                delete settingsToDisplay.model;

                settingsEl.textContent = JSON.stringify(settingsToDisplay, null, 2);
            }

            // Show modal
            const modal = new bootstrap.Modal(document.getElementById('session-info-modal'));
            modal.show();

            Logger.info('SESSION_INFO', 'Displayed session info modal');
        } catch (error) {
            Logger.error('SESSION_INFO', 'Failed to display session info modal', error);
        }
    }

    handleToolRelatedMessage(message, source = 'websocket') {
        try {
            // Handle assistant messages with tool use blocks
            if (message.type === 'assistant' && message.metadata && message.metadata.tool_uses && Array.isArray(message.metadata.tool_uses)) {
                const toolUses = message.metadata.tool_uses;
                let hasToolUse = false;

                toolUses.forEach(toolUse => {
                    hasToolUse = true;
                    const toolUseBlock = {
                        id: toolUse.id,
                        name: toolUse.name,
                        input: toolUse.input
                    };

                    const toolCall = this.toolCallManager.handleToolUse(toolUseBlock);
                    this.renderToolCall(toolCall);
                });

                return hasToolUse;
            }

            // Handle permission requests
            if (message.type === 'permission_request') {
                const permissionRequest = {
                    request_id: message.request_id || message.metadata?.request_id,
                    tool_name: message.tool_name || message.metadata?.tool_name,
                    input_params: message.input_params || message.metadata?.input_params,
                    metadata: message.metadata,  // Pass full metadata including suggestions
                    suggestions: message.metadata?.suggestions || message.suggestions  // Also pass directly for convenience
                };

                const toolCall = this.toolCallManager.handlePermissionRequest(permissionRequest);
                if (toolCall) {
                    this.updateToolCall(toolCall);
                }
                return true;
            }

            // Handle permission responses
            if (message.type === 'permission_response') {
                const permissionResponse = {
                    request_id: message.request_id || message.metadata?.request_id,
                    decision: message.decision || message.metadata?.decision,
                    reasoning: message.reasoning || message.metadata?.reasoning,
                    applied_updates: message.applied_updates || message.metadata?.applied_updates || []
                };

                const toolCall = this.toolCallManager.handlePermissionResponse(permissionResponse);
                if (toolCall) {
                    this.updateToolCall(toolCall);
                }
                return true;
            }

            // Handle tool result blocks (in user messages)
            if (message.type === 'user' && message.metadata && message.metadata.tool_results && Array.isArray(message.metadata.tool_results)) {
                const toolResults = message.metadata.tool_results;
                let hasToolResult = false;

                toolResults.forEach(toolResult => {
                    hasToolResult = true;
                    const toolResultBlock = {
                        tool_use_id: toolResult.tool_use_id,
                        content: toolResult.content,
                        is_error: toolResult.is_error
                    };

                    const toolCall = this.toolCallManager.handleToolResult(toolResultBlock);
                    if (toolCall) {
                        this.updateToolCall(toolCall);

                        // Check if this is ExitPlanMode completing successfully (real-time only, backend handles it)
                        if (source === 'websocket' && toolCall.name === 'ExitPlanMode' && toolCall.status === 'completed' && !toolCall.result?.error) {
                            // Debug: Log the full toolCall object to see what data is available
                            Logger.debug('PERMISSION', 'ExitPlanMode completed - checking for setMode', {
                                appliedUpdates: toolCall.appliedUpdates,
                                suggestions: toolCall.suggestions,
                                permissionDecision: toolCall.permissionDecision,
                                fullToolCall: toolCall
                            });

                            // Check if a setMode suggestion was applied
                            const hadSetModeApplied = toolCall.appliedUpdates?.some(update => update.type === 'setMode');

                            if (hadSetModeApplied) {
                                // Mode already changed via suggestion - don't reset
                                Logger.info('PERMISSION', 'ExitPlanMode completed with setMode suggestion applied - mode already updated');
                            } else {
                                // No setMode applied - reset to default as before
                                Logger.info('PERMISSION', 'ExitPlanMode completed - updating permission mode to default');
                                this.setPermissionMode('default');
                            }
                        }
                    }
                });

                return hasToolResult;
            }

            return false;
        } catch (error) {
            Logger.error('TOOL_MANAGER', 'Error handling tool-related message', {error, message});
            return false;
        }
    }

    handleThinkingBlockMessage(message) {
        try {
            // Check if this is an assistant message with thinking blocks
            if (message.type === 'assistant' && message.metadata && message.metadata.thinking_blocks && Array.isArray(message.metadata.thinking_blocks)) {
                const thinkingBlocks = message.metadata.thinking_blocks;

                if (thinkingBlocks.length > 0) {
                    Logger.debug('MESSAGE', 'Processing thinking blocks', thinkingBlocks);

                    thinkingBlocks.forEach(thinkingBlock => {
                        // Generate a unique ID for this thinking block
                        const thinkingId = `thinking_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;

                        const thinkingBlockData = {
                            id: thinkingId,
                            content: thinkingBlock.content,
                            timestamp: thinkingBlock.timestamp || message.timestamp,
                            isExpanded: false // Start collapsed
                        };

                        this.renderThinkingBlock(thinkingBlockData);
                    });

                    return true; // Indicate that we handled thinking blocks
                }
            }

            return false; // No thinking blocks found
        } catch (error) {
            Logger.error('MESSAGE', 'Error handling thinking block message', {error, message});
            return false;
        }
    }

    renderToolCall(toolCall) {
        Logger.debug('UI', 'Rendering tool call', toolCall);

        const messagesArea = document.getElementById('messages-area');

        // Wrap tool call in two-column layout
        const wrapper = document.createElement('div');
        wrapper.className = 'message-row row py-1 tool-call';
        wrapper.id = `tool-call-wrapper-${toolCall.id}`;

        const timestamp = new Date().toLocaleTimeString();

        wrapper.innerHTML = `
            <div class="col-auto message-speaker text-end pe-3" title="${timestamp}">
                agent
            </div>
            <div class="col message-content-column" id="tool-call-content-${toolCall.id}">
            </div>
        `;

        // Add to DOM
        messagesArea.appendChild(wrapper);

        // Insert the actual tool call element into the content column
        const contentColumn = document.getElementById(`tool-call-content-${toolCall.id}`);
        const toolCallElement = this.createToolCallElement(toolCall);
        contentColumn.appendChild(toolCallElement);

        this.smartScrollToBottom();
    }

    updateToolCall(toolCall, scroll = true) {
        Logger.debug('UI', 'Updating tool call', toolCall);

        const existingContentColumn = document.getElementById(`tool-call-content-${toolCall.id}`);
        if (existingContentColumn) {
            // Replace the tool call element inside the content column
            const updatedElement = this.createToolCallElement(toolCall);
            existingContentColumn.innerHTML = '';
            existingContentColumn.appendChild(updatedElement);

            if (scroll) {
                this.smartScrollToBottom();
            }
        } else {
            this.renderToolCall(toolCall);
        }
    }

    createToolCallElement(toolCall) {
        const element = document.createElement('div');
        element.className = 'accordion';
        element.id = `tool-call-${toolCall.id}`;

        // Use unified accordion template
        element.innerHTML = this.createToolCallHTML(toolCall);

        // Add event delegation for permission button clicks only
        this.setupToolCallEventListeners(element, toolCall);

        return element;
    }

    createToolCallHTML(toolCall) {
        const statusClass = `tool-status-${toolCall.status}`;
        const statusIcon = {
            'pending': '🔄',
            'permission_required': '❓',
            'executing': '⚡',
            'completed': toolCall.permissionDecision === 'deny' ? '❌' : '✅',
            'error': '💥'
        }[toolCall.status] || '🔧';

        // Get handler for this tool
        const handler = this.toolHandlerRegistry.getHandler(toolCall.name) || this.defaultToolHandler;

        // Generate summary for accordion button
        let summary;
        if (handler && handler.getCollapsedSummary) {
            const customSummary = handler.getCollapsedSummary(toolCall);
            summary = customSummary !== null ? customSummary : this.toolCallManager.generateCollapsedSummary(toolCall);
        } else {
            summary = this.toolCallManager.generateCollapsedSummary(toolCall);
        }

        // Determine if accordion should be expanded
        const collapseClass = toolCall.isExpanded ? 'accordion-collapse collapse show' : 'accordion-collapse collapse';
        const buttonClass = toolCall.isExpanded ? 'accordion-button' : 'accordion-button collapsed';

        // Build accordion body content
        let bodyContent = `
            <div class="tool-call-details">
                ${handler.renderParameters(toolCall, this.escapeHtml.bind(this))}
            </div>
        `;

        // Add permission prompt if needed
        if (toolCall.status === 'permission_required') {
            const hasSuggestions = toolCall.suggestions && toolCall.suggestions.length > 0;
            const suggestion = hasSuggestions ? toolCall.suggestions[0] : null;

            bodyContent += `
                <div class="tool-permission-prompt">
                    <p><strong>🔐 Permission Required</strong></p>
                    <p>Claude wants to use the <code>${this.escapeHtml(toolCall.name)}</code> tool.</p>
            `;

            // Show suggestion if available
            if (suggestion) {
                if (suggestion.type === 'setMode') {
                    const modeLabel = this.getPermissionModeLabel(suggestion.mode);
                    bodyContent += `
                        <div class="permission-suggestion">
                            <div class="suggestion-icon">💡</div>
                            <div class="suggestion-content">
                                <strong>Suggestion:</strong> Switch to "${modeLabel}" mode for this session
                                <small class="d-block text-muted">Future ${toolCall.name} operations will be auto-approved</small>
                            </div>
                        </div>
                    `;
                } else if (suggestion.type === 'addRules' && suggestion.rules) {
                    const rulesDisplay = suggestion.rules.map(rule =>
                        `<code>${this.escapeHtml(rule.toolName || 'unknown')}${rule.ruleContent ? ': ' + this.escapeHtml(rule.ruleContent) : ''}</code>`
                    ).join(', ');
                    bodyContent += `
                        <div class="permission-suggestion">
                            <div class="suggestion-icon">💡</div>
                            <div class="suggestion-content">
                                <strong>Suggestion:</strong> Add permission rule for ${rulesDisplay}
                                <small class="d-block text-muted">This will allow similar operations in the future</small>
                            </div>
                        </div>
                    `;
                } else if (suggestion.type === 'addDirectories' && suggestion.directories) {
                    const dirsCount = suggestion.directories.length;
                    const dirsDisplay = dirsCount === 1
                        ? `<code>${this.escapeHtml(suggestion.directories[0])}</code>`
                        : `${dirsCount} directories`;
                    bodyContent += `
                        <div class="permission-suggestion">
                            <div class="suggestion-icon">💡</div>
                            <div class="suggestion-content">
                                <strong>Suggestion:</strong> Add ${dirsDisplay} to allowed directories
                                <small class="d-block text-muted">This will allow access to files in these directories</small>
                            </div>
                        </div>
                    `;
                }
            }

            bodyContent += `
                    <div class="permission-buttons">
            `;

            // Show "Approve & Apply" button if we have suggestions
            if (hasSuggestions) {
                bodyContent += `
                        <button class="btn btn-success permission-approve-with-suggestion" data-request-id="${toolCall.permissionRequestId}">
                            ✅ Approve & Apply
                        </button>
                        <button class="btn btn-outline-success permission-approve" data-request-id="${toolCall.permissionRequestId}">
                            ✅ Approve Only
                        </button>
                `;
            } else {
                // No suggestions - standard approve button
                bodyContent += `
                        <button class="btn btn-success permission-approve" data-request-id="${toolCall.permissionRequestId}">
                            ✅ Approve
                        </button>
                `;
            }

            bodyContent += `
                        <button class="btn btn-danger permission-deny" data-request-id="${toolCall.permissionRequestId}">
                            ❌ Deny
                        </button>
                    </div>
                    <div class="permission-clarification-section">
                        <textarea
                            class="permission-clarification-input"
                            id="permission-clarification-${toolCall.permissionRequestId}"
                            placeholder="Or provide guidance to redirect the agent..."
                            rows="1"
                            data-request-id="${toolCall.permissionRequestId}"></textarea>
                        <button class="btn btn-warning permission-deny-with-clarification" data-request-id="${toolCall.permissionRequestId}" disabled>
                            💬 Provide Guidance
                        </button>
                    </div>
                </div>
            `;
        }

        // Add applied permission updates if any
        if (toolCall.appliedUpdates && toolCall.appliedUpdates.length > 0 && toolCall.permissionDecision === 'allow') {
            bodyContent += `
                <div class="permission-updates-applied">
                    <div class="permission-update-badge">
                        🔒 Permission Updated
                    </div>
                    <div class="permission-update-details">
                        ${toolCall.appliedUpdates.map(update => {
                            if (update.type === 'setMode') {
                                const modeLabel = this.getPermissionModeLabel(update.mode);
                                return `<div class="permission-update-item">Mode: ${this.escapeHtml(modeLabel)} (session)</div>`;
                            } else if (update.type === 'addRules') {
                                return `<div class="permission-update-item">Added rule for ${this.escapeHtml(update.behavior || 'allow')}</div>`;
                            } else if (update.type === 'addDirectories') {
                                const dirs = update.directories || [];
                                return `<div class="permission-update-item">Added ${dirs.length} allowed ${dirs.length === 1 ? 'directory' : 'directories'}</div>`;
                            } else {
                                return `<div class="permission-update-item">${this.escapeHtml(update.type)}</div>`;
                            }
                        }).join('')}
                    </div>
                </div>
            `;
        }

        // Add result if available using handler
        if (toolCall.result) {
            bodyContent += handler.renderResult(toolCall, this.escapeHtml.bind(this));
        }

        // Add explanation if available
        if (toolCall.explanation) {
            bodyContent += `
                <div class="tool-explanation">
                    <strong>Explanation:</strong>
                    <div class="tool-explanation-content">${this.escapeHtml(toolCall.explanation)}</div>
                </div>
            `;
        }

        // Return Bootstrap accordion structure
        return `
            <div class="accordion-item ${statusClass}">
                <h2 class="accordion-header">
                    <button class="${buttonClass}" type="button" data-bs-toggle="collapse" data-bs-target="#collapse-${toolCall.id}" aria-expanded="${toolCall.isExpanded}" aria-controls="collapse-${toolCall.id}">
                        ${this.escapeHtml(summary)}
                    </button>
                </h2>
                <div id="collapse-${toolCall.id}" class="${collapseClass}">
                    <div class="accordion-body">
                        ${bodyContent}
                    </div>
                </div>
            </div>
        `;
    }

    setupToolCallEventListeners(element, toolCall) {
        // Listen to Bootstrap collapse events to keep state in sync
        const collapseElement = element.querySelector(`#collapse-${toolCall.id}`);
        if (collapseElement) {
            collapseElement.addEventListener('shown.bs.collapse', () => {
                // Update internal state when accordion expands
                this.toolCallManager.setToolExpansion(toolCall.id, true);
            });

            collapseElement.addEventListener('hidden.bs.collapse', () => {
                // Update internal state when accordion collapses
                this.toolCallManager.setToolExpansion(toolCall.id, false);
            });
        }

        // Handle permission button clicks
        const approveBtn = element.querySelector('.permission-approve');
        const approveWithSuggestionBtn = element.querySelector('.permission-approve-with-suggestion');
        const denyBtn = element.querySelector('.permission-deny');
        const clarificationTextarea = element.querySelector('.permission-clarification-input');
        const denyWithClarificationBtn = element.querySelector('.permission-deny-with-clarification');

        if (approveBtn) {
            approveBtn.addEventListener('click', () => {
                // Check if this specific button is already disabled (prevents double-click)
                if (approveBtn.disabled) return;

                // Disable all buttons immediately to prevent duplicate clicks
                const allButtons = element.querySelectorAll('.permission-buttons button, .permission-deny-with-clarification');
                allButtons.forEach(btn => btn.disabled = true);
                if (clarificationTextarea) clarificationTextarea.disabled = true;

                // Update button text to show submission
                approveBtn.textContent = '⏳ Submitting...';

                const requestId = approveBtn.getAttribute('data-request-id');
                this.handlePermissionDecision(requestId, 'allow', false, null, allButtons);
            });
        }

        if (approveWithSuggestionBtn) {
            approveWithSuggestionBtn.addEventListener('click', () => {
                // Check if this specific button is already disabled (prevents double-click)
                if (approveWithSuggestionBtn.disabled) return;

                // Disable all buttons immediately to prevent duplicate clicks
                const allButtons = element.querySelectorAll('.permission-buttons button, .permission-deny-with-clarification');
                allButtons.forEach(btn => btn.disabled = true);
                if (clarificationTextarea) clarificationTextarea.disabled = true;

                // Update button text to show submission
                approveWithSuggestionBtn.textContent = '⏳ Submitting...';

                const requestId = approveWithSuggestionBtn.getAttribute('data-request-id');
                this.handlePermissionDecision(requestId, 'allow', true, null, allButtons);
            });
        }

        if (denyBtn) {
            denyBtn.addEventListener('click', () => {
                // Check if this specific button is already disabled (prevents double-click)
                if (denyBtn.disabled) return;

                // Disable all buttons immediately to prevent duplicate clicks
                const allButtons = element.querySelectorAll('.permission-buttons button, .permission-deny-with-clarification');
                allButtons.forEach(btn => btn.disabled = true);
                if (clarificationTextarea) clarificationTextarea.disabled = true;

                // Update button text to show submission
                denyBtn.textContent = '⏳ Submitting...';

                const requestId = denyBtn.getAttribute('data-request-id');
                this.handlePermissionDecision(requestId, 'deny', false, null, allButtons);
            });
        }

        // Handle clarification textarea input
        if (clarificationTextarea && denyWithClarificationBtn) {
            // Auto-expand textarea as content is added
            const autoExpand = () => {
                // Reset height to auto to get the correct scrollHeight
                clarificationTextarea.style.height = 'auto';
                // Set height to scrollHeight (content height) but respect max-height from CSS
                const maxHeight = 144; // 9rem = 144px (approximately)
                clarificationTextarea.style.height = Math.min(clarificationTextarea.scrollHeight, maxHeight) + 'px';
            };

            clarificationTextarea.addEventListener('input', () => {
                const hasContent = clarificationTextarea.value.trim().length > 0;
                denyWithClarificationBtn.disabled = !hasContent;
                autoExpand();
            });

            // Handle Enter key to submit, Shift+Enter for newline
            clarificationTextarea.addEventListener('keydown', (e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault();
                    const hasContent = clarificationTextarea.value.trim().length > 0;
                    if (hasContent && !denyWithClarificationBtn.disabled) {
                        denyWithClarificationBtn.click();
                    }
                }
            });

            denyWithClarificationBtn.addEventListener('click', () => {
                const clarificationMessage = clarificationTextarea.value.trim();
                if (!clarificationMessage) return;

                // Disable all buttons immediately to prevent duplicate clicks
                const allButtons = element.querySelectorAll('.permission-buttons button, .permission-deny-with-clarification');
                if (Array.from(allButtons).some(btn => btn.disabled)) return;
                allButtons.forEach(btn => btn.disabled = true);
                clarificationTextarea.disabled = true;

                // Update button text to show submission
                denyWithClarificationBtn.textContent = '⏳ Submitting...';

                const requestId = denyWithClarificationBtn.getAttribute('data-request-id');
                this.handlePermissionDecision(requestId, 'deny', false, clarificationMessage, allButtons);
            });
        }
    }

    toggleToolCallExpansion(toolUseId) {
        // Bootstrap accordion handles the UI toggle via data-bs-toggle
        // Just update the internal state
        this.toolCallManager.toggleToolExpansion(toolUseId);
        // No need to call updateToolCall - Bootstrap handles the DOM changes
    }

    handlePermissionDecision(requestId, decision, applySuggestions, clarificationMessage, allButtons) {
        Logger.info('PERMISSION', 'Permission decision', {requestId, decision, applySuggestions, clarificationMessage});

        // Send permission response to backend
        if (this.sessionWebsocket && this.sessionWebsocket.readyState === WebSocket.OPEN) {
            const response = {
                type: 'permission_response',
                request_id: requestId,
                decision: decision,
                apply_suggestions: applySuggestions,
                timestamp: new Date().toISOString()
            };

            // Add clarification message if provided
            if (clarificationMessage) {
                response.clarification_message = clarificationMessage;
            }

            this.sessionWebsocket.send(JSON.stringify(response));

            // Immediately update the toolCall locally (don't wait for backend response)
            // Get the toolCall from the permission request
            const toolCall = this.toolCallManager.getToolCallByPermissionRequest(requestId);
            if (toolCall) {
                // Build permission response object to update the toolCall
                const permissionResponse = {
                    request_id: requestId,
                    decision: decision,
                    reasoning: decision === 'allow' ? 'User allowed permission' : 'User denied permission',
                    applied_updates: applySuggestions && toolCall.suggestions ? toolCall.suggestions : []
                };

                // Update the toolCall with the permission decision
                this.toolCallManager.handlePermissionResponse(permissionResponse);
                this.updateToolCall(toolCall);
            }

            // Update button to show submitted state
            allButtons.forEach(btn => {
                if (!btn.disabled) return;
                if (btn.classList.contains('permission-approve') || btn.classList.contains('permission-approve-with-suggestion')) {
                    btn.textContent = applySuggestions ? '✅ Approved & Applied' : '✅ Approved';
                    btn.classList.add('submitted');
                } else if (btn.classList.contains('permission-deny')) {
                    btn.textContent = '❌ Denied';
                    btn.classList.add('submitted');
                } else if (btn.classList.contains('permission-deny-with-clarification')) {
                    btn.textContent = '💬 Guidance Provided';
                    btn.classList.add('submitted');
                }
            });
        } else {
            // Re-enable buttons if WebSocket is not available
            Logger.error('PERMISSION', 'Cannot send permission response: WebSocket not connected');
            allButtons.forEach(btn => {
                btn.disabled = false;
                // Reset button text
                if (btn.classList.contains('permission-approve-with-suggestion')) {
                    btn.textContent = '✅ Approve & Apply';
                } else if (btn.classList.contains('permission-approve')) {
                    btn.textContent = btn.classList.contains('btn-outline-success') ? '✅ Approve Only' : '✅ Approve';
                } else if (btn.classList.contains('permission-deny')) {
                    btn.textContent = '❌ Deny';
                } else if (btn.classList.contains('permission-deny-with-clarification')) {
                    btn.textContent = '💬 Provide Guidance';
                }
            });
        }
    }

    getPermissionModeLabel(mode) {
        const labels = {
            'default': 'Default',
            'acceptEdits': 'Accept Edits',
            'plan': 'Plan',
            'bypassPermissions': 'Bypass Permissions'
        };
        return labels[mode] || mode;
    }

    renderThinkingBlock(thinkingBlock) {
        Logger.debug('UI', 'Rendering thinking block', thinkingBlock);

        const messagesArea = document.getElementById('messages-area');

        // Wrap thinking block in two-column layout (like tool calls)
        const wrapper = document.createElement('div');
        wrapper.className = 'message-row row py-1 thinking-block';
        wrapper.id = `thinking-block-wrapper-${thinkingBlock.id}`;

        const timestamp = thinkingBlock.timestamp
            ? new Date(thinkingBlock.timestamp).toLocaleTimeString()
            : new Date().toLocaleTimeString();

        wrapper.innerHTML = `
            <div class="col-auto message-speaker text-end pe-3" title="${timestamp}">
                agent
            </div>
            <div class="col message-content-column" id="thinking-block-content-${thinkingBlock.id}">
            </div>
        `;

        messagesArea.appendChild(wrapper);

        const contentColumn = document.getElementById(`thinking-block-content-${thinkingBlock.id}`);
        const thinkingElement = this.createThinkingBlockElement(thinkingBlock);
        contentColumn.appendChild(thinkingElement);

        this.smartScrollToBottom();
    }

    createThinkingBlockElement(thinkingBlock) {
        const element = document.createElement('div');
        element.className = 'accordion';
        element.id = `thinking-block-${thinkingBlock.id}`;

        const firstLine = thinkingBlock.content.split('\n')[0] || 'Claude was thinking...';

        const collapseClass = thinkingBlock.isExpanded
            ? 'accordion-collapse collapse show'
            : 'accordion-collapse collapse';
        const buttonClass = thinkingBlock.isExpanded
            ? 'accordion-button'
            : 'accordion-button collapsed';

        element.innerHTML = `
            <div class="accordion-item">
                <h2 class="accordion-header">
                    <button class="${buttonClass} thinking-accordion-button" type="button"
                            data-bs-toggle="collapse"
                            data-bs-target="#thinking-collapse-${thinkingBlock.id}">
                        <span class="thinking-summary-text">🧠 Thinking: ${this.escapeHtml(firstLine)}</span>
                    </button>
                </h2>
                <div id="thinking-collapse-${thinkingBlock.id}" class="${collapseClass}">
                    <div class="accordion-body">
                        <div class="thinking-content">
                            <pre class="thinking-text">${this.escapeHtml(thinkingBlock.content)}</pre>
                        </div>
                    </div>
                </div>
            </div>
        `;

        return element;
    }

    async loadSessionInfo() {
        if (!this.currentSessionId) return;

        // Skip if this session is being deleted
        if (this.deletingSessions.has(this.currentSessionId)) {
            Logger.debug('SESSION', 'Skipping loadSessionInfo - deletion in progress', this.currentSessionId);
            return;
        }

        // Skip if session no longer exists in our local map
        if (!this.sessions.has(this.currentSessionId)) {
            Logger.debug('SESSION', 'Skipping loadSessionInfo - not in local sessions map', this.currentSessionId);
            return;
        }

        try {
            const data = await this.apiRequest(`/api/sessions/${this.currentSessionId}`);
            this.updateSessionInfo(data);
        } catch (error) {
            // If it's a 404, the session was likely deleted - handle gracefully
            if (error.message.includes('404')) {
                Logger.info('SESSION', 'Session not found (404) - likely deleted, clearing from UI', this.currentSessionId);
                this.handleSessionDeleted(this.currentSessionId);
            } else {
                Logger.error('SESSION', 'Failed to load session info', error);
            }
        }
    }

    async setPermissionMode(mode) {
        if (!this.currentSessionId) {
            Logger.error('PERMISSION', 'Cannot set permission mode: no active session');
            return;
        }

        try {
            Logger.info('PERMISSION', 'Setting permission mode', mode);

            // Update local state immediately for responsive UI
            this.currentPermissionMode = mode;
            this.updatePermissionModeUI(mode);

            // Persist to backend
            const response = await this.apiRequest(`/api/sessions/${this.currentSessionId}/permission-mode`, {
                method: 'POST',
                body: JSON.stringify({ mode: mode })
            });

            if (response.success) {
                Logger.info('PERMISSION', 'Permission mode successfully set', response.mode);
            } else {
                Logger.error('PERMISSION', 'Failed to set permission mode on backend');
            }
        } catch (error) {
            Logger.error('PERMISSION', 'Error setting permission mode', error);
            // Optionally revert UI if backend call fails
            // this.extractPermissionMode(lastKnownState);
        }
    }

    flashModeButton() {
        const modeButton = document.getElementById('permission-mode-clickable');
        if (!modeButton) {
            Logger.warn('UI', 'Mode button not found for flash animation');
            return;
        }

        // Remove existing animation class if present (allows re-triggering)
        modeButton.classList.remove('mode-change-flash');

        // Force reflow to restart animation
        void modeButton.offsetWidth;

        // Add animation class
        modeButton.classList.add('mode-change-flash');

        // Remove class after animation completes
        setTimeout(() => {
            modeButton.classList.remove('mode-change-flash');
        }, 1800); // 3 iterations × 600ms
    }

    async loadMessages() {
        if (!this.currentSessionId) return;

        try {
            Logger.debug('MESSAGE', 'Loading all messages with pagination');
            const allMessages = [];
            let offset = 0;
            const pageSize = 50;
            let hasMore = true;

            // Load all messages using pagination
            while (hasMore) {
                Logger.debug('MESSAGE', 'Loading messages page', {offset, limit: pageSize});
                const response = await this.apiRequest(
                    `/api/sessions/${this.currentSessionId}/messages?limit=${pageSize}&offset=${offset}`
                );

                // Add messages from this page
                allMessages.push(...response.messages);

                // Check if there are more pages
                hasMore = response.has_more;
                offset += pageSize;

                Logger.debug('MESSAGE', 'Loaded messages page', {loaded: response.messages.length, total: allMessages.length, hasMore});
            }

            Logger.debug('MESSAGE', 'Finished loading all messages', {total: allMessages.length});

            // Check for init messages in historical data
            const initMessage = allMessages.find(m => m.type === 'system' && (m.subtype === 'init' || m.metadata?.subtype === 'init'));
            if (initMessage?.metadata?.init_data) {
                this.sessionInitData.set(this.currentSessionId, initMessage.metadata.init_data);
                Logger.debug('SESSION_INFO', 'Loaded init data from historical messages', this.currentSessionId);
                this.updateSessionInfoButton();
            }

            this.renderMessages(allMessages);
        } catch (error) {
            Logger.error('MESSAGE', 'Failed to load messages', error);
        }
    }

    // UI WebSocket Management (for session state updates)
    connectUIWebSocket() {
        if (this.uiWebsocket && this.uiWebsocket.readyState === WebSocket.OPEN) {
            Logger.debug('WS_UI', 'UI WebSocket already connected');
            return;
        }

        Logger.info('WS_UI', 'Connecting to UI WebSocket');
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${protocol}//${window.location.host}/ws/ui`;

        this.uiWebsocket = new WebSocket(wsUrl);

        this.uiWebsocket.onopen = () => {
            Logger.info('WS_UI', 'UI WebSocket connected successfully');
            this.uiConnectionRetryCount = 0;
        };

        this.uiWebsocket.onmessage = (event) => {
            try {
                const data = JSON.parse(event.data);
                this.handleUIWebSocketMessage(data);
            } catch (error) {
                Logger.error('WS_UI', 'Error parsing UI WebSocket message', error);
            }
        };

        this.uiWebsocket.onclose = (event) => {
            Logger.info('WS_UI', 'UI WebSocket disconnected', {code: event.code, reason: event.reason});
            this.uiWebsocket = null;

            // Auto-reconnect UI WebSocket (it should always stay connected)
            if (this.uiConnectionRetryCount < this.maxUIRetries) {
                this.uiConnectionRetryCount++;
                const delay = Math.min(1000 * Math.pow(2, this.uiConnectionRetryCount), 30000);
                Logger.info('WS_UI', 'Reconnecting UI WebSocket', {delay, attempt: this.uiConnectionRetryCount, max: this.maxUIRetries});

                setTimeout(() => {
                    this.connectUIWebSocket();
                }, delay);
            } else {
                Logger.warn('WS_UI', 'Max UI WebSocket reconnection attempts reached');
            }
        };

        this.uiWebsocket.onerror = (error) => {
            Logger.error('WS_UI', 'UI WebSocket error', error);
        };
    }

    handleUIWebSocketMessage(data) {
        Logger.debug('WS_UI', 'UI WebSocket message received', data.type);

        switch (data.type) {
            case 'sessions_list':
                // Initial sessions list on connection
                this.updateSessionsList(data.data.sessions);
                break;
            case 'state_change':
                // Real-time session state change
                this.handleStateChange(data.data);
                break;
            case 'project_updated':
                // Project was updated (name, expansion state, etc.)
                this.handleProjectUpdated(data.data);
                break;
            case 'project_deleted':
                // Project was deleted
                this.handleProjectDeleted(data.data);
                break;
            case 'ping':
                // Respond to server ping
                if (this.uiWebsocket && this.uiWebsocket.readyState === WebSocket.OPEN) {
                    this.uiWebsocket.send(JSON.stringify({type: 'pong', timestamp: new Date().toISOString()}));
                }
                break;
            case 'pong':
                // Server responded to our ping
                Logger.debug('WS_UI', 'UI WebSocket pong received');
                break;
            default:
                Logger.warn('WS_UI', 'Unknown UI WebSocket message type', data.type);
        }
    }

    updateSessionsList(sessions) {
        Logger.debug('SESSION', 'Updating sessions list', {count: sessions.length});
        this.sessions.clear();
        // Store sessions in order received from backend (which is sorted by order field)
        this.orderedSessions = [];
        sessions.forEach(session => {
            this.sessions.set(session.session_id, session);
            this.orderedSessions.push(session);
        });
        this.renderSessions();
    }

    handleProjectUpdated(data) {
        Logger.debug('PROJECT', 'Project updated via WebSocket', data);
        const project = data.project;

        // Update local project cache
        const existingProject = this.projectManager.projects.get(project.project_id);

        if (existingProject) {
            // Check if this is a new session being added
            const hadSessions = existingProject.session_ids ? existingProject.session_ids.length : 0;
            const hasSessions = project.session_ids ? project.session_ids.length : 0;
            const isSessionAdded = hasSessions > hadSessions;

            // Update cache
            this.projectManager.projects.set(project.project_id, project);

            // Update ordered list if needed
            if (!this.projectManager.orderedProjects.includes(project.project_id)) {
                this.projectManager.orderedProjects.push(project.project_id);
            }

            // GRANULAR UPDATE: Only update what changed
            if (isSessionAdded) {
                // A session was added - we'll get the session data via state_change
                // Just update the status line here
                this.updateProjectStatusLine(project.project_id);
            } else {
                // Other metadata changed (name, expansion, etc.)
                this.updateProjectInDOM(project.project_id, 'metadata-changed');
            }
        } else {
            // New project - need full render
            this.projectManager.projects.set(project.project_id, project);
            if (!this.projectManager.orderedProjects.includes(project.project_id)) {
                this.projectManager.orderedProjects.push(project.project_id);
            }
            this.renderSessions();
        }
    }

    handleProjectDeleted(data) {
        Logger.debug('PROJECT', 'Project deleted via WebSocket', data);
        const projectId = data.project_id;

        // Remove from local cache
        this.projectManager.projects.delete(projectId);
        this.projectManager.orderedProjects = this.projectManager.orderedProjects.filter(id => id !== projectId);

        // If any sessions from this project are loaded, clear them
        // Re-render to remove from UI
        this.renderSessions();
    }

    async refreshSessions() {
        Logger.debug('SESSION', 'Refreshing sessions via API fallback');
        // Fallback to API call if UI WebSocket is not available
        await this.loadSessions();
    }

    // Session WebSocket Management (for message streaming)
    connectSessionWebSocket() {
        if (!this.currentSessionId) return;

        // If there's already a connection in CONNECTING or OPEN state, don't create another
        if (this.sessionWebsocket && (this.sessionWebsocket.readyState === WebSocket.CONNECTING || this.sessionWebsocket.readyState === WebSocket.OPEN)) {
            Logger.debug('WS_SESSION', 'WebSocket already exists in state:', this.sessionWebsocket.readyState);
            return;
        }

        // Reset intentional disconnect flag for new connections
        this.intentionalSessionDisconnect = false;

        Logger.info('WS_SESSION', 'Connecting session WebSocket', this.currentSessionId);
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${protocol}//${window.location.host}/ws/session/${this.currentSessionId}`;

        try {
            this.sessionWebsocket = new WebSocket(wsUrl);

            this.sessionWebsocket.onopen = () => {
                Logger.info('WS_SESSION', 'WebSocket connected');
                this.updateConnectionStatus('connected');
                this.sessionConnectionRetryCount = 0;
            };

            this.sessionWebsocket.onmessage = (event) => {
                try {
                    const data = JSON.parse(event.data);
                    this.handleWebSocketMessage(data);
                } catch (error) {
                    Logger.error('WS_SESSION', 'Failed to parse WebSocket message', error);
                }
            };

            this.sessionWebsocket.onclose = (event) => {
                Logger.info('WS_SESSION', 'WebSocket disconnected', {code: event.code, reason: event.reason});
                this.updateConnectionStatus('disconnected');

                // Don't retry if this was an intentional disconnect
                if (this.intentionalSessionDisconnect) {
                    Logger.debug('WS_SESSION', 'WebSocket closed intentionally, not retrying');
                    return;
                }

                // Don't retry on specific error codes (session invalid/inactive)
                if (event.code === 4404 || event.code === 4003 || event.code === 4500) {
                    Logger.info('WS_SESSION', 'WebSocket closed with error code, not retrying', event.code);
                    return;
                }

                this.scheduleReconnect();
            };

            this.sessionWebsocket.onerror = (error) => {
                Logger.error('WS_SESSION', 'WebSocket error', error);
                this.updateConnectionStatus('disconnected');
            };

        } catch (error) {
            Logger.error('WS_SESSION', 'Failed to create WebSocket', error);
            this.updateConnectionStatus('disconnected');
        }
    }

    disconnectSessionWebSocket() {
        if (this.sessionWebsocket) {
            // Mark as intentional disconnect to prevent reconnection
            this.intentionalSessionDisconnect = true;
            this.sessionWebsocket.close();
            this.sessionWebsocket = null;
        }
        this.updateConnectionStatus('disconnected');
    }

    scheduleReconnect() {
        // Don't reconnect if this was an intentional disconnect
        if (this.intentionalSessionDisconnect) {
            Logger.debug('WS_SESSION', 'Reconnect cancelled due to intentional disconnect');
            return;
        }

        if (this.sessionConnectionRetryCount < this.maxSessionRetries) {
            this.sessionConnectionRetryCount++;
            const delay = Math.min(1000 * Math.pow(2, this.sessionConnectionRetryCount), 30000);

            Logger.info('WS_SESSION', 'Scheduling WebSocket reconnect', {delay, attempt: this.sessionConnectionRetryCount});
            setTimeout(() => {
                if (this.currentSessionId && !this.intentionalSessionDisconnect) {
                    this.connectSessionWebSocket();
                }
            }, delay);
        } else {
            Logger.warn('WS_SESSION', 'Max reconnection attempts reached');
        }
    }

    handleWebSocketMessage(data) {
        Logger.debug('WS_SESSION', 'WebSocket message received', data);

        switch (data.type) {
            case 'message':
                this.handleIncomingMessage(data.data);
                break;
            case 'state_change':
                this.handleStateChange(data.data);
                break;
            case 'connection_established':
                Logger.info('WS_SESSION', 'WebSocket connection confirmed for session', data.session_id);
                break;
            case 'ping':
                // Respond to server ping to keep connection alive
                if (this.websocket && this.sessionWebsocket.readyState === WebSocket.OPEN) {
                    this.sessionWebsocket.send(JSON.stringify({type: 'pong', timestamp: new Date().toISOString()}));
                }
                break;
            case 'interrupt_response':
                this.handleInterruptResponse(data);
                break;
            default:
                Logger.warn('WS_SESSION', 'Unknown WebSocket message type', data.type);
        }
    }

    handleInterruptResponse(data) {
        Logger.info('INTERRUPT', 'Interrupt response received', data);

        if (data.success) {
            Logger.info('INTERRUPT', 'Interrupt successful', data.message);
            // Interrupt was successful, hide processing indicators
            this.hideProcessingIndicator();
        } else {
            Logger.warn('INTERRUPT', 'Interrupt failed', data.message);
            // Interrupt failed, return to processing state (not stopping state)
            this.showProcessingIndicator();
        }
    }

    // UI Updates
    async selectSession(sessionId) {
        // If already connected to this session, don't reconnect
        if (this.currentSessionId === sessionId && this.sessionWebsocket && this.sessionWebsocket.readyState === WebSocket.OPEN) {
            Logger.debug('SESSION', 'Already connected to session', sessionId);
            return;
        }

        // Show loading screen immediately when switching sessions
        this.showLoading(true);

        try {
            // Clean disconnect from previous session
            if (this.currentSessionId && this.currentSessionId !== sessionId) {
                // Save current session's input before switching
                const messageInput = document.getElementById('message-input');
                if (messageInput) {
                    this.sessionInputCache.set(this.currentSessionId, messageInput.value);
                }
                Logger.info('SESSION', 'Switching sessions', {from: this.currentSessionId, to: sessionId});
                this.disconnectSessionWebSocket();
                // Wait a moment for the disconnection to complete
                await new Promise(resolve => setTimeout(resolve, 100));
            }

            this.currentSessionId = sessionId;

            // Processing state will be set by loadSessionInfo() call below

            // Update UI
            document.querySelectorAll('.session-item').forEach(item => {
                item.classList.remove('active');
            });

            const sessionElement = document.querySelector(`[data-session-id="${sessionId}"]`);
            if (sessionElement) {
                sessionElement.classList.add('active');
            }

            // Show chat container
            document.getElementById('no-session-selected').classList.add('d-none');
            document.getElementById('chat-container').classList.remove('d-none');

            // Load session info first to check state
            await this.loadSessionInfo();

            // Check if session needs to be started or is ready for use
            const session = this.sessions.get(sessionId);
            if (session) {
                if (session.state === 'error') {
                    // Session is in error state, skip WebSocket initialization
                    Logger.info('SESSION', 'Session is in error state, skipping WebSocket connection', sessionId);
                    // Just load messages without attempting to connect
                } else if (session.state === 'active' || session.state === 'running') {
                    // Session is already active, just connect WebSocket
                    Logger.info('SESSION', 'Session is already active, connecting WebSocket', sessionId);
                    this.connectSessionWebSocket();
                } else if (session.state === 'starting') {
                    // Session is starting, wait for it to become active
                    Logger.info('SESSION', 'Session is starting, waiting for it to become active', sessionId);
                    let attempts = 0;
                    const maxAttempts = 15;
                    const pollInterval = 1000;
                    while (attempts < maxAttempts) {
                        await new Promise(resolve => setTimeout(resolve, pollInterval));
                        await this.loadSessionInfo();
                        const updatedSession = this.sessions.get(sessionId);
                        if (updatedSession && updatedSession.state === 'error') {
                            Logger.info('SESSION', 'Session entered error state during startup, stopping wait', sessionId);
                            break;
                        } else if (updatedSession && (updatedSession.state === 'active' || updatedSession.state === 'running')) {
                            Logger.info('SESSION', 'Session is now active, connecting WebSocket', sessionId);
                            this.connectSessionWebSocket();
                            break;
                        }
                        attempts++;
                        Logger.debug('SESSION', 'Waiting for session to become active', {sessionId, attempt: attempts, max: maxAttempts});
                    }

                    if (attempts >= maxAttempts) {
                        Logger.warn('SESSION', 'Session did not become active', {sessionId, maxAttempts, seconds: maxAttempts * pollInterval / 1000});
                    }
                } else {
                    // Session needs to be started (both fresh sessions and existing sessions)
                    // The server-side logic will handle whether to create fresh or resume based on claude_code_session_id
                    Logger.info('SESSION', 'Starting session', {sessionId, currentState: session.state});
                    await this.apiRequest(`/api/sessions/${sessionId}/start`, { method: 'POST' });

                    // Wait for session to be fully active before connecting WebSocket
                    let attempts = 0;
                    const maxAttempts = 15; // Increased from 10 to allow for longer SDK initialization
                    const pollInterval = 1000; // Increased from 200ms to 1 second
                    while (attempts < maxAttempts) {
                        await new Promise(resolve => setTimeout(resolve, pollInterval));
                        await this.loadSessionInfo();
                        const updatedSession = this.sessions.get(sessionId);
                        if (updatedSession && updatedSession.state === 'error') {
                            Logger.info('SESSION', 'Session entered error state during startup, stopping wait', sessionId);
                            break;
                        } else if (updatedSession && (updatedSession.state === 'active' || updatedSession.state === 'running')) {
                            Logger.info('SESSION', 'Session is now active, connecting WebSocket', sessionId);
                            this.connectSessionWebSocket();
                            break;
                        }
                        attempts++;
                        Logger.debug('SESSION', 'Waiting for session to become active', {sessionId, attempt: attempts, max: maxAttempts});
                    }

                    if (attempts >= maxAttempts) {
                        Logger.warn('SESSION', 'Session did not become active', {sessionId, maxAttempts, seconds: maxAttempts * pollInterval / 1000});
                    }
                }
            }

            // Load messages after session is ready
            this.loadMessages();

            // Load session info to get current processing state from backend
            this.loadSessionInfo();

            // Restore cached input for this session
            const messageInput = document.getElementById('message-input');
            if (messageInput) {
                const cachedInput = this.sessionInputCache.get(sessionId) || '';
                messageInput.value = cachedInput;
                this.autoExpandTextarea(messageInput); // Adjust height if needed
            }

            // Update session info button state based on available data
            this.updateSessionInfoButton();
        } catch (error) {
            Logger.error('SESSION', 'Error selecting session', error);
        } finally {
            // Hide loading screen when session switch is complete
            this.showLoading(false);
        }
    }

    async renderSessions() {
        const container = document.getElementById('sessions-container');
        container.innerHTML = '';

        const projects = this.projectManager.getAllProjects();

        if (projects.length === 0) {
            container.innerHTML = '<p class="text-muted">No projects available. Create a project to get started.</p>';
            return;
        }

        for (const project of projects) {
            const projectElement = await this.createProjectElement(project);
            container.appendChild(projectElement);
        }
    }

    async createProjectElement(project) {
        const projectElement = document.createElement('div');
        projectElement.className = 'accordion-item border rounded mb-2';
        projectElement.setAttribute('data-project-id', project.project_id);
        projectElement.draggable = true;

        // Create accordion header
        const accordionHeader = document.createElement('h2');
        accordionHeader.className = 'accordion-header';
        accordionHeader.id = `heading-${project.project_id}`;

        // Create accordion button
        const accordionButton = document.createElement('button');
        accordionButton.className = `accordion-button ${project.is_expanded ? '' : 'collapsed'} bg-white p-2`;
        accordionButton.type = 'button';
        accordionButton.setAttribute('data-bs-toggle', 'collapse');
        accordionButton.setAttribute('data-bs-target', `#collapse-${project.project_id}`);
        accordionButton.setAttribute('aria-expanded', project.is_expanded);
        accordionButton.setAttribute('aria-controls', `collapse-${project.project_id}`);

        // Project name and path container
        const projectInfo = document.createElement('div');
        projectInfo.className = 'flex-grow-1 me-2';
        const formattedPath = this.projectManager.formatPath(project.working_directory);
        projectInfo.innerHTML = `
            <div class="fw-semibold">${this.escapeHtml(project.name)}</div>
            <small class="text-muted font-monospace" title="${this.escapeHtml(project.working_directory)}">${this.escapeHtml(formattedPath)}</small>
        `;

        accordionButton.appendChild(projectInfo);
        accordionHeader.appendChild(accordionButton);

        // Edit project button - appears on hover, positioned to the left of add session button
        const editProjectBtn = document.createElement('button');
        editProjectBtn.className = 'btn btn-sm btn-outline-secondary position-absolute end-0 top-50 translate-middle-y me-5 project-edit-btn';
        editProjectBtn.innerHTML = '✏️';
        editProjectBtn.title = 'Edit or delete project';
        editProjectBtn.type = 'button';
        editProjectBtn.style.zIndex = '10';
        editProjectBtn.style.opacity = '0'; // Hidden by default
        editProjectBtn.addEventListener('click', async (e) => {
            e.stopPropagation();
            e.preventDefault();
            await this.showEditProjectModal(project.project_id);
        });

        // Add session button - positioned next to accordion button, NOT inside it
        const addSessionBtn = document.createElement('button');
        addSessionBtn.className = 'btn btn-sm btn-outline-primary position-absolute end-0 top-50 translate-middle-y me-2';
        addSessionBtn.innerHTML = '+';
        addSessionBtn.title = 'Add session to project';
        addSessionBtn.type = 'button';
        addSessionBtn.style.zIndex = '10'; // Ensure it's above accordion button
        addSessionBtn.addEventListener('click', async (e) => {
            e.stopPropagation();
            e.preventDefault();
            await this.showCreateSessionModalForProject(project.project_id);
        });

        // Add buttons to accordion header (siblings of accordion button, not children)
        accordionHeader.style.position = 'relative'; // For absolute positioning of buttons
        accordionHeader.appendChild(editProjectBtn);
        accordionHeader.appendChild(addSessionBtn);

        // Show edit button on hover
        accordionHeader.addEventListener('mouseenter', () => {
            editProjectBtn.style.opacity = '1';
        });
        accordionHeader.addEventListener('mouseleave', () => {
            editProjectBtn.style.opacity = '0';
        });

        projectElement.appendChild(accordionHeader);

        // Project status line (always visible) - placed outside collapse area
        const statusLine = await this.createProjectStatusLine(project);
        projectElement.appendChild(statusLine);

        // Collapsible sessions area
        const collapseDiv = document.createElement('div');
        collapseDiv.id = `collapse-${project.project_id}`;
        collapseDiv.className = `accordion-collapse collapse ${project.is_expanded ? 'show' : ''}`;
        collapseDiv.setAttribute('aria-labelledby', `heading-${project.project_id}`);

        const collapseBody = document.createElement('div');
        collapseBody.className = 'accordion-body p-0';

        // Sessions container - use list group
        if (project.session_ids && project.session_ids.length > 0) {
            const sessionsContainer = document.createElement('div');
            sessionsContainer.className = 'list-group list-group-flush';

            // Use cached session data instead of fetching from API
            const sessions = project.session_ids
                .map(sid => this.sessions.get(sid))
                .filter(s => s); // Filter out any sessions that aren't in cache yet

            for (const session of sessions) {
                const sessionElement = this.createSessionElement(session, project.project_id);
                sessionsContainer.appendChild(sessionElement);
            }

            collapseBody.appendChild(sessionsContainer);
        }

        collapseDiv.appendChild(collapseBody);

        // Listen to Bootstrap collapse events to sync state with backend
        collapseDiv.addEventListener('shown.bs.collapse', async () => {
            if (!project.is_expanded) {
                await this.projectManager.toggleExpansion(project.project_id);
                project.is_expanded = true;
            }
        });

        collapseDiv.addEventListener('hidden.bs.collapse', async () => {
            if (project.is_expanded) {
                await this.projectManager.toggleExpansion(project.project_id);
                project.is_expanded = false;

                // Exit session if current session belongs to this project
                if (this.currentSessionId) {
                    const currentSession = this.sessions.get(this.currentSessionId);
                    if (currentSession && project.session_ids.includes(this.currentSessionId)) {
                        this.exitSession();
                    }
                }
            }
        });

        projectElement.appendChild(collapseDiv);

        // Add drag-and-drop event listeners for project reordering
        this.addProjectDragListeners(projectElement, project.project_id);

        return projectElement;
    }

    async createProjectStatusLine(project) {
        const statusLine = document.createElement('div');
        statusLine.className = 'progress project-status-line';
        statusLine.style.height = '4px';
        statusLine.style.borderRadius = '0';

        if (!project.session_ids || project.session_ids.length === 0) {
            // Empty project - show single gray segment
            const emptySegment = document.createElement('div');
            emptySegment.className = 'progress-bar bg-secondary';
            emptySegment.style.width = '100%';
            statusLine.appendChild(emptySegment);
            return statusLine;
        }

        // Use cached session data instead of fetching from API
        const sessions = project.session_ids
            .map(sid => this.sessions.get(sid))
            .filter(s => s); // Filter out any sessions that aren't in cache yet

        const segmentWidth = `${100 / sessions.length}%`;

        for (const session of sessions) {
            const segment = document.createElement('div');
            segment.className = 'progress-bar';
            segment.style.width = segmentWidth;

            // Determine color based on session state - match status dot fill colors
            const isProcessing = session.is_processing || false;
            const displayState = isProcessing ? 'processing' : session.state;
            const bgColor = this.getSessionStatusDotFillColor(displayState);

            segment.style.backgroundColor = bgColor;

            // Add animation for active states
            if (displayState === 'starting' || displayState === 'processing') {
                segment.classList.add('progress-bar-striped', 'progress-bar-animated');
            }

            statusLine.appendChild(segment);
        }

        return statusLine;
    }

    getSessionStateBgClass(state) {
        // Map states to Bootstrap background classes (legacy, kept for compatibility)
        const bgMap = {
            'created': 'bg-secondary',
            'CREATED': 'bg-secondary',
            'starting': 'bg-success',
            'Starting': 'bg-success',
            'running': 'bg-success',
            'active': 'bg-success',
            'processing': 'bg-primary',
            'paused': 'bg-secondary',
            'terminated': 'bg-secondary',
            'error': 'bg-danger',
            'failed': 'bg-danger'
        };
        return bgMap[state] || 'bg-secondary';
    }

    getSessionStatusDotFillColor(state) {
        // Return the fill color that matches status dot background colors
        const colorMap = {
            'created': '#d3d3d3',      // grey (status-dot-grey background)
            'CREATED': '#d3d3d3',
            'starting': '#90ee90',     // light green (status-dot-green background)
            'Starting': '#90ee90',
            'running': '#90ee90',      // light green (status-dot-green background)
            'active': '#90ee90',       // light green (status-dot-green background)
            'processing': '#dda0dd',   // light purple (status-dot-purple background)
            'paused': '#d3d3d3',       // grey (status-dot-grey background)
            'terminated': '#d3d3d3',   // grey (status-dot-grey background)
            'error': '#ffb3b3',        // light red (status-dot-red background)
            'failed': '#ffb3b3'        // light red (status-dot-red background)
        };
        return colorMap[state] || '#d3d3d3'; // default grey
    }

    getSessionStateColor(state) {
        // Match the colors from session indicator dots for consistency (border colors)
        const colorMap = {
            'created': '#6c757d',      // grey (matches status-dot-grey border)
            'CREATED': '#6c757d',
            'starting': '#28a745',     // green (matches status-dot-green border for starting/blinking)
            'Starting': '#28a745',
            'running': '#28a745',      // green (matches status-dot-green)
            'active': '#28a745',       // green (matches status-dot-green for active)
            'processing': '#6f42c1',   // purple (matches status-dot-purple border for processing/blinking)
            'paused': '#6c757d',       // grey (matches status-dot-grey)
            'terminated': '#6c757d',   // grey (matches status-dot-grey)
            'error': '#dc3545',        // red (matches status-dot-red border)
            'failed': '#dc3545'        // red (matches status-dot-red)
        };
        return colorMap[state] || '#6c757d'; // default grey
    }

    createSessionElement(session, projectId) {
        const sessionId = session.session_id;
        const sessionElement = document.createElement('div');
        sessionElement.className = 'list-group-item list-group-item-action';

        // Add active class if this is the currently selected session
        if (sessionId === this.currentSessionId) {
            sessionElement.classList.add('active');
        }

        sessionElement.setAttribute('data-session-id', sessionId);
        sessionElement.setAttribute('data-project-id', projectId);

        // Add drag-and-drop attributes
        sessionElement.draggable = true;
        sessionElement.setAttribute('data-order', session.order || 999999);
        sessionElement.addEventListener('click', (e) => {
            // Don't select session if clicking on edit button
            if (e.target.closest('.session-edit-btn')) return;
            this.selectSession(sessionId);
        });

        // Add drag-and-drop event listeners (project-aware)
        this.addDragDropListeners(sessionElement, sessionId, projectId);

        // Create status indicator - show processing state if is_processing is true
        const isProcessing = session.is_processing || false;
        const displayState = isProcessing ? 'processing' : session.state;
        const statusIndicator = this.createStatusIndicator(displayState, 'session', session.state);

        // Use session name if available, fallback to session ID
        const displayName = session.name || sessionId;

        sessionElement.innerHTML = `
            <div class="d-flex align-items-center gap-2 position-relative">
                <div class="flex-grow-1" title="${sessionId}">
                    <span class="session-name-display">${this.escapeHtml(displayName)}</span>
                </div>
                <button class="btn btn-sm btn-outline-secondary session-edit-btn" title="Edit or delete session" style="opacity: 0;">✏️</button>
            </div>
        `;

        // Insert status indicator at the beginning
        const sessionHeader = sessionElement.querySelector('.d-flex');
        sessionHeader.insertBefore(statusIndicator, sessionHeader.firstChild);

        // Add edit button click handler
        const editBtn = sessionElement.querySelector('.session-edit-btn');
        editBtn.addEventListener('click', async (e) => {
            e.stopPropagation();
            e.preventDefault();
            await this.showEditSessionModal(sessionId);
        });

        // Show/hide edit button on hover
        sessionElement.addEventListener('mouseenter', () => {
            editBtn.style.opacity = '1';
        });
        sessionElement.addEventListener('mouseleave', () => {
            editBtn.style.opacity = '0';
        });

        return sessionElement;
    }

    async toggleProjectExpansion(projectId) {
        try {
            const isExpanded = await this.projectManager.toggleExpansion(projectId);

            // If project was collapsed and current session belongs to this project, exit the session
            if (!isExpanded && this.currentSessionId) {
                const sessionProjectId = this._findProjectForSession(this.currentSessionId);
                if (sessionProjectId === projectId) {
                    this.exitSession();
                }
            }

            await this.updateProjectInDOM(projectId, 'expansion-toggled');
        } catch (error) {
            Logger.error('PROJECT', `Failed to toggle expansion for ${projectId}`, error);
        }
    }

    // ==================== GRANULAR DOM UPDATE METHODS ====================

    _findProjectForSession(sessionId) {
        /**
         * Find which project a session belongs to
         */
        for (const [projectId, project] of this.projectManager.projects) {
            if (project.session_ids && project.session_ids.includes(sessionId)) {
                return projectId;
            }
        }
        return null;
    }

    async updateProjectInDOM(projectId, updateType) {
        /**
         * Update a project element in the DOM without full re-render
         * @param {string} projectId - The project ID
         * @param {string} updateType - Type of update: 'session-added', 'session-removed',
         *                               'session-state-changed', 'expansion-toggled', 'metadata-changed'
         */
        const projectElement = document.querySelector(`[data-project-id="${projectId}"]`);
        if (!projectElement) {
            Logger.warn('DOM', `Project element not found for ${projectId}`);
            return;
        }

        const project = this.projectManager.projects.get(projectId);
        if (!project) {
            Logger.warn('DOM', `Project data not found for ${projectId}`);
            return;
        }

        switch (updateType) {
            case 'expansion-toggled':
                // Update Bootstrap accordion collapse state
                const accordionButton = projectElement.querySelector('.accordion-button');
                const collapseDiv = projectElement.querySelector('.accordion-collapse');

                if (accordionButton && collapseDiv) {
                    if (project.is_expanded) {
                        accordionButton.classList.remove('collapsed');
                        accordionButton.setAttribute('aria-expanded', 'true');
                        collapseDiv.classList.add('show');

                        // Recreate session elements from cache to ensure fresh state
                        const accordionBody = collapseDiv.querySelector('.accordion-body');
                        let sessionsContainer = accordionBody?.querySelector('.list-group.list-group-flush');

                        if (accordionBody && project.session_ids && project.session_ids.length > 0) {
                            // Remove existing container if present
                            if (sessionsContainer) {
                                sessionsContainer.remove();
                            }

                            // Create fresh container with updated session data
                            sessionsContainer = document.createElement('div');
                            sessionsContainer.className = 'list-group list-group-flush';

                            const sessions = project.session_ids
                                .map(sid => this.sessions.get(sid))
                                .filter(s => s);

                            for (const session of sessions) {
                                const sessionElement = this.createSessionElement(session, projectId);
                                sessionsContainer.appendChild(sessionElement);
                            }

                            accordionBody.appendChild(sessionsContainer);
                        }
                    } else {
                        accordionButton.classList.add('collapsed');
                        accordionButton.setAttribute('aria-expanded', 'false');
                        collapseDiv.classList.remove('show');
                    }
                }
                break;

            case 'session-added':
            case 'session-removed':
            case 'session-state-changed':
                // Re-render status line for these changes
                await this.updateProjectStatusLine(projectId);
                break;

            case 'metadata-changed':
                // Update project name and path in the new Bootstrap structure
                const projectInfo = projectElement.querySelector('.flex-grow-1');
                if (projectInfo) {
                    const formattedPath = this.projectManager.formatPath(project.working_directory);
                    projectInfo.innerHTML = `
                        <div class="fw-semibold">${this.escapeHtml(project.name)}</div>
                        <small class="text-muted font-monospace" title="${this.escapeHtml(project.working_directory)}">${this.escapeHtml(formattedPath)}</small>
                    `;
                }
                break;
        }
    }

    updateSessionInDOM(sessionId, sessionData) {
        /**
         * Update a session element in the DOM without full re-render
         * Updates status indicator and name only
         * ALWAYS updates cache, even if DOM element doesn't exist (collapsed project)
         */
        // ALWAYS update cached session data first (even if DOM element doesn't exist)
        this.sessions.set(sessionId, sessionData);
        const index = this.orderedSessions.findIndex(s => s.session_id === sessionId);
        if (index !== -1) {
            this.orderedSessions[index] = sessionData;
        }

        // Now update DOM if element exists (project is expanded)
        const sessionElement = document.querySelector(`[data-session-id="${sessionId}"]`);
        if (!sessionElement) {
            Logger.debug('DOM', `Session element not in DOM (project collapsed): ${sessionId} - cache updated`, {
                state: sessionData.state,
                is_processing: sessionData.is_processing
            });
            return;
        }

        // Update status indicator
        const sessionHeader = sessionElement.querySelector('.d-flex');
        if (sessionHeader) {
            // Remove old status indicator (status-dot)
            const oldIndicator = sessionHeader.querySelector('.status-dot');
            if (oldIndicator) {
                oldIndicator.remove();
            }

            // Create new status indicator
            const isProcessing = sessionData.is_processing || false;
            const displayState = isProcessing ? 'processing' : sessionData.state;
            const statusIndicator = this.createStatusIndicator(displayState, 'session', sessionData.state);

            // Insert at beginning of header
            sessionHeader.insertBefore(statusIndicator, sessionHeader.firstChild);

            Logger.debug('DOM', `Updated session indicator in DOM: ${sessionId}`, {
                state: sessionData.state,
                is_processing: sessionData.is_processing,
                displayState
            });
        }

        // Update session name if changed
        const nameDisplay = sessionElement.querySelector('.session-name-display');
        const nameInput = sessionElement.querySelector('.session-name-edit');
        if (nameDisplay && sessionData.name) {
            const displayName = sessionData.name || sessionId;
            if (nameDisplay.textContent !== displayName) {
                nameDisplay.textContent = displayName;
                if (nameInput) {
                    nameInput.value = displayName;
                }
            }
        }
    }

    async updateProjectStatusLine(projectId) {
        /**
         * Update just the project status line (multi-segment bar)
         * without re-rendering the entire project
         */
        const projectElement = document.querySelector(`[data-project-id="${projectId}"]`);
        if (!projectElement) {
            Logger.warn('DOM', `Project element not found for status line update: ${projectId}`);
            return;
        }

        const project = this.projectManager.projects.get(projectId);
        if (!project) {
            Logger.warn('DOM', `Project data not found for status line update: ${projectId}`);
            return;
        }

        // Find existing status line
        const oldStatusLine = projectElement.querySelector('.project-status-line');
        if (!oldStatusLine) {
            Logger.warn('DOM', `Status line element not found for ${projectId}`);
            return;
        }

        // Create new status line
        const newStatusLine = await this.createProjectStatusLine(project);

        // Replace old with new
        oldStatusLine.replaceWith(newStatusLine);
    }

    async addSessionToProjectDOM(projectId, sessionData) {
        /**
         * Add a new session element to a project's sessions container
         * Updates status line as well
         */
        const projectElement = document.querySelector(`[data-project-id="${projectId}"]`);
        if (!projectElement) {
            Logger.warn('DOM', `Project element not found for adding session: ${projectId}`);
            return;
        }

        const project = this.projectManager.projects.get(projectId);
        if (!project) {
            Logger.warn('DOM', `Project data not found for adding session: ${projectId}`);
            return;
        }

        // Update cached session data
        this.sessions.set(sessionData.session_id, sessionData);
        if (!this.orderedSessions.find(s => s.session_id === sessionData.session_id)) {
            this.orderedSessions.unshift(sessionData); // Add to beginning of list
        }

        // Update project's session_ids if not already present (add to top)
        if (!project.session_ids.includes(sessionData.session_id)) {
            project.session_ids.unshift(sessionData.session_id); // Add to beginning of array
        }

        // Expand the project if it's collapsed (for better UX when creating a session)
        if (!project.is_expanded) {
            // Update backend state - this will trigger the expansion listener which updates the DOM
            await this.projectManager.toggleExpansion(projectId);
            project.is_expanded = true;

            // Re-render the project to show the expansion with the new session
            await this.updateProjectInDOM(projectId, 'session-added');
        } else {
            // Project is already expanded, just add session element to container
            const accordionBody = projectElement.querySelector('.accordion-body');
            let sessionsContainer = accordionBody?.querySelector('.list-group.list-group-flush');

            // Create container if it doesn't exist
            if (!sessionsContainer && accordionBody) {
                sessionsContainer = document.createElement('div');
                sessionsContainer.className = 'list-group list-group-flush';
                accordionBody.appendChild(sessionsContainer);
            }

            // Create and prepend session element (new sessions go to the top)
            if (sessionsContainer) {
                const sessionElement = this.createSessionElement(sessionData, projectId);
                sessionsContainer.insertBefore(sessionElement, sessionsContainer.firstChild);
            }
        }

        // Update status line to reflect new session
        await this.updateProjectStatusLine(projectId);
    }

    async showCreateSessionModalForProject(projectId) {
        this.currentProjectId = projectId;
        // Show modal without working directory field (project determines this)
        const modalElement = document.getElementById('create-session-modal');
        const workingDirGroup = document.getElementById('working-directory-group');
        workingDirGroup.style.display = 'none'; // Hide working directory field
        const modal = new bootstrap.Modal(modalElement);
        modal.show();
    }

    updateSessionHeaderName(name) {
        // Update the header to display session name instead of ID
        const currentSessionIdElement = document.getElementById('current-session-id');
        if (currentSessionIdElement) {
            currentSessionIdElement.textContent = name;
        }
    }

    updateSessionInfo(sessionData) {
        // Display session name if available, fallback to session ID
        const sessionName = sessionData.session.name || this.currentSessionId;
        document.getElementById('current-session-id').textContent = sessionName;

        // Update session state indicator
        const stateContainer = document.getElementById('current-session-state');
        stateContainer.innerHTML = '';

        // Create new status dot - show processing state if is_processing is true
        const isProcessing = sessionData.session.is_processing || false;
        const displayState = isProcessing ? 'processing' : sessionData.session.state;
        const statusDot = this.createStatusIndicator(displayState, 'session', sessionData.session.state);
        stateContainer.appendChild(statusDot);
        stateContainer.className = `session-state-indicator ${displayState}`;

        // Handle error state display
        const sessionInfoBar = document.getElementById('session-info-bar');
        const errorMessageElement = document.getElementById('session-error-message');

        if (sessionData.session.state === 'error' && sessionData.session.error_message) {
            // Show error message in top bar
            errorMessageElement.textContent = sessionData.session.error_message;
            errorMessageElement.classList.remove('d-none');
            sessionInfoBar.classList.add('error');
            Logger.info('UI', 'Displaying error message in top bar', sessionData.session.error_message);

            // For error state: clear any processing indicator and disable input controls
            this.updateProcessingState(false);
            this.setInputControlsEnabled(false);
        } else {
            // Hide error message and remove error styling
            errorMessageElement.classList.add('d-none');
            sessionInfoBar.classList.remove('error');

            // Check processing state from backend and update UI accordingly
            const backendProcessingState = sessionData.session.is_processing || false;
            this.updateProcessingState(backendProcessingState);

            // For non-error sessions: enable controls if not processing, keep disabled if processing
            if (!backendProcessingState) {
                this.setInputControlsEnabled(true);
            }
        }

        // Update the sessions Map with current session state
        if (this.currentSessionId && this.sessions.has(this.currentSessionId)) {
            const existingSession = this.sessions.get(this.currentSessionId);
            existingSession.state = sessionData.session.state;
            existingSession.error_message = sessionData.session.error_message;
            existingSession.is_processing = sessionData.session.is_processing || false;
            existingSession.current_permission_mode = sessionData.session.current_permission_mode || 'acceptEdits';

            // Sync local permission mode state with backend (source of truth)
            this.currentPermissionMode = existingSession.current_permission_mode;

            // Update session data consistently (with automatic re-render)
            this.updateSessionData(this.currentSessionId, existingSession);
        }

        // Update permission mode display
        this.updatePermissionModeDisplay(sessionData.session);
    }

    updatePermissionModeDisplay(session) {
        const statusBar = document.getElementById('status-bar');
        const permissionModeClickable = document.getElementById('permission-mode-clickable');
        const permissionModeIcon = document.getElementById('permission-mode-icon');
        const permissionModeText = document.getElementById('permission-mode-text');

        const currentMode = session.current_permission_mode || 'acceptEdits';
        const isActive = session.state === 'active';

        // Show/hide status bar based on session state
        if (isActive) {
            statusBar.classList.remove('d-none');
        } else {
            statusBar.classList.add('d-none');
            return;
        }

        // Update mode display
        const modeConfig = {
            'default': {
                icon: '🔒',
                label: 'Mode: default',
                description: 'Click to cycle modes • Requires approval for most tools',
                color: 'grey'
            },
            'acceptEdits': {
                icon: '✏️',
                label: 'Mode: acceptEdits',
                description: 'Click to cycle modes • Auto-approves file edits',
                color: 'green'
            },
            'plan': {
                icon: '📋',
                label: 'Mode: plan',
                description: 'Click to cycle modes • Read-only mode',
                color: 'blue'
            }
        };

        const config = modeConfig[currentMode] || modeConfig['acceptEdits'];

        permissionModeIcon.textContent = config.icon;
        permissionModeText.textContent = config.label;

        // Set description as tooltip on the clickable area
        permissionModeClickable.title = config.description;
    }

    async cyclePermissionMode() {
        if (!this.currentSessionId) return;

        const session = this.sessions.get(this.currentSessionId);
        if (!session || session.state !== 'active') {
            Logger.debug('PERMISSION', 'Cannot cycle permission mode - session not active');
            return;
        }

        // Define cycle order (excluding bypassPermissions for safety)
        const modeOrder = ['default', 'acceptEdits', 'plan'];
        const currentMode = session.current_permission_mode || 'acceptEdits';
        const currentIndex = modeOrder.indexOf(currentMode);
        const nextIndex = (currentIndex + 1) % modeOrder.length;
        const nextMode = modeOrder[nextIndex];

        Logger.info('PERMISSION', 'Cycling permission mode', {from: currentMode, to: nextMode});

        // Set flag to prevent flash on user-initiated change
        this._userInitiatedModeChange = true;

        try {
            const response = await this.apiRequest(`/api/sessions/${this.currentSessionId}/permission-mode`, {
                method: 'POST',
                body: JSON.stringify({ mode: nextMode })
            });

            if (response.success) {
                Logger.info('PERMISSION', 'Successfully changed permission mode', nextMode);
                // Update local session data
                session.current_permission_mode = nextMode;
                this.updatePermissionModeDisplay(session);
            }
        } catch (error) {
            Logger.error('PERMISSION', 'Failed to cycle permission mode', error);
            this.showError(`Failed to change permission mode: ${error.message}`);
        }
    }

    renderMessages(messages) {
        const messagesArea = document.getElementById('messages-area');
        messagesArea.innerHTML = '';

        // Clear the tool call manager for fresh loading
        this.toolCallManager = new ToolCallManager();

        // Single-pass processing for historical messages using unified logic
        Logger.debug('MESSAGE', 'Processing historical messages with unified single-pass approach', {count: messages.length});

        let toolUseCount = 0;
        messages.forEach(message => {
            // Process each message in order - tool calls will be created as needed
            const result = this.processMessage(message, 'historical');

            // Count tool uses for logging
            if (message.type === 'assistant' && message.metadata && message.metadata.tool_uses) {
                toolUseCount += message.metadata.tool_uses.length;
            }
        });

        Logger.debug('MESSAGE', 'Single-pass processing complete', {toolUseCount, messageCount: messages.length});
        this.smartScrollToBottom();
    }

    addMessageToUI(message, scroll = true) {
        const messagesArea = document.getElementById('messages-area');
        const messageElement = document.createElement('div');

        // Use metadata for enhanced styling if available
        const subtype = message.subtype || message.metadata?.subtype;
        const messageClass = subtype ? `message-row row py-1 ${message.type} ${subtype}` : `message-row row py-1 ${message.type}`;
        messageElement.className = messageClass;

        const timestamp = new Date(message.timestamp).toLocaleTimeString();

        // Determine speaker label
        let speakerLabel = message.type;
        if (message.type === 'assistant') {
            speakerLabel = 'agent';
        }

        // Handle local command responses: render as system messages
        if (message.type === 'user' && (message.metadata?.is_local_command_response || subtype === 'local_command_response')) {
            speakerLabel = 'system';
        }

        // Build content using standardized approach
        let contentHtml = '';
        const content = message.content || '';

        // Determine display mode based on message type and metadata
        const shouldShowMetadata = this._shouldShowMetadata(message);
        const shouldShowJsonContent = this.isJsonContent(content);

        if (shouldShowMetadata || shouldShowJsonContent) {
            // Show primary text content
            if (content) {
                contentHtml += `<div class="message-content">${this.escapeHtml(content)}</div>`;
            }

            // Show metadata or JSON content for debugging/system messages
            if (shouldShowMetadata) {
                const debugData = this._getDisplayMetadata(message);
                if (debugData) {
                    contentHtml += `<div class="message-json">${this.formatJson(debugData)}</div>`;
                }
            } else if (shouldShowJsonContent) {
                const jsonData = this.tryParseJson(content);
                if (jsonData) {
                    contentHtml += `<div class="message-json">${this.formatJson(jsonData)}</div>`;
                }
            }
        } else {
            // Regular text content with smart formatting
            contentHtml = this._formatMessageContent(message, content);
        }

        // Two-column layout: speaker | content
        messageElement.innerHTML = `
            <div class="col-auto message-speaker text-end pe-3" title="${timestamp}">
                ${speakerLabel}
            </div>
            <div class="col message-content-column">
                ${contentHtml}
            </div>
        `;

        messagesArea.appendChild(messageElement);

        if (scroll) {
            this.smartScrollToBottom();
        }
    }

    /**
     * Determine if metadata should be shown for debugging purposes
     */
    _shouldShowMetadata(message) {
        // Show metadata for result messages (internal completion data)
        if (message.type === 'result') {
            return true;
        }

        // For system messages, only show metadata for debugging/error types
        if (message.type === 'system') {
            const subtype = message.subtype || message.metadata?.subtype;
            // Show metadata for internal/debug system messages, not user-facing ones
            if (subtype === 'interrupt' || subtype === 'client_launched' || subtype === 'init') {
                return false; // User-facing system messages should be clean
            }
            return true; // Other system messages (errors, etc.) show metadata
        }

        // Don't show metadata for regular user and assistant messages
        if (message.type === 'user' || message.type === 'assistant') {
            return false;
        }

        // Show metadata for other message types or unusual cases
        return false;
    }

    /**
     * Get relevant metadata for display (excluding raw data)
     */
    _getDisplayMetadata(message) {
        if (!message.metadata) {
            return null;
        }

        // Create clean metadata object excluding raw SDK data
        const displayMetadata = {};

        for (const [key, value] of Object.entries(message.metadata)) {
            // Skip raw SDK data fields
            if (key === 'raw_sdk_message' || key === 'raw_sdk_response' || key === 'sdk_message') {
                continue;
            }

            // Skip processing metadata
            if (key === 'source' || key === 'processed_at') {
                continue;
            }

            // Include relevant metadata
            displayMetadata[key] = value;
        }

        return Object.keys(displayMetadata).length > 0 ? displayMetadata : null;
    }

    /**
     * Format message content with smart handling
     */
    _formatMessageContent(message, content) {
        // Handle empty content
        if (!content) {
            // Show placeholder for empty user messages that might have been tool results
            if (message.type === 'user' && message.metadata?.has_tool_results) {
                return `<div class="message-content message-empty">[Tool results handled by tool call UI]</div>`;
            }
            return `<div class="message-content message-empty">[Empty message]</div>`;
        }

        // Special formatting for local command responses (monospace, pre-formatted)
        const subtype = message.subtype || message.metadata?.subtype;
        if (message.type === 'user' && (message.metadata?.is_local_command_response || subtype === 'local_command_response')) {
            return `<div class="message-content" style="font-family: monospace; white-space: pre; background-color: #f8f9fa; padding: 8px; border-radius: 4px; font-size: 0.9em;">${this.escapeHtml(content.trim())}</div>`;
        }

        // Trim leading/trailing whitespace (internal newlines preserved by CSS white-space: pre-wrap)
        return `<div class="message-content">${this.escapeHtml(content.trim())}</div>`;
    }

    /**
     * Get enhanced message header with subtype information
     */
    _getMessageHeader(message) {
        const subtype = message.subtype || message.metadata?.subtype;

        if (subtype) {
            return `${message.type} (${subtype})`;
        }

        return message.type;
    }

    handleStateChange(stateData) {
        Logger.debug('SESSION', 'Session state changed', stateData);

        // Update specific session in real-time instead of reloading all sessions
        const sessionId = stateData.session_id;
        const sessionInfo = stateData.session;

        // Skip processing if this session is being deleted
        if (this.deletingSessions.has(sessionId)) {
            Logger.debug('SESSION', 'Ignoring state change - deletion in progress', sessionId);
            return;
        }

        if (sessionInfo) {
            // Check for permission mode changes (only for current session)
            if (sessionId === this.currentSessionId) {
                const oldSession = this.sessions.get(sessionId);
                if (oldSession && oldSession.current_permission_mode !== sessionInfo.current_permission_mode) {
                    const newMode = sessionInfo.current_permission_mode;
                    // Only flash if this was NOT a user-initiated change (e.g., ExitPlanMode, approved suggestion)
                    if (!this._userInitiatedModeChange) {
                        this.flashModeButton();
                    }
                    // Clear the flag
                    this._userInitiatedModeChange = false;
                    Logger.info('PERMISSION', 'Mode changed', {old: oldSession.current_permission_mode, new: newMode});
                }
            }

            // GRANULAR UPDATE: Update session in DOM without full re-render
            this.updateSessionInDOM(sessionId, sessionInfo);

            // Update project status line to reflect state change
            const projectId = this._findProjectForSession(sessionId);
            if (projectId) {
                this.updateProjectStatusLine(projectId);
            }

            // If this is the current session, update the session info display
            if (sessionId === this.currentSessionId) {
                this.updateSessionInfo({ session: sessionInfo });
            }
        }
    }

    updateSpecificSession(sessionId, sessionInfo) {
        // Skip if session is being deleted
        if (this.deletingSessions.has(sessionId)) {
            return;
        }

        // Update session data consistently (with automatic re-render)
        this.updateSessionData(sessionId, sessionInfo);

        if (sessionId === this.currentSessionId) {
            this.updateSessionInfo({ session: sessionInfo });
        }
    }

    handleSessionDeleted(sessionId) {
        // Clean up a session that was deleted externally
        Logger.info('SESSION', 'Handling external deletion of session', sessionId);

        // Remove from sessions map
        this.sessions.delete(sessionId);

        // Remove from orderedSessions array
        this.orderedSessions = this.orderedSessions.filter(s => s.session_id !== sessionId);

        // If it was the current session, exit it
        if (this.currentSessionId === sessionId) {
            this.exitSession();
        }

        // Re-render sessions
        this.renderSessions();
    }

    updateConnectionStatus(status) {
        const indicatorContainer = document.getElementById('connection-indicator');

        // Clear existing indicator
        indicatorContainer.innerHTML = '';

        // Create new status dot
        const statusDot = this.createStatusIndicator(status, 'websocket', status);
        indicatorContainer.appendChild(statusDot);

        // Keep the container class for any legacy styling
        indicatorContainer.className = `connection-status-indicator ${status}`;
    }

    // Modal Management
    showCreateSessionModal() {
        document.getElementById('working-directory').value = '.';
        const modalElement = document.getElementById('create-session-modal');
        const modal = new bootstrap.Modal(modalElement);
        modal.show();
    }

    hideCreateSessionModal() {
        const modalElement = document.getElementById('create-session-modal');
        const modal = bootstrap.Modal.getInstance(modalElement);
        if (modal) {
            modal.hide();
        }

        // Reset form and buttons
        const form = document.getElementById('create-session-form');
        form.reset();

        // Re-enable all buttons in case they were disabled
        const submitBtn = modalElement.querySelector('button[type="submit"]');
        const dismissButtons = modalElement.querySelectorAll('[data-bs-dismiss="modal"]');
        if (submitBtn) {
            submitBtn.disabled = false;
            submitBtn.innerHTML = 'Create Session';
        }
        dismissButtons.forEach(btn => btn.disabled = false);
    }

    handleCreateSession(event) {
        event.preventDefault();

        const formData = new FormData(event.target);
        const data = Object.fromEntries(formData.entries());

        // Disable form buttons to prevent double submission
        const modalElement = document.getElementById('create-session-modal');
        const submitBtn = modalElement.querySelector('button[type="submit"]');
        const dismissButtons = modalElement.querySelectorAll('[data-bs-dismiss="modal"]');

        if (!submitBtn) {
            Logger.error('SESSION', 'Could not find submit button');
            return;
        }

        const originalSubmitText = submitBtn.innerHTML;

        submitBtn.disabled = true;
        dismissButtons.forEach(btn => btn.disabled = true);
        submitBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Creating...';

        this.createSession(data)
            .then(() => {
                this.hideCreateSessionModal();

                // Close sidebar on mobile after session creation
                if (window.innerWidth < 768 && !this.sidebarCollapsed) {
                    this.toggleSidebar();
                }
            })
            .catch(error => {
                Logger.error('SESSION', 'Session creation failed', error);
                // Re-enable buttons on error
                submitBtn.disabled = false;
                dismissButtons.forEach(btn => btn.disabled = false);
                submitBtn.innerHTML = originalSubmitText;
            });
    }

    browseDirectory() {
        this.showFolderBrowser('working-directory');
    }

    // Project Modal Methods
    showCreateProjectModal() {
        const modalElement = document.getElementById('create-project-modal');
        const modal = new bootstrap.Modal(modalElement);
        modal.show();
    }

    hideCreateProjectModal() {
        const modalElement = document.getElementById('create-project-modal');
        const modal = bootstrap.Modal.getInstance(modalElement);
        if (modal) {
            modal.hide();
        }
        document.getElementById('create-project-form').reset();
    }

    async handleCreateProject(event) {
        event.preventDefault();

        const formData = new FormData(event.target);
        const data = Object.fromEntries(formData.entries());

        try {
            const project = await this.projectManager.createProject(data.name, data.working_directory);
            this.hideCreateProjectModal();
            await this.refreshSessions(); // Reload to show new project
            Logger.info('PROJECT', 'Project created successfully', project);
        } catch (error) {
            Logger.error('PROJECT', 'Project creation failed', error);
            this.showError('Failed to create project: ' + error.message);
        }
    }

    browseProjectDirectory() {
        this.showFolderBrowser('project-working-directory');
    }

    // Folder Browser Methods
    showFolderBrowser(targetInputId, initialPath = null) {
        this.folderBrowserTargetInput = targetInputId;
        const modalElement = document.getElementById('folder-browser-modal');
        const modal = new bootstrap.Modal(modalElement);
        modal.show();

        // Load initial path
        const startPath = initialPath || document.getElementById(targetInputId).value || null;
        this.browseFolderPath(startPath);
    }

    async browseFolderPath(path = null) {
        try {
            const response = await fetch(`/api/filesystem/browse${path ? `?path=${encodeURIComponent(path)}` : ''}`);
            if (!response.ok) {
                throw new Error('Failed to browse filesystem');
            }

            const data = await response.json();

            // Update current path display
            document.getElementById('folder-browser-path').value = data.current_path;

            // Enable/disable up button
            const upBtn = document.getElementById('folder-browser-up');
            upBtn.disabled = !data.parent_path;

            // Render folders list
            const listContainer = document.getElementById('folder-browser-list');
            listContainer.innerHTML = '';

            if (data.directories.length === 0) {
                const emptyMsg = document.createElement('div');
                emptyMsg.className = 'list-group-item text-muted';
                emptyMsg.textContent = 'No subdirectories found';
                listContainer.appendChild(emptyMsg);
            } else {
                data.directories.forEach(dir => {
                    const item = document.createElement('button');
                    item.type = 'button';
                    item.className = 'list-group-item list-group-item-action';
                    item.textContent = `📁 ${dir.name}`;
                    item.onclick = () => this.browseFolderPath(dir.path);
                    listContainer.appendChild(item);
                });
            }

            Logger.info('FOLDER_BROWSER', `Browsing: ${data.current_path}`);
        } catch (error) {
            Logger.error('FOLDER_BROWSER', 'Failed to browse path', error);
            this.showError('Failed to browse filesystem: ' + error.message);
        }
    }

    selectCurrentFolder() {
        const currentPath = document.getElementById('folder-browser-path').value;
        if (currentPath && this.folderBrowserTargetInput) {
            document.getElementById(this.folderBrowserTargetInput).value = currentPath;
            this.hideFolderBrowser();
            Logger.info('FOLDER_BROWSER', `Selected folder: ${currentPath}`);
        }
    }

    hideFolderBrowser() {
        const modalElement = document.getElementById('folder-browser-modal');
        const modal = bootstrap.Modal.getInstance(modalElement);
        if (modal) {
            modal.hide();
        }
        this.folderBrowserTargetInput = null;
    }

    // Project Edit/Delete Modal Methods

    async showEditProjectModal(projectId) {
        const project = this.projectManager.getProject(projectId);
        if (!project) return;

        // Store the project ID for later use
        this.editingProjectId = projectId;

        // Update modal content
        document.getElementById('edit-project-name').value = project.name;
        const directoryElement = document.getElementById('edit-project-directory');
        directoryElement.textContent = project.working_directory;
        directoryElement.title = project.working_directory; // Show full path on hover

        const modalElement = document.getElementById('edit-project-modal');
        const modal = new bootstrap.Modal(modalElement);
        modal.show();
    }

    hideEditProjectModal() {
        const modalElement = document.getElementById('edit-project-modal');
        const modal = bootstrap.Modal.getInstance(modalElement);
        if (modal) {
            modal.hide();
        }
    }

    async confirmProjectRename() {
        if (!this.editingProjectId) return;

        const newName = document.getElementById('edit-project-name').value.trim();
        if (!newName) {
            this.showError('Project name cannot be empty');
            return;
        }

        const saveBtn = document.getElementById('save-project-btn');
        const cancelBtn = document.querySelector('#edit-project-modal .btn-secondary');
        const deleteBtn = document.getElementById('delete-project-btn');

        try {
            // Disable buttons to prevent double-clicks
            saveBtn.disabled = true;
            if (cancelBtn) cancelBtn.disabled = true;
            if (deleteBtn) deleteBtn.disabled = true;

            this.showLoading(true);

            // Update project via API
            await this.projectManager.updateProject(this.editingProjectId, { name: newName });

            // Hide modal
            this.hideEditProjectModal();

            Logger.info('PROJECT', 'Project renamed successfully', this.editingProjectId);
        } catch (error) {
            Logger.error('PROJECT', 'Failed to rename project', error);
            this.showError(`Failed to rename project: ${error.message}`);
        } finally {
            // Re-enable buttons
            saveBtn.disabled = false;
            if (cancelBtn) cancelBtn.disabled = false;
            if (deleteBtn) deleteBtn.disabled = false;
            this.showLoading(false);
            this.editingProjectId = null;
        }
    }

    showDeleteProjectConfirmation() {
        if (!this.editingProjectId) return;

        const project = this.projectManager.getProject(this.editingProjectId);
        if (!project) return;

        // Hide edit modal first
        this.hideEditProjectModal();

        // Show delete confirmation modal
        document.getElementById('delete-project-name').textContent = project.name;
        const modalElement = document.getElementById('delete-project-modal');
        const modal = new bootstrap.Modal(modalElement);
        modal.show();
    }

    hideDeleteProjectModal() {
        const modalElement = document.getElementById('delete-project-modal');
        const modal = bootstrap.Modal.getInstance(modalElement);
        if (modal) {
            modal.hide();
        }
    }

    async confirmDeleteProject() {
        if (!this.editingProjectId) return;

        const projectIdToDelete = this.editingProjectId;
        const confirmBtn = document.getElementById('confirm-delete-project');
        const cancelBtn = document.querySelector('#delete-project-modal .btn-secondary');

        try {
            // Disable both buttons to prevent double-clicks
            confirmBtn.disabled = true;
            if (cancelBtn) cancelBtn.disabled = true;

            this.showLoading(true);

            // Check if current session belongs to this project BEFORE deletion
            let shouldExitSession = false;
            if (this.currentSessionId) {
                const project = this.projectManager.getProject(projectIdToDelete);
                if (project && project.session_ids.includes(this.currentSessionId)) {
                    shouldExitSession = true;
                }
            }

            // Delete project via API (backend handles cascade delete of sessions)
            await this.projectManager.deleteProject(projectIdToDelete);

            // Hide modal
            this.hideDeleteProjectModal();

            // Exit session if it belonged to the deleted project
            if (shouldExitSession) {
                this.exitSession();
            }

            Logger.info('PROJECT', 'Project deleted successfully', projectIdToDelete);
        } catch (error) {
            Logger.error('PROJECT', 'Failed to delete project', error);
            this.showError(`Failed to delete project: ${error.message}`);
        } finally {
            // Re-enable buttons
            confirmBtn.disabled = false;
            if (cancelBtn) cancelBtn.disabled = false;
            this.showLoading(false);
            this.editingProjectId = null;
        }
    }

    // Session Edit/Delete Modal Methods

    async showEditSessionModal(sessionId) {
        const session = this.sessions.get(sessionId);
        if (!session) return;

        // Store the session ID for later use
        this.editingSessionId = sessionId;

        // Update modal content
        document.getElementById('edit-session-name').value = session.name || sessionId;
        const sessionIdElement = document.getElementById('edit-session-id');
        sessionIdElement.textContent = sessionId;
        sessionIdElement.title = sessionId; // Show full ID on hover

        // Set permission mode dropdown
        const permissionModeSelect = document.getElementById('edit-session-permission-mode');
        permissionModeSelect.value = session.current_permission_mode || 'default';

        const modalElement = document.getElementById('edit-session-modal');
        const modal = new bootstrap.Modal(modalElement);
        modal.show();
    }

    hideEditSessionModal() {
        const modalElement = document.getElementById('edit-session-modal');
        const modal = bootstrap.Modal.getInstance(modalElement);
        if (modal) {
            modal.hide();
        }
    }

    async confirmSessionRename() {
        if (!this.editingSessionId) return;

        const newName = document.getElementById('edit-session-name').value.trim();
        if (!newName) {
            this.showError('Session name cannot be empty');
            return;
        }

        const newPermissionMode = document.getElementById('edit-session-permission-mode').value;
        const session = this.sessions.get(this.editingSessionId);

        const saveBtn = document.getElementById('save-session-btn');
        const cancelBtn = document.querySelector('#edit-session-modal .btn-secondary');
        const deleteBtn = document.getElementById('delete-session-from-edit-btn');

        try {
            // Disable buttons to prevent double-clicks
            saveBtn.disabled = true;
            if (cancelBtn) cancelBtn.disabled = true;
            if (deleteBtn) deleteBtn.disabled = true;

            this.showLoading(true);

            // Update session name via API if changed
            if (newName !== session.name) {
                const response = await this.apiRequest(`/api/sessions/${this.editingSessionId}/name`, {
                    method: 'PUT',
                    body: JSON.stringify({ name: newName })
                });

                if (response.success) {
                    // Update local session data
                    if (this.sessions.has(this.editingSessionId)) {
                        const session = this.sessions.get(this.editingSessionId);
                        session.name = newName;
                        this.updateSessionData(this.editingSessionId, session);
                    }

                    // Update header if this is the current session
                    if (this.editingSessionId === this.currentSessionId) {
                        this.updateSessionHeaderName(newName);
                    }

                    Logger.info('SESSION', 'Session renamed successfully', this.editingSessionId);
                } else {
                    throw new Error('Failed to update session name');
                }
            }

            // Update permission mode via API if changed
            if (newPermissionMode !== session.current_permission_mode) {
                const response = await this.apiRequest(`/api/sessions/${this.editingSessionId}/permission-mode`, {
                    method: 'POST',
                    body: JSON.stringify({ mode: newPermissionMode })
                });

                if (response.success) {
                    // Update local session data
                    if (this.sessions.has(this.editingSessionId)) {
                        const session = this.sessions.get(this.editingSessionId);
                        session.current_permission_mode = newPermissionMode;
                        this.updateSessionData(this.editingSessionId, session);
                    }

                    // Update permission mode display if this is the current session
                    if (this.editingSessionId === this.currentSessionId) {
                        this.updatePermissionMode(newPermissionMode);
                    }

                    Logger.info('SESSION', 'Permission mode updated successfully', {
                        sessionId: this.editingSessionId,
                        newMode: newPermissionMode
                    });
                } else {
                    throw new Error('Failed to update permission mode');
                }
            }

            // Hide modal
            this.hideEditSessionModal();

        } catch (error) {
            Logger.error('SESSION', 'Failed to update session', error);
            this.showError(`Failed to update session: ${error.message}`);
        } finally {
            // Re-enable buttons
            saveBtn.disabled = false;
            if (cancelBtn) cancelBtn.disabled = false;
            if (deleteBtn) deleteBtn.disabled = false;
            this.showLoading(false);
            this.editingSessionId = null;
        }
    }

    showDeleteSessionConfirmation() {
        if (!this.editingSessionId) return;

        const session = this.sessions.get(this.editingSessionId);
        if (!session) return;

        // Hide edit modal first
        this.hideEditSessionModal();

        // Show delete confirmation modal
        const sessionName = session.name || this.editingSessionId;
        document.getElementById('delete-session-name').textContent = sessionName;
        const modalElement = document.getElementById('delete-session-modal');
        const modal = new bootstrap.Modal(modalElement);
        modal.show();
    }

    hideDeleteSessionModal() {
        const modalElement = document.getElementById('delete-session-modal');
        const modal = bootstrap.Modal.getInstance(modalElement);
        if (modal) {
            modal.hide();
        }
    }

    async confirmDeleteSession() {
        if (!this.editingSessionId) return;

        const sessionIdToDelete = this.editingSessionId;
        const confirmBtn = document.getElementById('confirm-delete-session');
        const cancelBtn = document.querySelector('#delete-session-modal .btn-secondary');

        try {
            // Disable both buttons to prevent double-clicks
            confirmBtn.disabled = true;
            if (cancelBtn) cancelBtn.disabled = true;

            this.showLoading(true);

            // Mark session as being deleted to prevent race conditions
            this.deletingSessions.add(sessionIdToDelete);

            // Remove from local sessions map immediately to prevent further operations
            this.sessions.delete(sessionIdToDelete);

            // Remove from orderedSessions array immediately
            this.orderedSessions = this.orderedSessions.filter(s => s.session_id !== sessionIdToDelete);

            // Remove from project's session_ids array to prevent reorder validation errors
            for (const project of this.projectManager.projects.values()) {
                if (project.session_ids && project.session_ids.includes(sessionIdToDelete)) {
                    project.session_ids = project.session_ids.filter(sid => sid !== sessionIdToDelete);
                    Logger.debug('SESSION', `Removed session ${sessionIdToDelete} from project ${project.project_id}`);
                    break;
                }
            }

            const response = await this.apiRequest(`/api/sessions/${sessionIdToDelete}`, {
                method: 'DELETE'
            });

            if (response.success) {
                // Clear cached input for deleted session
                this.sessionInputCache.delete(sessionIdToDelete);

                // Session successfully deleted
                this.hideDeleteSessionModal();

                // If this was the current session, exit it
                if (this.currentSessionId === sessionIdToDelete) {
                    this.exitSession();
                }

                // Refresh the sessions list
                this.renderSessions();

                Logger.info('SESSION', 'Session deleted successfully', sessionIdToDelete);
            } else {
                // Restore session to map and orderedSessions if deletion failed
                const sessionData = await this.apiRequest(`/api/sessions/${sessionIdToDelete}`).catch(() => null);
                if (sessionData && sessionData.session) {
                    this.sessions.set(sessionIdToDelete, sessionData.session);
                    // Re-add to orderedSessions if not already present
                    if (!this.orderedSessions.find(s => s.session_id === sessionIdToDelete)) {
                        this.orderedSessions.push(sessionData.session);
                    }
                    this.renderSessions();
                }
                throw new Error('Failed to delete session');
            }
        } catch (error) {
            Logger.error('SESSION', 'Failed to delete session', error);
            this.showError(`Failed to delete session: ${error.message}`);

            // Try to restore session to map and orderedSessions if deletion failed
            try {
                const sessionData = await this.apiRequest(`/api/sessions/${sessionIdToDelete}`);
                if (sessionData && sessionData.session) {
                    this.sessions.set(sessionIdToDelete, sessionData.session);
                    // Re-add to orderedSessions if not already present
                    if (!this.orderedSessions.find(s => s.session_id === sessionIdToDelete)) {
                        this.orderedSessions.push(sessionData.session);
                    }
                    this.renderSessions();
                }
            } catch (restoreError) {
                Logger.debug('SESSION', 'Could not restore session data after failed deletion');
            }
        } finally {
            // Re-enable buttons
            confirmBtn.disabled = false;
            if (cancelBtn) cancelBtn.disabled = false;

            // Always remove from deleting set
            this.deletingSessions.delete(sessionIdToDelete);
            this.showLoading(false);
            this.editingSessionId = null;
        }
    }

    // Utility Methods
    showLoading(show) {
        const overlay = document.getElementById('loading-overlay');
        if (show) {
            overlay.classList.remove('d-none');
        } else {
            overlay.classList.add('d-none');
        }
    }

    showError(message) {
        alert(`Error: ${message}`);
    }

    setInputControlsEnabled(enabled) {
        const messageInput = document.getElementById('message-input');
        const sendBtn = document.getElementById('send-btn');
        const interruptSendBtn = document.getElementById('interrupt-send-btn');

        // Always keep input enabled to allow queuing
        if (messageInput) {
            messageInput.disabled = false;
        }

        if (sendBtn) {
            sendBtn.disabled = !enabled;
        }

        if (interruptSendBtn) {
            interruptSendBtn.disabled = !enabled;
        }

        // Update placeholders based on session state
        const session = this.sessions.get(this.currentSessionId);
        if (session && session.state === 'error') {
            if (messageInput) {
                messageInput.disabled = true;
                messageInput.placeholder = "Session is in error state - input disabled";
            }
        } else if (this.isProcessing) {
            if (messageInput) {
                messageInput.placeholder = "Queue next message or interrupt...";
            }
        } else {
            if (messageInput) {
                messageInput.placeholder = "Type your message to Claude Code...";
            }
        }

        Logger.debug('UI', enabled ? 'Input controls enabled' : 'Input controls disabled');
    }

    // Auto-scroll functionality
    toggleAutoScroll() {
        this.autoScrollEnabled = !this.autoScrollEnabled;
        const button = document.getElementById('auto-scroll-toggle');

        if (this.autoScrollEnabled) {
            button.textContent = '📜 Auto-scroll: ON';
            button.className = 'btn btn-sm btn-outline-secondary';
            this.smartScrollToBottom();
        } else {
            button.textContent = '📜 Auto-scroll: OFF';
            button.className = 'btn btn-sm btn-secondary';
        }
    }

    handleScroll(event) {
        if (this.scrollTimeout) {
            clearTimeout(this.scrollTimeout);
        }

        // Only mark as user scrolling if they scroll away from the bottom
        if (!this.isAtBottom()) {
            this.isUserScrolling = true;
        } else {
            // If user scrolls to bottom, don't consider it user scrolling
            this.isUserScrolling = false;
        }

        // Reset user scrolling flag after a delay
        this.scrollTimeout = setTimeout(() => {
            this.isUserScrolling = false;
        }, 1500);
    }

    isAtBottom() {
        const messagesArea = document.getElementById('messages-area');
        const threshold = 100; // pixels from bottom
        return messagesArea.scrollTop + messagesArea.clientHeight >= messagesArea.scrollHeight - threshold;
    }

    smartScrollToBottom() {
        if (!this.autoScrollEnabled) {
            return;
        }

        // Always scroll if user is not actively scrolling
        // Or if user is scrolling but near the bottom
        if (!this.isUserScrolling || this.isAtBottom()) {
            // Use requestAnimationFrame for smoother scrolling
            requestAnimationFrame(() => {
                this.scrollToBottom();
            });
        }
    }

    scrollToBottom() {
        const messagesArea = document.getElementById('messages-area');
        messagesArea.scrollTop = messagesArea.scrollHeight;
    }

    // Message formatting utilities
    isJsonContent(content) {
        if (typeof content !== 'string') return false;
        try {
            JSON.parse(content);
            return content.trim().startsWith('{') || content.trim().startsWith('[');
        } catch {
            return false;
        }
    }

    tryParseJson(content) {
        try {
            return JSON.parse(content);
        } catch {
            return null;
        }
    }

    formatJson(obj) {
        try {
            return JSON.stringify(obj, null, 2);
        } catch {
            return String(obj);
        }
    }

    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    // Session Data Management Helper
    updateSessionData(sessionId, sessionInfo, skipRender = false) {
        // Update sessions Map
        this.sessions.set(sessionId, sessionInfo);

        // Update orderedSessions array
        const index = this.orderedSessions.findIndex(s => s.session_id === sessionId);
        if (index !== -1) {
            this.orderedSessions[index] = sessionInfo;
        } else {
            // Session not found in ordered list, add it
            Logger.warn('SESSION', 'Session not found in orderedSessions, adding it', sessionId);
            this.orderedSessions.push(sessionInfo);
        }

        // Optionally trigger re-render
        if (!skipRender) {
            this.renderSessions();
        }
    }

    // Drag and Drop Methods
    addDragDropListeners(sessionElement, sessionId, projectId) {
        // Store drag state
        if (!this.dragState) {
            this.dragState = {
                draggedElement: null,
                draggedSessionId: null,
                draggedProjectId: null,
                dropIndicator: null,
                insertBefore: false
            };
        }

        sessionElement.addEventListener('dragstart', (e) => {
            this.dragState.draggedElement = sessionElement;
            this.dragState.draggedSessionId = sessionId;
            this.dragState.draggedProjectId = projectId;
            sessionElement.classList.add('dragging');

            // Set drag data
            e.dataTransfer.effectAllowed = 'move';
            e.dataTransfer.setData('text/plain', sessionId);
        });

        sessionElement.addEventListener('dragend', (e) => {
            sessionElement.classList.remove('dragging');
            this.removeDragVisualEffects();
        });

        sessionElement.addEventListener('dragover', (e) => {
            // Only handle session drags, not project drags
            if (!this.dragState.draggedSessionId) {
                return; // Not dragging a session, ignore
            }

            // Check if target is in same project
            const targetProjectId = sessionElement.getAttribute('data-project-id');

            if (this.dragState.draggedProjectId !== targetProjectId) {
                // Cross-project drag - show not-allowed cursor
                e.dataTransfer.dropEffect = 'none';
                return;
            }

            e.preventDefault();
            e.dataTransfer.dropEffect = 'move';

            if (this.dragState.draggedElement && sessionElement !== this.dragState.draggedElement) {
                this.showDropIndicator(sessionElement, e);
            }
        });

        sessionElement.addEventListener('dragenter', (e) => {
            // Only handle session drags
            if (!this.dragState.draggedSessionId) {
                return;
            }
            const targetProjectId = sessionElement.getAttribute('data-project-id');
            if (this.dragState.draggedProjectId === targetProjectId) {
                e.preventDefault();
            }
        });

        sessionElement.addEventListener('dragleave', (e) => {
            // Only handle session drags
            if (!this.dragState.draggedSessionId) {
                return;
            }
            // Only hide indicator if we're actually leaving the element
            if (!sessionElement.contains(e.relatedTarget)) {
                this.hideDropIndicator();
            }
        });

        sessionElement.addEventListener('drop', (e) => {
            e.preventDefault();

            // Validate same project
            const targetProjectId = sessionElement.getAttribute('data-project-id');
            if (this.dragState.draggedProjectId !== targetProjectId) {
                Logger.warn('SESSION', 'Cannot move session to different project');
                this.removeDragVisualEffects();
                return;
            }

            this.handleSessionDrop(sessionElement, sessionId, projectId);
        });
    }

    showDropIndicator(targetElement, event) {
        this.hideDropIndicator();

        const rect = targetElement.getBoundingClientRect();
        const midpoint = rect.top + rect.height / 2;
        const insertBefore = event.clientY < midpoint;

        // Create drop indicator
        const indicator = document.createElement('div');
        indicator.className = 'drop-indicator';
        indicator.textContent = ''; // Empty line to show where drop will occur

        this.dragState.dropIndicator = indicator;
        this.dragState.insertBefore = insertBefore; // Store the insert position

        if (insertBefore) {
            targetElement.parentNode.insertBefore(indicator, targetElement);
        } else {
            targetElement.parentNode.insertBefore(indicator, targetElement.nextSibling);
        }
    }

    hideDropIndicator() {
        if (this.dragState.dropIndicator) {
            this.dragState.dropIndicator.remove();
            this.dragState.dropIndicator = null;
            this.dragState.insertBefore = false;
        }
    }

    removeDragVisualEffects() {
        this.hideDropIndicator();
        // Remove any other drag effects
        document.querySelectorAll('.session-item.dragging').forEach(el => {
            el.classList.remove('dragging');
        });
    }

    async handleSessionDrop(targetElement, targetSessionId, projectId) {
        if (!this.dragState.draggedSessionId || this.dragState.draggedSessionId === targetSessionId) {
            this.removeDragVisualEffects();
            return;
        }

        const draggedSessionId = this.dragState.draggedSessionId;

        try {
            // Get project to find its sessions
            const project = this.projectManager.getProject(projectId);
            if (!project) {
                throw new Error('Project not found');
            }

            // Calculate new order based on drop position (within project sessions only)
            const newOrder = this.calculateNewOrder(draggedSessionId, targetSessionId, project.session_ids);

            // Call project-specific reorder API
            await this.reorderProjectSessions(projectId, newOrder);

        } catch (error) {
            Logger.error('SESSION', 'Failed to reorder sessions', error);
            this.showError('Failed to reorder sessions');
        } finally {
            this.removeDragVisualEffects();
        }
    }

    calculateNewOrder(draggedSessionId, targetSessionId, projectSessionIds) {
        // Get current session IDs in order (from project)
        const currentOrder = [...projectSessionIds];

        // Remove dragged session from current position
        const filteredOrder = currentOrder.filter(id => id !== draggedSessionId);

        // Find target position
        const targetIndex = filteredOrder.indexOf(targetSessionId);

        // Use the stored insert position from showDropIndicator
        const insertBefore = this.dragState.insertBefore || false;

        // Insert dragged session at new position
        const newIndex = insertBefore ? targetIndex : targetIndex + 1;
        filteredOrder.splice(newIndex, 0, draggedSessionId);

        return filteredOrder;
    }

    async reorderProjectSessions(projectId, sessionIds) {
        try {
            await this.projectManager.reorderSessionsInProject(projectId, sessionIds);

            // Update local project cache
            const project = this.projectManager.getProject(projectId);
            if (project) {
                project.session_ids = sessionIds;
            }

            // Re-render to show new order
            await this.renderSessions();

            Logger.info('SESSION', `Reordered sessions in project ${projectId}`);
        } catch (error) {
            Logger.error('SESSION', 'Failed to reorder sessions in project', error);
            throw error;
        }
    }

    // Project Drag and Drop Methods
    addProjectDragListeners(projectElement, projectId) {
        // Initialize project drag state if needed
        if (!this.projectDragState) {
            this.projectDragState = {
                draggedElement: null,
                draggedProjectId: null,
                dropIndicator: null,
                insertBefore: false,
                isReordering: false
            };
        }

        projectElement.addEventListener('dragstart', (e) => {
            // Only handle if drag started on project element itself (not child sessions)
            if (e.target !== projectElement && !e.target.closest('.accordion-header')) {
                return;
            }

            e.stopPropagation(); // Prevent bubbling to session handlers

            this.projectDragState.draggedElement = projectElement;
            this.projectDragState.draggedProjectId = projectId;
            projectElement.classList.add('dragging');

            e.dataTransfer.effectAllowed = 'move';
            e.dataTransfer.setData('text/plain', projectId);
        });

        projectElement.addEventListener('dragover', (e) => {
            // Only handle project drags, not session drags
            if (!this.projectDragState.draggedProjectId) {
                return; // Not dragging a project, ignore
            }

            e.preventDefault();
            e.dataTransfer.dropEffect = 'move';

            if (this.projectDragState.draggedProjectId === projectId) {
                return;
            }

            this.showProjectDropIndicator(projectElement, e.clientY);
        });

        projectElement.addEventListener('dragenter', (e) => {
            // Only handle project drags
            if (!this.projectDragState.draggedProjectId) {
                return;
            }
            e.preventDefault();
        });

        projectElement.addEventListener('dragleave', (e) => {
            // Only handle project drags
            if (!this.projectDragState.draggedProjectId) {
                return;
            }
            if (e.target === projectElement) {
                this.removeProjectDropIndicator();
            }
        });

        projectElement.addEventListener('drop', async (e) => {
            e.preventDefault();
            e.stopPropagation();

            const draggedProjectId = this.projectDragState.draggedProjectId;

            // Prevent duplicate drop events and self-drops
            if (!draggedProjectId || draggedProjectId === projectId || this.projectDragState.isReordering) {
                this.removeProjectDragEffects();
                return;
            }

            this.projectDragState.isReordering = true;

            try {
                const newOrder = this.calculateNewProjectOrder(draggedProjectId, projectId);

                // Check if order actually changed
                const currentOrder = this.projectManager.orderedProjects;
                const orderChanged = JSON.stringify(currentOrder) !== JSON.stringify(newOrder);

                if (!orderChanged) {
                    Logger.debug('PROJECT', 'Project order unchanged, skipping API call');
                    this.projectDragState.isReordering = false;
                    this.removeProjectDragEffects();
                    return;
                }

                await this.projectManager.reorderProjects(newOrder);
                await this.renderSessions();

                Logger.info('PROJECT', `Reordered projects successfully`);
            } catch (error) {
                Logger.error('PROJECT', 'Failed to reorder projects', error);
                this.showError('Failed to reorder projects');
            } finally {
                this.projectDragState.isReordering = false;
                this.removeProjectDragEffects();
            }
        });

        projectElement.addEventListener('dragend', () => {
            this.removeProjectDragEffects();
        });
    }

    showProjectDropIndicator(projectElement, clientY) {
        this.removeProjectDropIndicator();

        const rect = projectElement.getBoundingClientRect();
        const midpoint = rect.top + rect.height / 2;
        const insertBefore = clientY < midpoint;

        this.projectDragState.insertBefore = insertBefore;

        const indicator = document.createElement('div');
        indicator.className = 'drop-indicator';
        this.projectDragState.dropIndicator = indicator;

        if (insertBefore) {
            projectElement.parentNode.insertBefore(indicator, projectElement);
        } else {
            projectElement.parentNode.insertBefore(indicator, projectElement.nextSibling);
        }
    }

    removeProjectDropIndicator() {
        if (this.projectDragState.dropIndicator) {
            this.projectDragState.dropIndicator.remove();
            this.projectDragState.dropIndicator = null;
        }
    }

    removeProjectDragEffects() {
        if (this.projectDragState.draggedElement) {
            this.projectDragState.draggedElement.classList.remove('dragging');
        }
        this.removeProjectDropIndicator();
        this.projectDragState.draggedElement = null;
        this.projectDragState.draggedProjectId = null;
    }

    calculateNewProjectOrder(draggedProjectId, targetProjectId) {
        const currentOrder = [...this.projectManager.orderedProjects];

        Logger.debug('PROJECT', `Current order: ${JSON.stringify(currentOrder)}`);
        Logger.debug('PROJECT', `Dragging ${draggedProjectId} to ${targetProjectId}`);

        const filteredOrder = currentOrder.filter(id => id !== draggedProjectId);
        const targetIndex = filteredOrder.indexOf(targetProjectId);

        const insertBefore = this.projectDragState.insertBefore || false;
        const newIndex = insertBefore ? targetIndex : targetIndex + 1;
        filteredOrder.splice(newIndex, 0, draggedProjectId);

        Logger.debug('PROJECT', `New order: ${JSON.stringify(filteredOrder)}`);

        return filteredOrder;
    }

    async reorderSessions(sessionIds) {
        // Legacy method - no longer used with project-based organization
        Logger.warn('SESSION', 'reorderSessions called - this method is deprecated in favor of reorderProjectSessions');
    }

    // Sidebar Management
    toggleSidebar() {
        const sidebar = document.getElementById('sidebar');
        const backdrop = document.getElementById('sidebar-backdrop');
        const toggleBtn = document.getElementById('sidebar-toggle-btn');
        const toggleIcon = document.getElementById('sidebar-toggle-icon');
        const isMobile = window.innerWidth < 768;

        this.sidebarCollapsed = !this.sidebarCollapsed;

        if (isMobile) {
            // Mobile: Use overlay mode
            if (this.sidebarCollapsed) {
                // Close mobile overlay
                sidebar.classList.remove('mobile-open');
                backdrop.classList.remove('show');
                toggleBtn.title = 'Show sidebar';
            } else {
                // Open mobile overlay
                sidebar.classList.add('mobile-open');
                backdrop.classList.add('show');
                toggleBtn.title = 'Hide sidebar';
            }
        } else {
            // Desktop: Use hide/show mode
            if (this.sidebarCollapsed) {
                sidebar.classList.add('d-none');
                toggleBtn.title = 'Show sidebar';
            } else {
                sidebar.classList.remove('d-none');
                toggleBtn.title = 'Hide sidebar';
            }
        }
    }

    startResize(e) {
        // Don't allow resize when collapsed or on mobile
        if (this.sidebarCollapsed || window.innerWidth < 768) return;

        this.isResizing = true;
        document.addEventListener('mousemove', this.handleResize.bind(this));
        document.addEventListener('mouseup', this.stopResize.bind(this));
        e.preventDefault();
    }

    handleResize(e) {
        if (!this.isResizing) return;

        const sidebar = document.getElementById('sidebar');
        // Get the main content container (parent of sidebar)
        const containerRect = sidebar.parentElement.getBoundingClientRect();
        const newWidth = e.clientX - containerRect.left;

        // Enforce constraints: min 200px, max 30% of viewport width
        const minWidth = 200;
        const maxWidth = window.innerWidth * 0.3;
        const constrainedWidth = Math.max(minWidth, Math.min(newWidth, maxWidth));

        this.sidebarWidth = constrainedWidth;
        sidebar.style.width = `${constrainedWidth}px`;
    }

    stopResize() {
        this.isResizing = false;
        document.removeEventListener('mousemove', this.handleResize.bind(this));
        document.removeEventListener('mouseup', this.stopResize.bind(this));
    }

    handleWindowResize() {
        const sidebar = document.getElementById('sidebar');
        const isMobile = window.innerWidth < 768;
        const wasMobile = this.sidebarCollapsed && sidebar.classList.contains('mobile-open');

        // Sync collapsed state when crossing mobile/desktop breakpoint
        if (isMobile && !this.sidebarCollapsed) {
            // Switched to mobile - collapse sidebar
            this.sidebarCollapsed = true;
            sidebar.classList.remove('mobile-open');
            document.getElementById('sidebar-backdrop').classList.remove('show');
        } else if (!isMobile && this.sidebarCollapsed && !wasMobile) {
            // Switched to desktop - uncollapse sidebar if it was auto-collapsed for mobile
            this.sidebarCollapsed = false;
            sidebar.classList.remove('d-none');
        }

        // Desktop sidebar width constraint
        if (!this.sidebarCollapsed && !isMobile) {
            const maxWidth = window.innerWidth * 0.3;

            // Ensure sidebar doesn't exceed 30% of new window width
            if (this.sidebarWidth > maxWidth) {
                this.sidebarWidth = maxWidth;
                sidebar.style.width = `${maxWidth}px`;
            }
        }
    }

    autoExpandTextarea(textarea) {
        // Reset height to allow shrinking
        textarea.style.height = 'auto';
        // Set to scroll height (content height)
        textarea.style.height = textarea.scrollHeight + 'px';
    }

    resetTextareaHeight(textarea) {
        textarea.style.height = 'auto';
    }

    showToast(message, type = 'info') {
        // Create toast container if it doesn't exist
        let toastContainer = document.getElementById('toast-container');
        if (!toastContainer) {
            toastContainer = document.createElement('div');
            toastContainer.id = 'toast-container';
            toastContainer.className = 'toast-container position-fixed top-0 end-0 p-3';
            toastContainer.style.zIndex = '9999';
            document.body.appendChild(toastContainer);
        }

        // Create toast element
        const toastEl = document.createElement('div');
        toastEl.className = `toast align-items-center text-bg-${type} border-0`;
        toastEl.setAttribute('role', 'alert');
        toastEl.setAttribute('aria-live', 'assertive');
        toastEl.setAttribute('aria-atomic', 'true');

        toastEl.innerHTML = `
            <div class="d-flex">
                <div class="toast-body">
                    ${this.escapeHtml(message)}
                </div>
                <button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast" aria-label="Close"></button>
            </div>
        `;

        toastContainer.appendChild(toastEl);

        // Initialize and show toast using Bootstrap
        const toast = new bootstrap.Toast(toastEl, {
            autohide: true,
            delay: 3000
        });

        toast.show();

        // Remove toast element after it's hidden
        toastEl.addEventListener('hidden.bs.toast', () => {
            toastEl.remove();
        });
    }
}

// Initialize the application when the DOM is loaded
document.addEventListener('DOMContentLoaded', () => {
    window.claudeWebUI = new ClaudeWebUI();
    window.app = window.claudeWebUI; // Make app available globally for onclick handlers
});

// Handle page unload
window.addEventListener('beforeunload', () => {
    if (window.claudeWebUI) {
        if (window.claudeWebUI.sessionWebsocket) {
            window.claudeWebUI.disconnectSessionWebSocket();
        }
        if (window.claudeWebUI.uiWebsocket) {
            window.claudeWebUI.uiWebsocket.close();
        }
    }
});