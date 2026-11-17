#!/usr/bin/env node
// Drift check (issue #2359): the Chromium revision the installed Playwright
// requires (playwright-core/browsers.json) must exist in the Playwright
// browser cache. Exit 0 = ok, 1 = drift; --warn prints the drift but exits 0.
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'

// Browsers.json entry name -> cache directory prefix.
const BROWSER_DIRS = {
  chromium: 'chromium',
  'chromium-headless-shell': 'chromium_headless_shell'
}

export function defaultCacheDir(env = process.env) {
  return env.PLAYWRIGHT_BROWSERS_PATH || path.join(os.homedir(), '.cache', 'ms-playwright')
}

// Returns [{name, revision, expectedDir}] for every required browser whose
// cache directory is not in installedDirs.
export function findDrift(browsersJson, installedDirs) {
  const installed = new Set(installedDirs)
  const drift = []
  for (const entry of browsersJson.browsers || []) {
    const prefix = BROWSER_DIRS[entry.name]
    if (!prefix) continue
    const expectedDir = `${prefix}-${entry.revision}`
    if (!installed.has(expectedDir)) {
      drift.push({ name: entry.name, revision: entry.revision, expectedDir })
    }
  }
  return drift
}

export function formatDrift(drift, cacheDir, installedDirs) {
  const have = installedDirs.filter((d) => /^chromium/.test(d)).join(', ') || 'none'
  const need = drift.map((d) => d.expectedDir).join(', ')
  return (
    `Playwright browser drift: required ${need} missing in ${cacheDir} (cached: ${have}). ` +
    'Rebuild the dev image (it follows frontend/package-lock.json) or run `npx playwright install chromium`.'
  )
}

function main() {
  const warnOnly = process.argv.includes('--warn')
  const cacheDir = defaultCacheDir()
  let browsersJson
  try {
    const require = createRequire(path.join(process.cwd(), 'package.json'))
    browsersJson = JSON.parse(
      fs.readFileSync(path.join(path.dirname(require.resolve('playwright-core/package.json')), 'browsers.json'), 'utf8')
    )
  } catch (err) {
    console.error(`check-playwright-browser: cannot read playwright-core/browsers.json (${err.message})`)
    process.exit(warnOnly ? 0 : 1)
  }
  let installedDirs = []
  try {
    installedDirs = fs.readdirSync(cacheDir)
  } catch {
    // Missing cache dir: every required browser is reported as drift.
  }
  const drift = findDrift(browsersJson, installedDirs)
  if (drift.length === 0) {
    console.log('Playwright browser cache matches the installed Playwright.')
    return
  }
  console.error(formatDrift(drift, cacheDir, installedDirs))
  process.exit(warnOnly ? 0 : 1)
}

if (process.argv[1] && fileURLToPath(import.meta.url) === path.resolve(process.argv[1])) {
  main()
}
