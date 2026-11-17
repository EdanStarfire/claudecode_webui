import fs from 'node:fs'
import path from 'node:path'
import { describe, it, expect } from 'vitest'
import { findDrift, defaultCacheDir } from '../../../scripts/check-playwright-browser.mjs'

const browsersJson = {
  browsers: [
    { name: 'chromium', revision: '1255' },
    { name: 'chromium-headless-shell', revision: '1255' },
    { name: 'ffmpeg', revision: '1013' },
    { name: 'firefox', revision: '1500' }
  ]
}

describe('findDrift', () => {
  it('reports no drift when both chromium dirs exist', () => {
    expect(findDrift(browsersJson, ['chromium-1255', 'chromium_headless_shell-1255'])).toEqual([])
  })

  it('reports drift when the cache holds an older revision', () => {
    const drift = findDrift(browsersJson, ['chromium-1248', 'chromium_headless_shell-1248'])
    expect(drift.map((d) => d.expectedDir)).toEqual(['chromium-1255', 'chromium_headless_shell-1255'])
  })

  it('reports only the missing directory', () => {
    const drift = findDrift(browsersJson, ['chromium-1255'])
    expect(drift.map((d) => d.expectedDir)).toEqual(['chromium_headless_shell-1255'])
  })

  it('reports drift for an empty or missing cache', () => {
    expect(findDrift(browsersJson, [])).toHaveLength(2)
  })

  it('ignores browsers other than chromium', () => {
    expect(findDrift({ browsers: [{ name: 'firefox', revision: '1' }] }, [])).toEqual([])
  })
})

describe('defaultCacheDir', () => {
  it('honours PLAYWRIGHT_BROWSERS_PATH', () => {
    expect(defaultCacheDir({ PLAYWRIGHT_BROWSERS_PATH: '/x/pw' })).toBe('/x/pw')
  })

  it('falls back to ~/.cache/ms-playwright', () => {
    expect(defaultCacheDir({})).toMatch(/\.cache[\\/]ms-playwright$/)
  })
})

describe('Dockerfile.dev Layer 6c (issue #2359)', () => {
  const dockerfile = fs.readFileSync(
    path.resolve(__dirname, '../../../../docker/Dockerfile.dev'),
    'utf8'
  )

  it('copies the lockfile and installs with npm ci', () => {
    expect(dockerfile).toMatch(/COPY[^\n]*frontend\/package-lock\.json/)
    expect(dockerfile).toMatch(/npm ci --no-audit --no-fund/)
    expect(dockerfile).not.toMatch(/npm install --no-audit --no-fund/)
  })

  it('asserts the cached browser matches after the playwright install', () => {
    expect(dockerfile).toMatch(/playwright install chromium[\s\S]*check-playwright-browser\.mjs/)
  })
})
