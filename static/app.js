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

        this.init();
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
        document.getElementById('exit-session-btn').addEventListener('click', () => this.exitSession());

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

            // Show processing indicator
            this.showProcessingIndicator();

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
        }
    }

    hideProcessingIndicator() {
        this.isProcessing = false;
        const progressElement = document.getElementById('claude-progress');
        const sendButton = document.getElementById('send-btn');
        const messageInput = document.getElementById('message-input');

        if (progressElement) {
            progressElement.classList.add('hidden');
        }
        if (sendButton) {
            sendButton.disabled = false;
            sendButton.textContent = 'Send';
        }
        if (messageInput) {
            messageInput.disabled = false;
        }
    }

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

        // Handle progress indicator for result messages
        if (message.type === 'result') {
            console.log('Hiding progress indicator for result message');
            // Hide progress indicator when we get a result message
            this.hideProcessingIndicator();
        }

        // Use the unified filtering logic to determine if message should be displayed
        if (this.shouldDisplayMessage(message)) {
            console.log('Adding message to UI:', message.type);
            this.addMessageToUI(message);
        }
    }

    async loadSessionInfo() {
        if (!this.currentSessionId) return;

        try {
            const data = await this.apiRequest(`/api/sessions/${this.currentSessionId}`);
            this.updateSessionInfo(data);
        } catch (error) {
            console.error('Failed to load session info:', error);
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

        // Auto-start session if it's not active/running/starting
        const session = this.sessions.get(sessionId);
        if (session && session.state !== 'active' && session.state !== 'running' && session.state !== 'starting') {
            console.log(`Auto-starting session ${sessionId} (current state: ${session.state})`);
            await this.apiRequest(`/api/sessions/${sessionId}/start`, { method: 'POST' });

            // Wait for session to be fully active before connecting WebSocket
            let attempts = 0;
            const maxAttempts = 15; // Increased from 10 to allow for longer SDK initialization
            const pollInterval = 1000; // Increased from 200ms to 1 second
            while (attempts < maxAttempts) {
                await new Promise(resolve => setTimeout(resolve, pollInterval));
                await this.loadSessionInfo();
                const updatedSession = this.sessions.get(sessionId);
                if (updatedSession && (updatedSession.state === 'active' || updatedSession.state === 'running')) {
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
        } else if (session && (session.state === 'active' || session.state === 'running')) {
            // Session is already active, just connect WebSocket
            this.connectSessionWebSocket();
        } else if (session && session.state === 'starting') {
            // Session is starting, wait for it to become active
            console.log(`Session ${sessionId} is starting, waiting for it to become active...`);
            let attempts = 0;
            const maxAttempts = 15;
            const pollInterval = 1000;
            while (attempts < maxAttempts) {
                await new Promise(resolve => setTimeout(resolve, pollInterval));
                await this.loadSessionInfo();
                const updatedSession = this.sessions.get(sessionId);
                if (updatedSession && (updatedSession.state === 'active' || updatedSession.state === 'running')) {
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

        // Load messages after session is ready
        this.loadMessages();
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
            sessionElement.addEventListener('click', () => this.selectSession(sessionId));

            sessionElement.innerHTML = `
                <div class="session-id">${sessionId}</div>
                <span class="session-status ${session.state}">${session.state}</span>
                <div class="session-info">
                    ${session.working_directory}
                </div>
            `;

            container.appendChild(sessionElement);
        });
    }

    updateSessionInfo(sessionData) {
        document.getElementById('current-session-id').textContent = this.currentSessionId;
        document.getElementById('current-session-state').textContent = sessionData.session.state;
        document.getElementById('current-session-state').className = `session-state ${sessionData.session.state}`;

        // Update the sessions Map with current session state
        if (this.currentSessionId && this.sessions.has(this.currentSessionId)) {
            const existingSession = this.sessions.get(this.currentSessionId);
            existingSession.state = sessionData.session.state;
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
        this.sessions.set(sessionId, sessionInfo);
        this.renderSessions();

        if (sessionId === this.currentSessionId) {
            this.updateSessionInfo({ session: sessionInfo });
        }
    }

    updateConnectionStatus(status) {
        const indicator = document.getElementById('connection-indicator');
        const text = document.getElementById('connection-text');

        indicator.className = `status-indicator ${status}`;

        switch (status) {
            case 'connected':
                text.textContent = 'Connected';
                break;
            case 'connecting':
                text.textContent = 'Connecting...';
                break;
            case 'disconnected':
            default:
                text.textContent = 'Disconnected';
                break;
        }
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