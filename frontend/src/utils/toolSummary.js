/**
 * Tool summary utility functions for generating tool descriptions
 * Used by ToolCallCard.vue and ToolTimelineBar.vue
 */

/**
 * Extract basename from file path
 * @param {string} path - File path
 * @returns {string} Basename
 */
export function getBasename(path) {
  if (!path) return ''
  // Handle both forward and backslashes
  const parts = path.replace(/\\/g, '/').split('/')
  return parts[parts.length - 1]
}

/**
 * Extract actual command from bash command (remove cd prefix)
 * @param {string} cmd - Full bash command
 * @returns {string} Actual command without cd prefix
 */
export function extractBashCommand(cmd) {
  if (!cmd) return ''
  // Remove cd "..." && prefix to show only the actual command
  // Match: cd "any path" && actual_command
  const match = cmd.match(/cd\s+"[^"]+"\s+&&\s+(.+)$/)
  if (match) {
    return match[1]
  }
  // No cd prefix found, return command as-is
  return cmd
}

/**
 * Truncate bash command to max length
 * @param {string} cmd - Command to truncate
 * @param {number} maxLen - Maximum length
 * @returns {string} Truncated command
 */
export function truncateBashCommand(cmd, maxLen = 200) {
  if (!cmd) return ''
  if (cmd.length <= maxLen) return cmd
  return cmd.substring(0, maxLen) + '...'
}

/**
 * Extract exit code from bash result
 * @param {Object} result - Tool result object
 * @returns {number|null} Exit code
 */
export function getExitCode(result) {
  if (!result) return null
  // Check for exit code in result content or message
  if (result.content) {
    const match = result.content.match(/exit code (\d+)/i)
    if (match) return parseInt(match[1])
  }
  // Default: success = 0, error = 1
  return result.error ? 1 : 0
}

/**
 * Count lines in diff
 * @param {string} oldStr - Old string
 * @param {string} newStr - New string
 * @returns {{added: number, removed: number}} Line counts
 */
export function countDiffLines(oldStr, newStr) {
  const oldLines = oldStr ? oldStr.split('\n').length : 0
  const newLines = newStr ? newStr.split('\n').length : 0
  return { added: newLines, removed: oldLines }
}

/**
 * Generate tool summary for display
 * @param {Object} toolCall - Tool call object with name, input, result, status
 * @param {string} status - Effective status of the tool
 * @returns {string} Human-readable summary
 */
export function generateToolSummary(toolCall, status) {
  const toolName = toolCall.name
  const input = toolCall.input || {}
  const result = toolCall.result

  switch (toolName) {
    case 'Bash':
    case 'Shell':
    case 'Command': {
      // Extract actual command (remove cd prefix), then truncate
      const extractedCmd = extractBashCommand(input.command)
      const cmd = truncateBashCommand(extractedCmd, 200)
      if (status === 'completed' && result) {
        const exitCode = getExitCode(result)
        return `Bash: ${cmd} (exit ${exitCode})`
      }
      return `Bash: ${cmd}`
    }

    case 'SlashCommand': {
      const cmd = truncateBashCommand(input.command, 200)
      return `SlashCommand: ${cmd}`
    }

    case 'Edit': {
      const filename = getBasename(input.file_path)
      if (status === 'completed' && result && !result.error) {
        const { added, removed } = countDiffLines(input.old_string, input.new_string)
        if (added === removed) {
          return `Edit: ${filename} (${added} lines changed)`
        }
        return `Edit: ${filename} (${added} lines added, ${removed} removed)`
      }
      return `Edit: ${filename}`
    }

    case 'Read': {
      const filename = getBasename(input.file_path)
      const hasRange = input.offset !== undefined || input.limit !== undefined
      if (hasRange) {
        const start = (input.offset || 0) + 1
        const end = input.limit ? (input.offset || 0) + input.limit : '∞'
        return `Read: ${filename} (lines ${start}-${end})`
      }
      return `Read: ${filename} (full file)`
    }

    case 'Write': {
      const filename = getBasename(input.file_path)
      if (status === 'completed' && result && !result.error && input.content) {
        const lineCount = input.content.split('\n').length
        return `Write: ${filename} (${lineCount} lines written)`
      }
      return `Write: ${filename}`
    }

    case 'Skill': {
      const skillName = input.skill || 'Unknown'
      if (status === 'completed' && result) {
        return result.error ? `Skill: ${skillName} (error)` : `Skill: ${skillName} (completed)`
      }
      return `Skill: ${skillName}`
    }

    case 'Task': {
      const agentType = input.subagent_type || 'general'
      const description = input.description || 'Task'
      if (status === 'completed' && result) {
        return result.error ? `Task: (${agentType}) ${description} (error)` : `Task: (${agentType}) ${description} (completed)`
      }
      return `Task: (${agentType}) ${description}`
    }

    case 'TodoWrite': {
      if (input.todos && Array.isArray(input.todos)) {
        const completed = input.todos.filter(t => t.status === 'completed').length
        const total = input.todos.length
        const inProgress = input.todos.find(t => t.status === 'in_progress')
        if (inProgress) {
          return `Todo: Working on "${inProgress.content}"`
        }
        return `Todo: ${completed}/${total} Tasks Completed`
      }
      return 'Todo: Updated'
    }

    case 'Grep':
    case 'Glob': {
      const pattern = input.pattern || ''
      const toolType = toolName === 'Grep' ? 'Grep' : 'Glob'
      if (status === 'completed' && result && !result.error) {
        // Count results - result content varies by output_mode
        let resultCount = 0
        if (result.content) {
          const lines = result.content.split('\n').filter(l => l.trim())
          resultCount = lines.length
        }
        return `${toolType}: "${pattern}" (${resultCount} results)`
      }
      return `${toolType}: "${pattern}"`
    }

    case 'WebFetch':
    case 'WebSearch': {
      const url = input.url || input.query || ''

      // For WebSearch, the query is not a URL - display it directly
      if (toolName === 'WebSearch') {
        const query = input.query || ''
        const displayQuery = query.length > 50 ? query.substring(0, 50) + '...' : query
        if (status === 'completed' && result) {
          return result.error ? `Web: ${displayQuery} (error)` : `Web: ${displayQuery} (success)`
        }
        return `Web: ${displayQuery}`
      }

      // For WebFetch, extract domain from URL
      let domain = ''
      try {
        if (url) {
          const parsedUrl = new URL(url.startsWith('http') ? url : `https://${url}`)
          domain = parsedUrl.hostname
        }
      } catch {
        // If URL parsing fails, use truncated URL
        domain = url.length > 50 ? url.substring(0, 50) + '...' : url
      }
      if (status === 'completed' && result) {
        return result.error ? `Web: ${domain} (error)` : `Web: ${domain} (success)`
      }
      return `Web: ${domain || url}`
    }

    case 'NotebookEdit': {
      const filename = getBasename(input.notebook_path)
      const cellNum = input.cell_number !== undefined ? input.cell_number : '?'
      const mode = input.edit_mode || 'replace'
      return `NotebookEdit: ${filename} cell #${cellNum} (${mode})`
    }

    case 'ExitPlanMode': {
      return 'ExitPlanMode: Plan submitted'
    }

    case 'BashOutput': {
      const bashId = input.bash_id || 'unknown'
      return `BashOutput: Read output from ${bashId}`
    }

    case 'KillShell': {
      const shellId = input.shell_id || 'unknown'
      return `KillShell: Terminate ${shellId}`
    }

    case 'AskUserQuestion': {
      const questions = input.questions || []
      const questionCount = questions.length
      if (status === 'completed' && result && !result.error) {
        return `AskUserQuestion: ${questionCount} question(s) answered`
      }
      if (status === 'permission_required') {
        return `AskUserQuestion: ${questionCount} question(s) awaiting response`
      }
      return `AskUserQuestion: ${questionCount} question(s)`
    }

    default: {
      return `${toolName}: executed`
    }
  }
}

/**
 * Generate short tool summary for timeline tooltip (just tool name and key parameter)
 * @param {Object} toolCall - Tool call object
 * @returns {string} Short summary
 */
export function generateShortToolSummary(toolCall) {
  const toolName = toolCall.name
  const input = toolCall.input || {}

  switch (toolName) {
    case 'Bash':
    case 'Shell':
    case 'Command': {
      const extractedCmd = extractBashCommand(input.command)
      const cmd = truncateBashCommand(extractedCmd, 60)
      return `${toolName}: ${cmd}`
    }

    case 'SlashCommand': {
      const cmd = truncateBashCommand(input.command, 60)
      return `SlashCommand: ${cmd}`
    }

    case 'Edit':
    case 'Read':
    case 'Write': {
      const filename = getBasename(input.file_path)
      return `${toolName}: ${filename}`
    }

    case 'Skill': {
      const skillName = input.skill || 'Unknown'
      return `Skill: ${skillName}`
    }

    case 'Task': {
      const agentType = input.subagent_type || 'general'
      const description = input.description || 'Task'
      const shortDesc = description.length > 40 ? description.substring(0, 40) + '...' : description
      return `Task: (${agentType}) ${shortDesc}`
    }

    case 'Grep':
    case 'Glob': {
      const pattern = input.pattern || ''
      const shortPattern = pattern.length > 40 ? pattern.substring(0, 40) + '...' : pattern
      return `${toolName}: "${shortPattern}"`
    }

    case 'WebFetch':
    case 'WebSearch': {
      if (toolName === 'WebSearch') {
        const query = input.query || ''
        const shortQuery = query.length > 40 ? query.substring(0, 40) + '...' : query
        return `WebSearch: ${shortQuery}`
      }
      let domain = ''
      try {
        const url = input.url || ''
        if (url) {
          const parsedUrl = new URL(url.startsWith('http') ? url : `https://${url}`)
          domain = parsedUrl.hostname
        }
      } catch {
        domain = 'URL'
      }
      return `WebFetch: ${domain}`
    }

    case 'NotebookEdit': {
      const filename = getBasename(input.notebook_path)
      return `NotebookEdit: ${filename}`
    }

    case 'AskUserQuestion': {
      const questions = input.questions || []
      const questionCount = questions.length
      return `AskUserQuestion: ${questionCount} question(s)`
    }

    default: {
      return toolName
    }
  }
}
