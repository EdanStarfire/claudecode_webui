// Image file extensions (dot-prefixed)
export const IMAGE_EXTENSIONS = new Set([
  '.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.svg', '.ico'
])

// File type icon mapping (dot-prefixed extension → emoji)
export const FILE_TYPE_ICONS = {
  // Documents
  '.pdf': '📄',
  '.doc': '📝',
  '.docx': '📝',
  '.txt': '📄',
  '.rtf': '📝',
  '.odt': '📝',
  // Spreadsheets
  '.xls': '📊',
  '.xlsx': '📊',
  '.csv': '📊',
  '.ods': '📊',
  // Code
  '.py': '🐍',
  '.js': '📜',
  '.ts': '📜',
  '.jsx': '📜',
  '.tsx': '📜',
  '.vue': '📜',
  '.svelte': '📜',
  '.go': '📜',
  '.rs': '📜',
  '.java': '📜',
  '.c': '📜',
  '.cpp': '📜',
  '.h': '📜',
  '.hpp': '📜',
  '.rb': '📜',
  '.php': '📜',
  '.html': '🌐',
  '.css': '🎨',
  '.scss': '🎨',
  '.sh': '💻',
  '.bat': '💻',
  '.sql': '🗄️',
  // Config / Data
  '.json': '📋',
  '.xml': '📋',
  '.yaml': '📋',
  '.yml': '📋',
  '.toml': '📋',
  '.ini': '⚙️',
  '.cfg': '⚙️',
  '.conf': '⚙️',
  '.env': '⚙️',
  // Docs
  '.md': '📝',
  '.rst': '📝',
  // Archives
  '.zip': '📦',
  '.tar': '📦',
  '.gz': '📦',
  '.rar': '📦',
  '.7z': '📦',
  // Data / Log
  '.log': '📋',
  // Default
  default: '📎',
}

// Extension (without dot) to MIME type map for images
export const IMAGE_MIME_TYPES = {
  png: 'image/png',
  jpg: 'image/jpeg',
  jpeg: 'image/jpeg',
  gif: 'image/gif',
  webp: 'image/webp',
  svg: 'image/svg+xml',
  bmp: 'image/bmp',
  ico: 'image/x-icon',
}

/**
 * Check if a filename or path refers to an image based on extension.
 * @param {string} filename
 * @returns {boolean}
 */
export function isImageFile(filename) {
  if (!filename) return false
  const ext = '.' + filename.split('.').pop().toLowerCase()
  return IMAGE_EXTENSIONS.has(ext)
}

/**
 * Get an icon emoji for a file based on its name or path.
 * @param {string} filename
 * @returns {string} emoji
 */
export function getFileIcon(filename) {
  if (!filename) return FILE_TYPE_ICONS.default
  const ext = '.' + filename.split('.').pop().toLowerCase()
  return FILE_TYPE_ICONS[ext] || FILE_TYPE_ICONS.default
}

/**
 * Get an icon emoji for a file based on its MIME type.
 * @param {string} mimeType
 * @returns {string} emoji
 */
export function getFileIconByMimeType(mimeType) {
  if (!mimeType) return FILE_TYPE_ICONS.default
  if (mimeType.startsWith('image/')) return '🖼️'
  if (
    mimeType.startsWith('text/x-python') ||
    mimeType.includes('javascript') ||
    mimeType.includes('typescript')
  )
    return '📝'
  if (mimeType.startsWith('text/')) return '📄'
  if (mimeType.includes('json') || mimeType.includes('yaml') || mimeType.includes('xml'))
    return '⚙️'
  return FILE_TYPE_ICONS.default
}

/**
 * Get the MIME type string for an image file path.
 * @param {string} path
 * @returns {string} MIME type, defaulting to 'image/png'
 */
export function getImageMimeType(path) {
  if (!path) return 'image/png'
  const ext = path.split('.').pop().toLowerCase()
  return IMAGE_MIME_TYPES[ext] || 'image/png'
}
