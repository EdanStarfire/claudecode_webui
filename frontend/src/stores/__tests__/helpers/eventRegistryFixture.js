import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))

export const GENERATED_PATH = path.resolve(
  __dirname, '../../../../../shared/tests/fixtures/generated/event_registry.json'
)
export const STATIC_PATH = path.resolve(
  __dirname, '../../../../../shared/tests/fixtures/static/event_registry.json'
)

export function loadEventRegistryFixture() {
  const source = fs.existsSync(GENERATED_PATH) ? GENERATED_PATH : STATIC_PATH
  return JSON.parse(fs.readFileSync(source, 'utf-8'))
}
