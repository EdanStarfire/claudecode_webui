/**
 * API client for communicating with the backend
 */
class APIClient {
    constructor(errorCallback) {
        this.errorCallback = errorCallback || ((msg) => console.error(msg));
    }

    /**
     * Make an API request to the backend
     * @param {string} endpoint - API endpoint path
     * @param {Object} options - Fetch options (method, body, headers, etc.)
     * @returns {Promise<Object>} Response data
     */
    async request(endpoint, options = {}) {
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
            this.errorCallback(`API request failed: ${error.message}`);
            throw error;
        }
    }

    // Convenience methods for common HTTP verbs
    async get(endpoint, options = {}) {
        return this.request(endpoint, { ...options, method: 'GET' });
    }

    async post(endpoint, data, options = {}) {
        return this.request(endpoint, {
            ...options,
            method: 'POST',
            body: JSON.stringify(data)
        });
    }

    async put(endpoint, data, options = {}) {
        return this.request(endpoint, {
            ...options,
            method: 'PUT',
            body: JSON.stringify(data)
        });
    }

    async delete(endpoint, options = {}) {
        return this.request(endpoint, { ...options, method: 'DELETE' });
    }
}
