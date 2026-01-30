/**
 * API utility for backend communication
 * Replaces the broken APIClient with clean fetch wrappers
 */

class APIError extends Error {
  constructor(message, status, data) {
    super(message)
    this.name = 'APIError'
    this.status = status
    this.data = data
  }
}

/**
 * Make an API request
 * @param {string} endpoint - API endpoint path
 * @param {Object} options - Fetch options
 * @returns {Promise<any>} Response data
 */
async function apiRequest(endpoint, options = {}) {
  try {
    const response = await fetch(endpoint, {
      headers: {
        'Content-Type': 'application/json',
        ...options.headers
      },
      ...options
    })

    // Handle non-OK responses
    if (!response.ok) {
      let errorData
      try {
        errorData = await response.json()
      } catch {
        errorData = { detail: response.statusText }
      }

      throw new APIError(
        errorData.detail || `HTTP ${response.status}: ${response.statusText}`,
        response.status,
        errorData
      )
    }

    // Parse response
    const contentType = response.headers.get('content-type')
    if (contentType && contentType.includes('application/json')) {
      return await response.json()
    }

    return await response.text()
  } catch (error) {
    if (error instanceof APIError) {
      throw error
    }

    // Network or other errors
    console.error('API request failed:', error)
    throw new APIError(
      `Network error: ${error.message}`,
      0,
      null
    )
  }
}

/**
 * GET request
 */
export async function apiGet(endpoint, options = {}) {
  // Handle query parameters
  let url = endpoint
  if (options.params) {
    const params = new URLSearchParams()
    for (const [key, value] of Object.entries(options.params)) {
      if (value !== null && value !== undefined) {
        params.append(key, value)
      }
    }
    const queryString = params.toString()
    if (queryString) {
      url = `${endpoint}?${queryString}`
    }
    // Remove params from options to avoid passing to fetch
    const { params: _, ...fetchOptions } = options
    return apiRequest(url, {
      ...fetchOptions,
      method: 'GET'
    })
  }

  return apiRequest(url, {
    ...options,
    method: 'GET'
  })
}

/**
 * POST request
 */
export async function apiPost(endpoint, data, options = {}) {
  return apiRequest(endpoint, {
    ...options,
    method: 'POST',
    body: JSON.stringify(data)
  })
}

/**
 * PUT request
 */
export async function apiPut(endpoint, data, options = {}) {
  return apiRequest(endpoint, {
    ...options,
    method: 'PUT',
    body: JSON.stringify(data)
  })
}

/**
 * DELETE request
 */
export async function apiDelete(endpoint, options = {}) {
  return apiRequest(endpoint, {
    ...options,
    method: 'DELETE'
  })
}

/**
 * PATCH request
 */
export async function apiPatch(endpoint, data, options = {}) {
  return apiRequest(endpoint, {
    ...options,
    method: 'PATCH',
    body: JSON.stringify(data)
  })
}

/**
 * Export named functions for convenience
 */
export const api = {
  get: apiGet,
  post: apiPost,
  put: apiPut,
  delete: apiDelete,
  patch: apiPatch
}
