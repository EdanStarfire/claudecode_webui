import { describe, it, expect } from 'vitest'
import { resolveFieldSource } from '@/utils/sourceCascade'

const BASE = {
  sessionValue: null,
  templateValue: null,
  templateName: null,
  profileValue: null,
  profileName: null,
}

describe('resolveFieldSource', () => {
  // ---- S: value set directly on this object ----

  it('returns S when sessionValue is a non-empty string', () => {
    const result = resolveFieldSource('model', { ...BASE, sessionValue: 'claude-opus' })
    expect(result).toEqual({ kind: 'S' })
  })

  it('returns S when sessionValue is 0 (falsy but defined)', () => {
    const result = resolveFieldSource('thinking_budget_tokens', { ...BASE, sessionValue: 0 })
    // 0 is not null/undefined/"" — counts as explicitly set
    expect(result).toEqual({ kind: 'S' })
  })

  it('returns S when sessionValue is false (boolean explicitly set)', () => {
    const result = resolveFieldSource('sandbox_enabled', { ...BASE, sessionValue: false })
    expect(result).toEqual({ kind: 'S' })
  })

  it('S takes priority over template and profile values', () => {
    const result = resolveFieldSource('model', {
      sessionValue: 'my-model',
      templateValue: 'template-model',
      templateName: 'Code Expert',
      profileValue: 'profile-model',
      profileName: 'My Profile',
    })
    expect(result).toEqual({ kind: 'S' })
  })

  // ---- T: value inherited from template ----

  it('returns T with templateName when only template has value', () => {
    const result = resolveFieldSource('model', {
      ...BASE,
      templateValue: 'claude-3-opus',
      templateName: 'Code Expert',
    })
    expect(result).toEqual({ kind: 'T', templateName: 'Code Expert' })
  })

  it('T takes priority over profile value', () => {
    const result = resolveFieldSource('model', {
      ...BASE,
      templateValue: 'template-model',
      templateName: 'Expert',
      profileValue: 'profile-model',
      profileName: 'My Profile',
    })
    expect(result).toEqual({ kind: 'T', templateName: 'Expert' })
  })

  it('does NOT return T when templateName is null (no template bound)', () => {
    const result = resolveFieldSource('model', {
      ...BASE,
      templateValue: 'some-value',
      templateName: null,
    })
    expect(result.kind).not.toBe('T')
  })

  // ---- P: value inherited from profile ----

  it('returns P with profileName when only profile has value', () => {
    const result = resolveFieldSource('role', {
      ...BASE,
      profileValue: 'specialist',
      profileName: 'Deep Think',
    })
    expect(result).toEqual({ kind: 'P', profileName: 'Deep Think' })
  })

  it('does NOT return P when profileName is null (no profile bound)', () => {
    const result = resolveFieldSource('role', {
      ...BASE,
      profileValue: 'specialist',
      profileName: null,
    })
    expect(result.kind).not.toBe('P')
  })

  // ---- EMPTY ----

  it('returns EMPTY when no values set anywhere', () => {
    const result = resolveFieldSource('model', BASE)
    expect(result).toEqual({ kind: 'EMPTY' })
  })

  it('returns EMPTY when sessionValue is null', () => {
    const result = resolveFieldSource('model', { ...BASE, sessionValue: null })
    expect(result).toEqual({ kind: 'EMPTY' })
  })

  it('returns EMPTY when sessionValue is undefined', () => {
    const result = resolveFieldSource('model', { ...BASE, sessionValue: undefined })
    expect(result).toEqual({ kind: 'EMPTY' })
  })

  it('returns EMPTY when sessionValue is empty string', () => {
    const result = resolveFieldSource('model', { ...BASE, sessionValue: '' })
    expect(result).toEqual({ kind: 'EMPTY' })
  })

  it('returns EMPTY when template has value but no templateName', () => {
    const result = resolveFieldSource('model', {
      ...BASE,
      templateValue: 'x',
      templateName: null,
      profileValue: 'y',
      profileName: null,
    })
    expect(result).toEqual({ kind: 'EMPTY' })
  })

  // ---- full matrix ----

  it.each([
    // [sessionValue, templateValue, templateName, profileValue, profileName, expectedKind]
    ['s-val', 't-val', 'T', 'p-val', 'P', 'S'],
    [null, 't-val', 'T', 'p-val', 'P', 'T'],
    [null, null, null, 'p-val', 'P', 'P'],
    [null, null, null, null, null, 'EMPTY'],
    ['', 't-val', 'T', 'p-val', 'P', 'T'],
    [null, '', 'T', 'p-val', 'P', 'P'],
  ])('matrix: S=%s T=%s(%s) P=%s(%s) → %s', (sv, tv, tn, pv, pn, kind) => {
    const result = resolveFieldSource('field', {
      sessionValue: sv,
      templateValue: tv,
      templateName: tn,
      profileValue: pv,
      profileName: pn,
    })
    expect(result.kind).toBe(kind)
  })
})
