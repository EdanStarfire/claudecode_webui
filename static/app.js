// Claude Code WebUI JavaScript Application

class ClaudeWebUI {
    constructor() {
        this.currentSessionId = null;
        this.sessions = new Map();

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
        this.sidebarCollapsed = false;
        this.sidebarWidth = 300;
        this.isResizing = false;

        // Session deletion state tracking
        this.deletingSessions = new Set();

        this.init();
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

    init() {
        this.setupEventListeners();
        this.connectUIWebSocket();
        this.updateConnectionStatus('disconnected');
    }

    setupEventListeners() {
        // Session controls
        document.getElementById('create-session-btn').addEventListener('click', () => this.showCreateSessionModal());
        document.getElementById('refresh-sessions-btn').addEventListener('click', () => this.refreshSessions());

        // Modal controls
        document.getElementById('close-modal').addEventListener('click', () => this.hideCreateSessionModal());
        document.getElementById('cancel-create').addEventListener('click', () => this.hideCreateSessionModal());
        document.getElementById('create-session-form').addEventListener('submit', (e) => this.handleCreateSession(e));

        // Browse directory button
        document.getElementById('browse-directory').addEventListener('click', () => this.browseDirectory());

        // Session actions
        document.getElementById('delete-session-btn').addEventListener('click', () => this.showDeleteSessionModal());
        document.getElementById('exit-session-btn').addEventListener('click', () => this.exitSession());

        // Delete modal controls
        document.getElementById('close-delete-modal').addEventListener('click', () => this.hideDeleteSessionModal());
        document.getElementById('cancel-delete').addEventListener('click', () => this.hideDeleteSessionModal());
        document.getElementById('confirm-delete').addEventListener('click', () => this.confirmDeleteSession());

        // Sidebar controls
        document.getElementById('sidebar-collapse-btn').addEventListener('click', () => this.toggleSidebar());
        document.getElementById('sidebar-resize-handle').addEventListener('mousedown', (e) => this.startResize(e));

        // Message sending
        document.getElementById('send-btn').addEventListener('click', () => this.sendMessage());
        document.getElementById('message-input').addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                this.sendMessage();
            }
        });

        // Auto-scroll toggle
        document.getElementById('auto-scroll-toggle').addEventListener('click', () => this.toggleAutoScroll());

        // Messages area scroll detection
        document.getElementById('messages-area').addEventListener('scroll', (e) => this.handleScroll(e));

        // Modal click outside to close
        document.getElementById('create-session-modal').addEventListener('click', (e) => {
            if (e.target.id === 'create-session-modal') {
                this.hideCreateSessionModal();
            }
        });

        document.getElementById('delete-session-modal').addEventListener('click', (e) => {
            if (e.target.id === 'delete-session-modal') {
                this.hideDeleteSessionModal();
            }
        });

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
            console.error('API request failed:', error);
            this.showError(`API request failed: ${error.message}`);
            throw error;
        }
    }

    // Session Management
    async loadSessions() {
        try {
            this.showLoading(true);
            const data = await this.apiRequest('/api/sessions');
            this.sessions.clear();

            data.sessions.forEach(session => {
                this.sessions.set(session.session_id, session);
            });

            this.renderSessions();
        } catch (error) {
            console.error('Failed to load sessions:', error);
        } finally {
            this.showLoading(false);
        }
    }

    async createSession(formData) {
        try {
            this.showLoading(true);

            const tools = formData.tools ? formData.tools.split(',').map(t => t.trim()).filter(t => t) : [];

            const payload = {
                working_directory: formData.working_directory,
                permissions: formData.permissions,
                system_prompt: formData.system_prompt || null,
                tools: tools,
                model: formData.model || null
            };

            const data = await this.apiRequest('/api/sessions', {
                method: 'POST',
                body: JSON.stringify(payload)
            });

            // No need to call loadSessions() - UI WebSocket will receive state change notification automatically
            await this.selectSession(data.session_id);

            return data.session_id;
        } catch (error) {
            console.error('Failed to create session:', error);
            throw error;
        } finally {
            this.showLoading(false);
        }
    }

    exitSession() {
        if (!this.currentSessionId) return;

        console.log(`Exiting session ${this.currentSessionId}`);

        // Clean disconnect from WebSocket
        this.disconnectSessionWebSocket();

        // Clear current session
        this.currentSessionId = null;

        // Reset UI to no session selected state
        document.getElementById('no-session-selected').classList.remove('hidden');
        document.getElementById('chat-container').classList.add('hidden');

        // Remove active state from all session items
        document.querySelectorAll('.session-item').forEach(item => {
            item.classList.remove('active');
        });

        // Clear messages area
        document.getElementById('messages-area').innerHTML = '';

        // Reset processing state when exiting session
        this.hideProcessingIndicator();

        // Re-enable input controls when exiting session
        this.setInputControlsEnabled(true);
    }

    async sendMessage() {
        const input = document.getElementById('message-input');
        const message = input.value.trim();

        if (!message || !this.currentSessionId || this.isProcessing) return;

        try {
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
        } catch (error) {
            console.error('Failed to send message:', error);
        }
    }

    showProcessingIndicator() {
        this.isProcessing = true;
        const progressElement = document.getElementById('claude-progress');
        const sendButton = document.getElementById('send-btn');
        const messageInput = document.getElementById('message-input');

        if (progressElement) {
            progressElement.classList.remove('hidden');
        }
        if (sendButton) {
            sendButton.disabled = true;
            sendButton.textContent = 'Processing...';
        }
        if (messageInput) {
            messageInput.disabled = true;
            // Ensure processing state styling, not error state styling
            messageInput.placeholder = "Processing...";
        }
    }

    hideProcessingIndicator() {
        this.isProcessing = false;
        const progressElement = document.getElementById('claude-progress');
        const sendButton = document.getElementById('send-btn');

        if (progressElement) {
            progressElement.classList.add('hidden');
        }
        if (sendButton) {
            sendButton.textContent = 'Send';
        }

        // Re-enable controls only if current session is not in error state
        this.updateControlsBasedOnSessionState();
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
        // Filter out init system messages
        if (message.type === 'system' && message.subtype === 'init') {
            console.log('Filtering out init system message with subtype:', message.subtype);
            return false;
        }

        // Filter out result messages
        if (message.type === 'result') {
            console.log('Filtering out result message');
            return false;
        }

        // Allow client_launched system messages to be displayed
        if (message.type === 'system' && message.subtype === 'client_launched') {
            console.log('Displaying client launched system message');
            return true;
        }

        // Display all other message types
        return true;
    }

    handleIncomingMessage(message) {
        console.log('Processing incoming message:', message);

        // Handle progress indicator for init messages
        if (message.type === 'system' && message.subtype === 'init') {
            // Don't show init messages, just ensure progress indicator is visible
            if (!this.isProcessing) {
                this.showProcessingIndicator();
            }
        }

        // Processing state is now handled by backend - no manual indicator management needed

        // Use the unified filtering logic to determine if message should be displayed
        if (this.shouldDisplayMessage(message)) {
            console.log('Adding message to UI:', message.type);
            this.addMessageToUI(message);
        }
    }

    async loadSessionInfo() {
        if (!this.currentSessionId) return;

        // Skip if this session is being deleted
        if (this.deletingSessions.has(this.currentSessionId)) {
            console.log(`Skipping loadSessionInfo for session ${this.currentSessionId} - deletion in progress`);
            return;
        }

        // Skip if session no longer exists in our local map
        if (!this.sessions.has(this.currentSessionId)) {
            console.log(`Skipping loadSessionInfo for session ${this.currentSessionId} - not in local sessions map`);
            return;
        }

        try {
            const data = await this.apiRequest(`/api/sessions/${this.currentSessionId}`);
            this.updateSessionInfo(data);
        } catch (error) {
            // If it's a 404, the session was likely deleted - handle gracefully
            if (error.message.includes('404')) {
                console.log(`Session ${this.currentSessionId} not found (404) - likely deleted, clearing from UI`);
                this.handleSessionDeleted(this.currentSessionId);
            } else {
                console.error('Failed to load session info:', error);
            }
        }
    }

    async loadMessages() {
        if (!this.currentSessionId) return;

        try {
            console.log('Loading all messages with pagination...');
            const allMessages = [];
            let offset = 0;
            const pageSize = 50;
            let hasMore = true;

            // Load all messages using pagination
            while (hasMore) {
                console.log(`Loading messages page: offset=${offset}, limit=${pageSize}`);
                const response = await this.apiRequest(
                    `/api/sessions/${this.currentSessionId}/messages?limit=${pageSize}&offset=${offset}`
                );

                // Add messages from this page
                allMessages.push(...response.messages);

                // Check if there are more pages
                hasMore = response.has_more;
                offset += pageSize;

                console.log(`Loaded ${response.messages.length} messages, total so far: ${allMessages.length}, has_more: ${hasMore}`);
            }

            console.log(`Finished loading all ${allMessages.length} messages`);
            this.renderMessages(allMessages);
        } catch (error) {
            console.error('Failed to load messages:', error);
        }
    }

    // UI WebSocket Management (for session state updates)
    connectUIWebSocket() {
        if (this.uiWebsocket && this.uiWebsocket.readyState === WebSocket.OPEN) {
            console.log('UI WebSocket already connected');
            return;
        }

        console.log('Connecting to UI WebSocket...');
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${protocol}//${window.location.host}/ws/ui`;

        this.uiWebsocket = new WebSocket(wsUrl);

        this.uiWebsocket.onopen = () => {
            console.log('UI WebSocket connected successfully');
            this.uiConnectionRetryCount = 0;
        };

        this.uiWebsocket.onmessage = (event) => {
            try {
                const data = JSON.parse(event.data);
                this.handleUIWebSocketMessage(data);
            } catch (error) {
                console.error('Error parsing UI WebSocket message:', error);
            }
        };

        this.uiWebsocket.onclose = (event) => {
            console.log('UI WebSocket disconnected', event.code, event.reason);
            this.uiWebsocket = null;

            // Auto-reconnect UI WebSocket (it should always stay connected)
            if (this.uiConnectionRetryCount < this.maxUIRetries) {
                this.uiConnectionRetryCount++;
                const delay = Math.min(1000 * Math.pow(2, this.uiConnectionRetryCount), 30000);
                console.log(`Reconnecting UI WebSocket in ${delay}ms (attempt ${this.uiConnectionRetryCount}/${this.maxUIRetries})`);

                setTimeout(() => {
                    this.connectUIWebSocket();
                }, delay);
            } else {
                console.log('Max UI WebSocket reconnection attempts reached');
            }
        };

        this.uiWebsocket.onerror = (error) => {
            console.error('UI WebSocket error:', error);
        };
    }

    handleUIWebSocketMessage(data) {
        console.log('UI WebSocket message received:', data.type);

        switch (data.type) {
            case 'sessions_list':
                // Initial sessions list on connection
                this.updateSessionsList(data.data.sessions);
                break;
            case 'state_change':
                // Real-time session state change
                this.handleStateChange(data.data);
                break;
            case 'ping':
                // Respond to server ping
                if (this.uiWebsocket && this.uiWebsocket.readyState === WebSocket.OPEN) {
                    this.uiWebsocket.send(JSON.stringify({type: 'pong', timestamp: new Date().toISOString()}));
                }
                break;
            case 'pong':
                // Server responded to our ping
                console.debug('UI WebSocket pong received');
                break;
            default:
                console.log('Unknown UI WebSocket message type:', data.type);
        }
    }

    updateSessionsList(sessions) {
        console.log(`Updating sessions list with ${sessions.length} sessions`);
        this.sessions.clear();
        sessions.forEach(session => {
            this.sessions.set(session.session_id, session);
        });
        this.renderSessions();
    }

    refreshSessions() {
        console.log('Refreshing sessions via API fallback');
        // Fallback to API call if UI WebSocket is not available
        this.loadSessions();
    }

    // Session WebSocket Management (for message streaming)
    connectSessionWebSocket() {
        if (!this.currentSessionId) return;

        // Only disconnect if we have an existing connection to a different session
        if (this.sessionWebsocket && this.sessionWebsocket.readyState === WebSocket.OPEN) {
            console.log('Closing existing session WebSocket connection before creating new one');
            this.disconnectSessionWebSocket();
        }

        // Reset intentional disconnect flag for new connections
        this.intentionalSessionDisconnect = false;

        console.log(`Connecting session WebSocket for session: ${this.currentSessionId}`);
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${protocol}//${window.location.host}/ws/session/${this.currentSessionId}`;

        try {
            this.sessionWebsocket = new WebSocket(wsUrl);

            this.sessionWebsocket.onopen = () => {
                console.log('WebSocket connected');
                this.updateConnectionStatus('connected');
                this.sessionConnectionRetryCount = 0;
            };

            this.sessionWebsocket.onmessage = (event) => {
                try {
                    const data = JSON.parse(event.data);
                    this.handleWebSocketMessage(data);
                } catch (error) {
                    console.error('Failed to parse WebSocket message:', error);
                }
            };

            this.sessionWebsocket.onclose = (event) => {
                console.log('WebSocket disconnected', event.code, event.reason);
                this.updateConnectionStatus('disconnected');

                // Don't retry if this was an intentional disconnect
                if (this.intentionalSessionDisconnect) {
                    console.log('WebSocket closed intentionally, not retrying');
                    return;
                }

                // Don't retry on specific error codes (session invalid/inactive)
                if (event.code === 4404 || event.code === 4003 || event.code === 4500) {
                    console.log(`WebSocket closed with error code ${event.code}, not retrying`);
                    return;
                }

                this.scheduleReconnect();
            };

            this.sessionWebsocket.onerror = (error) => {
                console.error('WebSocket error:', error);
                this.updateConnectionStatus('disconnected');
            };

        } catch (error) {
            console.error('Failed to create WebSocket:', error);
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
            console.log('Reconnect cancelled due to intentional disconnect');
            return;
        }

        if (this.sessionConnectionRetryCount < this.maxSessionRetries) {
            this.sessionConnectionRetryCount++;
            const delay = Math.min(1000 * Math.pow(2, this.sessionConnectionRetryCount), 30000);

            console.log(`Scheduling WebSocket reconnect in ${delay}ms (attempt ${this.sessionConnectionRetryCount})`);
            setTimeout(() => {
                if (this.currentSessionId && !this.intentionalSessionDisconnect) {
                    this.connectSessionWebSocket();
                }
            }, delay);
        } else {
            console.log('Max reconnection attempts reached');
        }
    }

    handleWebSocketMessage(data) {
        console.log('WebSocket message received:', data);

        switch (data.type) {
            case 'message':
                this.handleIncomingMessage(data.data);
                break;
            case 'state_change':
                this.handleStateChange(data.data);
                break;
            case 'connection_established':
                console.log('WebSocket connection confirmed for session:', data.session_id);
                break;
            case 'ping':
                // Respond to server ping to keep connection alive
                if (this.websocket && this.sessionWebsocket.readyState === WebSocket.OPEN) {
                    this.sessionWebsocket.send(JSON.stringify({type: 'pong', timestamp: new Date().toISOString()}));
                }
                break;
            default:
                console.log('Unknown WebSocket message type:', data.type);
        }
    }

    // UI Updates
    async selectSession(sessionId) {
        // If already connected to this session, don't reconnect
        if (this.currentSessionId === sessionId && this.websocket && this.sessionWebsocket.readyState === WebSocket.OPEN) {
            console.log(`Already connected to session ${sessionId}`);
            return;
        }

        // Clean disconnect from previous session
        if (this.currentSessionId && this.currentSessionId !== sessionId) {
            console.log(`Switching from session ${this.currentSessionId} to ${sessionId}`);
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
        document.getElementById('no-session-selected').classList.add('hidden');
        document.getElementById('chat-container').classList.remove('hidden');

        // Load session info first to check state
        await this.loadSessionInfo();

        // Check if session needs to be started or is ready for use
        const session = this.sessions.get(sessionId);
        if (session) {
            if (session.state === 'error') {
                // Session is in error state, skip WebSocket initialization
                console.log(`Session ${sessionId} is in error state, skipping WebSocket connection`);
                // Just load messages without attempting to connect
            } else if (session.state === 'active' || session.state === 'running') {
                // Session is already active, just connect WebSocket
                console.log(`Session ${sessionId} is already active, connecting WebSocket`);
                this.connectSessionWebSocket();
            } else if (session.state === 'starting') {
                // Session is starting, wait for it to become active
                console.log(`Session ${sessionId} is starting, waiting for it to become active...`);
                let attempts = 0;
                const maxAttempts = 15;
                const pollInterval = 1000;
                while (attempts < maxAttempts) {
                    await new Promise(resolve => setTimeout(resolve, pollInterval));
                    await this.loadSessionInfo();
                    const updatedSession = this.sessions.get(sessionId);
                    if (updatedSession && updatedSession.state === 'error') {
                        console.log(`Session ${sessionId} entered error state during startup, stopping wait`);
                        break;
                    } else if (updatedSession && (updatedSession.state === 'active' || updatedSession.state === 'running')) {
                        console.log(`Session ${sessionId} is now active, connecting WebSocket`);
                        this.connectSessionWebSocket();
                        break;
                    }
                    attempts++;
                    console.log(`Waiting for session ${sessionId} to become active... (attempt ${attempts}/${maxAttempts})`);
                }

                if (attempts >= maxAttempts) {
                    console.warn(`Session ${sessionId} did not become active after ${maxAttempts} attempts (${maxAttempts * pollInterval / 1000} seconds)`);
                }
            } else {
                // Session needs to be started (both fresh sessions and existing sessions)
                // The server-side logic will handle whether to create fresh or resume based on claude_code_session_id
                console.log(`Starting session ${sessionId} (current state: ${session.state})`);
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
                        console.log(`Session ${sessionId} entered error state during startup, stopping wait`);
                        break;
                    } else if (updatedSession && (updatedSession.state === 'active' || updatedSession.state === 'running')) {
                        console.log(`Session ${sessionId} is now active, connecting WebSocket`);
                        this.connectSessionWebSocket();
                        break;
                    }
                    attempts++;
                    console.log(`Waiting for session ${sessionId} to become active... (attempt ${attempts}/${maxAttempts})`);
                }

                if (attempts >= maxAttempts) {
                    console.warn(`Session ${sessionId} did not become active after ${maxAttempts} attempts (${maxAttempts * pollInterval / 1000} seconds)`);
                }
            }
        }

        // Load messages after session is ready
        this.loadMessages();

        // Load session info to get current processing state from backend
        this.loadSessionInfo();
    }

    renderSessions() {
        const container = document.getElementById('sessions-container');
        container.innerHTML = '';

        if (this.sessions.size === 0) {
            container.innerHTML = '<p class="text-muted">No sessions available</p>';
            return;
        }

        this.sessions.forEach((session, sessionId) => {
            const sessionElement = document.createElement('div');
            sessionElement.className = 'session-item';

            // Add active class if this is the currently selected session
            if (sessionId === this.currentSessionId) {
                sessionElement.classList.add('active');
            }

            sessionElement.setAttribute('data-session-id', sessionId);
            sessionElement.addEventListener('click', (e) => {
                // Don't select session if clicking on input field
                if (e.target.tagName === 'INPUT') return;
                this.selectSession(sessionId);
            });

            // Create status indicator - show processing state if is_processing is true
            const isProcessing = session.is_processing || false;
            const displayState = isProcessing ? 'processing' : session.state;
            const statusIndicator = this.createStatusIndicator(displayState, 'session', session.state);

            // Use session name if available, fallback to session ID
            const displayName = session.name || sessionId;

            sessionElement.innerHTML = `
                <div class="session-header">
                    <div class="session-name" title="${sessionId}">
                        <span class="session-name-display">${this.escapeHtml(displayName)}</span>
                        <input class="session-name-edit" type="text" value="${this.escapeHtml(displayName)}" style="display: none;">
                    </div>
                </div>
            `;

            // Insert status indicator at the beginning
            const sessionHeader = sessionElement.querySelector('.session-header');
            sessionHeader.insertBefore(statusIndicator, sessionHeader.firstChild);

            // Add double-click editing functionality
            const nameDisplay = sessionElement.querySelector('.session-name-display');
            const nameInput = sessionElement.querySelector('.session-name-edit');

            nameDisplay.addEventListener('dblclick', (e) => {
                e.stopPropagation();
                this.startEditingSessionName(sessionId, nameDisplay, nameInput);
            });

            this.setupSessionNameInput(sessionId, nameDisplay, nameInput);

            container.appendChild(sessionElement);
        });
    }

    startEditingSessionName(sessionId, nameDisplay, nameInput) {
        // Hide display, show input
        nameDisplay.style.display = 'none';
        nameInput.style.display = 'inline-block';
        nameInput.focus();
        nameInput.select();
    }

    setupSessionNameInput(sessionId, nameDisplay, nameInput) {
        // Handle Enter key to save
        nameInput.addEventListener('keydown', (e) => {
            e.stopPropagation();
            if (e.key === 'Enter') {
                this.saveSessionName(sessionId, nameDisplay, nameInput);
            } else if (e.key === 'Escape') {
                this.cancelEditingSessionName(nameDisplay, nameInput);
            }
        });

        // Handle click outside to cancel
        nameInput.addEventListener('blur', () => {
            this.cancelEditingSessionName(nameDisplay, nameInput);
        });
    }

    async saveSessionName(sessionId, nameDisplay, nameInput) {
        const newName = nameInput.value.trim();
        if (!newName) {
            this.cancelEditingSessionName(nameDisplay, nameInput);
            return;
        }

        try {
            const response = await this.apiRequest(`/api/sessions/${sessionId}/name`, {
                method: 'PUT',
                body: JSON.stringify({ name: newName })
            });

            if (response.success) {
                // Update local session data
                if (this.sessions.has(sessionId)) {
                    const session = this.sessions.get(sessionId);
                    session.name = newName;
                    this.sessions.set(sessionId, session);
                }

                // Update display
                nameDisplay.textContent = newName;
                nameDisplay.style.display = 'inline-block';
                nameInput.style.display = 'none';

                // Update header if this is the current session
                if (sessionId === this.currentSessionId) {
                    this.updateSessionHeaderName(newName);
                }
            } else {
                throw new Error('Failed to update session name');
            }
        } catch (error) {
            console.error('Failed to save session name:', error);
            this.cancelEditingSessionName(nameDisplay, nameInput);
            this.showError('Failed to update session name');
        }
    }

    cancelEditingSessionName(nameDisplay, nameInput) {
        // Reset input value to original
        nameInput.value = nameDisplay.textContent;
        // Show display, hide input
        nameDisplay.style.display = 'inline-block';
        nameInput.style.display = 'none';
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
            errorMessageElement.classList.remove('hidden');
            sessionInfoBar.classList.add('error');
            console.log('Displaying error message in top bar:', sessionData.session.error_message);

            // For error state: clear any processing indicator and disable input controls
            this.updateProcessingState(false);
            this.setInputControlsEnabled(false);
        } else {
            // Hide error message and remove error styling
            errorMessageElement.classList.add('hidden');
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
            this.sessions.set(this.currentSessionId, existingSession);
        }
    }

    renderMessages(messages) {
        const messagesArea = document.getElementById('messages-area');
        messagesArea.innerHTML = '';

        messages.forEach(message => {
            // Apply the same filtering logic used for live messages
            if (this.shouldDisplayMessage(message)) {
                this.addMessageToUI(message, false);
            }
        });

        this.smartScrollToBottom();
    }

    addMessageToUI(message, scroll = true) {
        const messagesArea = document.getElementById('messages-area');
        const messageElement = document.createElement('div');
        messageElement.className = `message ${message.type}`;

        const timestamp = new Date(message.timestamp).toLocaleTimeString();

        // Enhanced message content with JSON display for system/tool messages
        let contentHtml = '';
        const content = message.content || '';

        if (message.type === 'system' || message.type === 'result' || this.isJsonContent(content)) {
            // Show both text content and JSON data
            if (content) {
                contentHtml += `<div class="message-content">${this.escapeHtml(content)}</div>`;
            }

            // Show JSON blob if available
            if (message.sdk_message || this.isJsonContent(content)) {
                const jsonData = message.sdk_message || this.tryParseJson(content);
                if (jsonData) {
                    contentHtml += `<div class="message-json">${this.formatJson(jsonData)}</div>`;
                }
            }
        } else {
            // Regular text content
            contentHtml = `<div class="message-content">${this.escapeHtml(content)}</div>`;
        }

        messageElement.innerHTML = `
            <div class="message-header">${message.type}</div>
            ${contentHtml}
            <div class="message-timestamp">${timestamp}</div>
        `;

        messagesArea.appendChild(messageElement);

        if (scroll) {
            this.smartScrollToBottom();
        }
    }

    handleStateChange(stateData) {
        console.log('Session state changed:', stateData);

        // Update specific session in real-time instead of reloading all sessions
        const sessionId = stateData.session_id;
        const sessionInfo = stateData.session;

        // Skip processing if this session is being deleted
        if (this.deletingSessions.has(sessionId)) {
            console.log(`Ignoring state change for session ${sessionId} - deletion in progress`);
            return;
        }

        if (sessionInfo) {
            // Update the session in our local sessions map
            this.sessions.set(sessionId, sessionInfo);

            // Re-render sessions to reflect the state change
            this.renderSessions();

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

        this.sessions.set(sessionId, sessionInfo);
        this.renderSessions();

        if (sessionId === this.currentSessionId) {
            this.updateSessionInfo({ session: sessionInfo });
        }
    }

    handleSessionDeleted(sessionId) {
        // Clean up a session that was deleted externally
        console.log(`Handling external deletion of session ${sessionId}`);

        // Remove from sessions map
        this.sessions.delete(sessionId);

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
        document.getElementById('create-session-modal').classList.remove('hidden');
    }

    hideCreateSessionModal() {
        document.getElementById('create-session-modal').classList.add('hidden');
        document.getElementById('create-session-form').reset();
    }

    handleCreateSession(event) {
        event.preventDefault();

        const formData = new FormData(event.target);
        const data = Object.fromEntries(formData.entries());

        this.createSession(data)
            .then(() => {
                this.hideCreateSessionModal();
            })
            .catch(error => {
                console.error('Session creation failed:', error);
            });
    }

    browseDirectory() {
        // In a real implementation, this would open a directory picker
        // For now, we'll just suggest using the current directory
        const input = document.getElementById('working-directory');
        if (!input.value) {
            input.value = prompt('Enter working directory:', '/') || input.value;
        }
    }

    showDeleteSessionModal() {
        if (!this.currentSessionId) return;

        // Get session info to display the name
        const session = this.sessions.get(this.currentSessionId);
        const sessionName = session?.name || this.currentSessionId;

        // Update modal content
        document.getElementById('delete-session-name').textContent = sessionName;
        document.getElementById('delete-session-modal').classList.remove('hidden');
    }

    hideDeleteSessionModal() {
        document.getElementById('delete-session-modal').classList.add('hidden');
    }

    async confirmDeleteSession() {
        if (!this.currentSessionId) return;

        const sessionIdToDelete = this.currentSessionId;

        try {
            this.showLoading(true);

            // Mark session as being deleted to prevent race conditions
            this.deletingSessions.add(sessionIdToDelete);

            // Remove from local sessions map immediately to prevent further operations
            this.sessions.delete(sessionIdToDelete);

            const response = await this.apiRequest(`/api/sessions/${sessionIdToDelete}`, {
                method: 'DELETE'
            });

            if (response.success) {
                // Session successfully deleted
                this.hideDeleteSessionModal();

                // If this was the current session, exit it
                if (this.currentSessionId === sessionIdToDelete) {
                    this.exitSession();
                }

                // Refresh the sessions list
                this.renderSessions();

                console.log(`Session ${sessionIdToDelete} deleted successfully`);
            } else {
                // Restore session to map if deletion failed
                const sessionData = await this.apiRequest(`/api/sessions/${sessionIdToDelete}`).catch(() => null);
                if (sessionData) {
                    this.sessions.set(sessionIdToDelete, sessionData.session);
                }
                throw new Error('Failed to delete session');
            }
        } catch (error) {
            console.error('Failed to delete session:', error);
            this.showError(`Failed to delete session: ${error.message}`);

            // Try to restore session to map if deletion failed
            try {
                const sessionData = await this.apiRequest(`/api/sessions/${sessionIdToDelete}`);
                if (sessionData && sessionData.session) {
                    this.sessions.set(sessionIdToDelete, sessionData.session);
                    this.renderSessions();
                }
            } catch (restoreError) {
                console.log('Could not restore session data after failed deletion');
            }
        } finally {
            // Always remove from deleting set
            this.deletingSessions.delete(sessionIdToDelete);
            this.showLoading(false);
        }
    }

    // Utility Methods
    showLoading(show) {
        const overlay = document.getElementById('loading-overlay');
        if (show) {
            overlay.classList.remove('hidden');
        } else {
            overlay.classList.add('hidden');
        }
    }

    showError(message) {
        alert(`Error: ${message}`);
    }

    setInputControlsEnabled(enabled) {
        const messageInput = document.getElementById('message-input');
        const sendButton = document.getElementById('send-btn');

        if (enabled) {
            // Enable input controls
            messageInput.disabled = false;
            sendButton.disabled = false;
            messageInput.placeholder = "Type your message to Claude Code...";
            console.log('Input controls enabled');
        } else {
            // Disable input controls for error state
            messageInput.disabled = true;
            sendButton.disabled = true;
            messageInput.placeholder = "Session is in error state - input disabled";
            console.log('Input controls disabled due to error state');
        }
    }

    // Auto-scroll functionality
    toggleAutoScroll() {
        this.autoScrollEnabled = !this.autoScrollEnabled;
        const button = document.getElementById('auto-scroll-toggle');

        if (this.autoScrollEnabled) {
            button.textContent = '📜 Auto-scroll: ON';
            button.className = 'btn btn-small btn-secondary auto-scroll-enabled';
            this.smartScrollToBottom();
        } else {
            button.textContent = '📜 Auto-scroll: OFF';
            button.className = 'btn btn-small btn-secondary auto-scroll-disabled';
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

    // Sidebar Management
    toggleSidebar() {
        const sidebar = document.getElementById('sidebar');
        this.sidebarCollapsed = !this.sidebarCollapsed;

        if (this.sidebarCollapsed) {
            // Store current width before collapsing
            this.sidebarWidth = sidebar.offsetWidth;
            // Force collapse width via inline style to override any previous inline styles
            sidebar.style.width = '50px';
            sidebar.classList.add('collapsed');
            document.getElementById('sidebar-collapse-btn').title = 'Expand sidebar';
        } else {
            // Restore the previous width
            sidebar.style.width = `${this.sidebarWidth}px`;
            sidebar.classList.remove('collapsed');
            document.getElementById('sidebar-collapse-btn').title = 'Collapse sidebar';
        }
    }

    startResize(e) {
        // Don't allow resize when collapsed
        if (this.sidebarCollapsed) return;

        this.isResizing = true;
        document.addEventListener('mousemove', this.handleResize.bind(this));
        document.addEventListener('mouseup', this.stopResize.bind(this));
        e.preventDefault();
    }

    handleResize(e) {
        if (!this.isResizing) return;

        const sidebar = document.getElementById('sidebar');
        const containerRect = document.querySelector('.main-content').getBoundingClientRect();
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
        if (!this.sidebarCollapsed) {
            const sidebar = document.getElementById('sidebar');
            const maxWidth = window.innerWidth * 0.3;

            // Ensure sidebar doesn't exceed 30% of new window width
            if (this.sidebarWidth > maxWidth) {
                this.sidebarWidth = maxWidth;
                sidebar.style.width = `${maxWidth}px`;
            }
        }
    }
}

// Initialize the application when the DOM is loaded
document.addEventListener('DOMContentLoaded', () => {
    window.claudeWebUI = new ClaudeWebUI();
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