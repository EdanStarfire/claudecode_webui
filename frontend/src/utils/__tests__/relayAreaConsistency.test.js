import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { PROFILE_AREAS, PROFILE_AREA_LABELS } from '@/utils/profileAreas'
import { CONFIG_FIELDS_LIST, FIELD_DEFAULTS, fieldArea } from '@/utils/configFields'
import { FIELD_SCHEMAS } from '@/components/configuration/fields/fieldSchemas.js'

const SRC = resolve(__dirname, '../..')
const read = (rel) => readFileSync(resolve(SRC, rel), 'utf8')
const RELAY_FIELDS = ['relay_id', 'relay_footer_enabled', 'relay_footer_template',
  'relay_channel_policies', 'relay_handle']

// The profile areas are hard-coded in several components. A miss silently drops an area from
// that screen, so every list must carry every area in PROFILE_AREAS.
describe('relay area is present in every hard-coded area list', () => {
  it('shared constants', () => {
    expect(PROFILE_AREAS).toContain('relay')
    expect(PROFILE_AREA_LABELS.relay).toBe('Relay')
  })

  it('config field definitions', () => {
    for (const f of RELAY_FIELDS) {
      expect(CONFIG_FIELDS_LIST).toContain(f)
      expect(f in FIELD_DEFAULTS).toBe(true)
      expect(FIELD_DEFAULTS[f]).toBeNull()
      expect(fieldArea(f)).toBe('relay')
    }
  })

  it('every PROFILE_AREAS key appears in each component list', () => {
    const files = [
      'components/settings/sections/GeneralSection.vue',
      'components/settings/sections/LibraryProfilesSection.vue',
      'components/settings/SettingsSidebar.vue',
    ]
    for (const file of files) {
      const text = read(file)
      for (const area of PROFILE_AREAS) {
        expect(text, `${file} is missing area '${area}'`).toMatch(
          new RegExp(`(key: '${area}'|\\b${area}:\\s*')`))
      }
    }
  })

  it('model tuning not-applicable label map names the relay area', () => {
    expect(read('components/settings/sections/ModelTuningSection.vue')).toMatch(/relay:\s*'Relay'/)
  })

  it('layout and sidebar route the relay section', () => {
    expect(read('components/settings/SettingsLayout.vue')).toMatch(/'relay':\s+RelaySection/)
    expect(read('components/settings/SettingsLayout.vue')).toContain("'/settings/relays'")
    expect(read('components/settings/SettingsSidebar.vue')).toContain("section: 'relay'")
    expect(read('components/settings/SettingsSidebar.vue')).toContain("'/settings/relays'")
    expect(read('router/index.js')).toContain("'/settings/relays'")
    expect(read('components/settings/search/settingsIndex.js')).toContain("section: 'edit-relay'")
  })

  it('field schema covers the relay area fields the form renders', () => {
    const keys = FIELD_SCHEMAS.relay.map(f => f.key)
    expect(keys).toEqual(['relay_id', 'relay_footer_enabled', 'relay_footer_template',
      'relay_channel_policies'])
    for (const k of keys) expect(fieldArea(k)).toBe('relay')
  })
})

// Relay sits immediately after MCP Servers wherever both appear (and before Hooks).
describe('relay ordering is consistent (issue #2103)', () => {
  const order = (text, re) => [...text.matchAll(re)].map(m => m[1])

  it('PROFILE_AREAS and labels', () => {
    const i = PROFILE_AREAS.indexOf('mcp')
    expect(PROFILE_AREAS[i + 1]).toBe('relay')
    expect(PROFILE_AREAS[i + 2]).toBe('hooks')
    const labelKeys = Object.keys(PROFILE_AREA_LABELS)
    expect(labelKeys[labelKeys.indexOf('mcp') + 1]).toBe('relay')
  })

  it('area editor sidebar list, Library sidebar group and component area arrays', () => {
    const sidebar = read('components/settings/SettingsSidebar.vue')
    const edit = order(sidebar, /\{ section: '([a-z-]+)',\s+icon:/g)
    expect(edit[edit.indexOf('mcp-servers') + 1]).toBe('relay')
    expect(edit[edit.indexOf('mcp-servers') + 2]).toBe('hooks')
    const lib = order(sidebar, /\{ to: '\/settings\/([a-z-]+)',\s+icon:/g)
    expect(lib.slice(lib.indexOf('mcp-servers'))).toEqual(['mcp-servers', 'relays', 'hooks'])
    for (const f of ['components/settings/sections/GeneralSection.vue',
      'components/settings/sections/LibraryProfilesSection.vue']) {
      const keys = order(read(f), /\{ key: '([a-z_]+)',\s+label:/g)
      expect(keys[keys.indexOf('mcp') + 1], f).toBe('relay')
    }
  })

  it('settings search index groups follow the same order', () => {
    const sections = []
    for (const m of read('components/settings/search/settingsIndex.js').matchAll(/section: '([a-z-]+)'/g)) {
      if (sections[sections.length - 1] !== m[1]) sections.push(m[1])
    }
    expect(sections[sections.indexOf('mcp-servers') + 1]).toBe('relays')
    expect(sections[sections.indexOf('relays') + 1]).toBe('hooks')
    expect(sections[sections.indexOf('edit-mcp-servers') + 1]).toBe('edit-relay')
    expect(sections[sections.indexOf('edit-relay') + 1]).toBe('edit-hooks')
  })
})
