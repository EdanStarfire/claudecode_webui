// Claude Code WebUI JavaScript Application

class ClaudeWebUI {
    constructor() {
        this.currentSessionId = null;
        this.websocket = null;
        this.sessions = new Map();
        this.connectionRetryCount = 0;
        this.maxRetries = 5;

        // Auto-scroll functionality
        this.autoScrollEnabled = true;
        this.isUserScrolling = false;
        this.scrollTimeout = null;

        this.init();
    }

    init() {
        this.setupEventListeners();
        this.loadSessions();
        this.updateConnectionStatus('disconnected');
    }

    setupEventListeners() {
        // Session controls
        document.getElementById('create-session-btn').addEventListener('click', () => this.showCreateSessionModal());
        document.getElementById('refresh-sessions-btn').addEventListener('click', () => this.loadSessions());

        // Modal controls
        document.getElementById('close-modal').addEventListener('click', () => this.hideCreateSessionModal());
        document.getElementById('cancel-create').addEventListener('click', () => this.hideCreateSessionModal());
        document.getElementById('create-session-form').addEventListener('submit', (e) => this.handleCreateSession(e));

        // Browse directory button
        document.getElementById('browse-directory').addEventListener('click', () => this.browseDirectory());

        // Session actions
        document.getElementById('start-session-btn').addEventListener('click', () => this.startSession());
        document.getElementById('pause-session-btn').addEventListener('click', () => this.pauseSession());
        document.getElementById('terminate-session-btn').addEventListener('click', () => this.terminateSession());

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

            await this.loadSessions();
            await this.selectSession(data.session_id);

            return data.session_id;
        } catch (error) {
            console.error('Failed to create session:', error);
            throw error;
        } finally {
            this.showLoading(false);
        }
    }

    async startSession() {
        if (!this.currentSessionId) return;

        try {
            await this.apiRequest(`/api/sessions/${this.currentSessionId}/start`, { method: 'POST' });
            await this.loadSessionInfo();

            // Small delay to ensure session is fully started before WebSocket connection
            await new Promise(resolve => setTimeout(resolve, 500));

            this.connectWebSocket();
        } catch (error) {
            console.error('Failed to start session:', error);
        }
    }

    async pauseSession() {
        if (!this.currentSessionId) return;

        try {
            await this.apiRequest(`/api/sessions/${this.currentSessionId}/pause`, { method: 'POST' });
            await this.loadSessionInfo();
        } catch (error) {
            console.error('Failed to pause session:', error);
        }
    }

    async terminateSession() {
        if (!this.currentSessionId) return;

        if (!confirm('Are you sure you want to terminate this session?')) {
            return;
        }

        try {
            await this.apiRequest(`/api/sessions/${this.currentSessionId}/terminate`, { method: 'POST' });
            this.disconnectWebSocket();
            await this.loadSessions();
            await this.loadSessionInfo();
        } catch (error) {
            console.error('Failed to terminate session:', error);
        }
    }

    async sendMessage() {
        const input = document.getElementById('message-input');
        const message = input.value.trim();

        if (!message || !this.currentSessionId) return;

        try {
            // Send via WebSocket if connected
            if (this.websocket && this.websocket.readyState === WebSocket.OPEN) {
                this.websocket.send(JSON.stringify({
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
            const data = await this.apiRequest(`/api/sessions/${this.currentSessionId}/messages`);
            this.renderMessages(data.messages);
        } catch (error) {
            console.error('Failed to load messages:', error);
        }
    }

    // WebSocket Management
    connectWebSocket() {
        if (!this.currentSessionId) return;

        this.disconnectWebSocket();

        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${protocol}//${window.location.host}/ws/${this.currentSessionId}`;

        try {
            this.websocket = new WebSocket(wsUrl);

            this.websocket.onopen = () => {
                console.log('WebSocket connected');
                this.updateConnectionStatus('connected');
                this.connectionRetryCount = 0;
            };

            this.websocket.onmessage = (event) => {
                try {
                    const data = JSON.parse(event.data);
                    this.handleWebSocketMessage(data);
                } catch (error) {
                    console.error('Failed to parse WebSocket message:', error);
                }
            };

            this.websocket.onclose = (event) => {
                console.log('WebSocket disconnected', event.code, event.reason);
                this.updateConnectionStatus('disconnected');

                // Don't retry on specific error codes (session invalid/inactive)
                if (event.code === 4404 || event.code === 4003 || event.code === 4500) {
                    console.log(`WebSocket closed with error code ${event.code}, not retrying`);
                    return;
                }

                this.scheduleReconnect();
            };

            this.websocket.onerror = (error) => {
                console.error('WebSocket error:', error);
                this.updateConnectionStatus('disconnected');
            };

        } catch (error) {
            console.error('Failed to create WebSocket:', error);
            this.updateConnectionStatus('disconnected');
        }
    }

    disconnectWebSocket() {
        if (this.websocket) {
            this.websocket.close();
            this.websocket = null;
        }
        this.updateConnectionStatus('disconnected');
    }

    scheduleReconnect() {
        if (this.connectionRetryCount < this.maxRetries) {
            this.connectionRetryCount++;
            const delay = Math.min(1000 * Math.pow(2, this.connectionRetryCount), 30000);

            console.log(`Scheduling WebSocket reconnect in ${delay}ms (attempt ${this.connectionRetryCount})`);
            setTimeout(() => {
                if (this.currentSessionId) {
                    this.connectWebSocket();
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
                this.addMessageToUI(data.data);
                break;
            case 'state_change':
                this.handleStateChange(data.data);
                break;
            case 'connection_established':
                console.log('WebSocket connection confirmed for session:', data.session_id);
                break;
            case 'ping':
                // Respond to server ping to keep connection alive
                if (this.websocket && this.websocket.readyState === WebSocket.OPEN) {
                    this.websocket.send(JSON.stringify({type: 'pong', timestamp: new Date().toISOString()}));
                }
                break;
            default:
                console.log('Unknown WebSocket message type:', data.type);
        }
    }

    // UI Updates
    async selectSession(sessionId) {
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

        // Auto-start session if it's not active
        const session = this.sessions.get(sessionId);
        if (session && session.state !== 'active' && session.state !== 'running') {
            console.log(`Auto-starting session ${sessionId} (current state: ${session.state})`);
            await this.apiRequest(`/api/sessions/${sessionId}/start`, { method: 'POST' });

            // Wait for session to be fully active before connecting WebSocket
            let attempts = 0;
            const maxAttempts = 10;
            while (attempts < maxAttempts) {
                await new Promise(resolve => setTimeout(resolve, 200));
                await this.loadSessionInfo();
                const updatedSession = this.sessions.get(sessionId);
                if (updatedSession && (updatedSession.state === 'active' || updatedSession.state === 'running')) {
                    console.log(`Session ${sessionId} is now active, connecting WebSocket`);
                    this.connectWebSocket();
                    break;
                }
                attempts++;
            }

            if (attempts >= maxAttempts) {
                console.warn(`Session ${sessionId} did not become active after ${maxAttempts} attempts`);
            }
        } else if (session && (session.state === 'active' || session.state === 'running')) {
            // Session is already active, just connect WebSocket
            this.connectWebSocket();
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

        // Update button states
        const state = sessionData.session.state;
        document.getElementById('start-session-btn').disabled = state === 'active';
        document.getElementById('pause-session-btn').disabled = state !== 'active';
        document.getElementById('terminate-session-btn').disabled = state === 'terminated';
    }

    renderMessages(messages) {
        const messagesArea = document.getElementById('messages-area');
        messagesArea.innerHTML = '';

        messages.forEach(message => {
            this.addMessageToUI(message, false);
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
        this.loadSessions();
        if (stateData.session_id === this.currentSessionId) {
            this.loadSessionInfo();
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

        // Mark as user scrolling
        this.isUserScrolling = true;

        // Reset user scrolling flag after a delay
        this.scrollTimeout = setTimeout(() => {
            this.isUserScrolling = false;
        }, 1000);
    }

    isAtBottom() {
        const messagesArea = document.getElementById('messages-area');
        const threshold = 50; // pixels from bottom
        return messagesArea.scrollTop + messagesArea.clientHeight >= messagesArea.scrollHeight - threshold;
    }

    smartScrollToBottom() {
        if (!this.autoScrollEnabled) {
            return;
        }

        // If user is scrolling, don't auto-scroll unless they're at the bottom
        if (this.isUserScrolling && !this.isAtBottom()) {
            return;
        }

        this.scrollToBottom();
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
    if (window.claudeWebUI && window.claudeWebUI.websocket) {
        window.claudeWebUI.disconnectWebSocket();
    }
});