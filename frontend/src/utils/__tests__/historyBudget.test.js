import { describe, it, expect } from 'vitest'
import {
  MIB, DESKTOP_BUDGET_BYTES, PHONE_BUDGET_BYTES, getHistoryBudgetBytes, isPhoneProfile, backgroundKeepBytes, trimTargetBytes,
} from '../historyBudget'

// Issue #2030 stage 4 PR 4d: budget constants and the device profile detection.

const desktop = { innerWidth: 1440, navigator: { deviceMemory: 8 }, matchMedia: () => ({ matches: false }), storage: null }

describe('getHistoryBudgetBytes', () => {
  it('is 64 MiB on a desktop and 32 MiB on a phone profile', () => {
    expect(getHistoryBudgetBytes(desktop)).toBe(64 * MIB)
    expect(DESKTOP_BUDGET_BYTES).toBe(64 * MIB)
    expect(PHONE_BUDGET_BYTES).toBe(32 * MIB)
    expect(getHistoryBudgetBytes({ ...desktop, innerWidth: 390 })).toBe(32 * MIB)
  })

  it('treats <= 4 GB device memory and a coarse pointer as phone-class', () => {
    expect(isPhoneProfile({ ...desktop, navigator: { deviceMemory: 4 } })).toBe(true)
    expect(isPhoneProfile({ ...desktop, navigator: { deviceMemory: 8 } })).toBe(false)
    expect(isPhoneProfile({ ...desktop, navigator: {} })).toBe(false)
    expect(isPhoneProfile({ ...desktop, matchMedia: (q) => ({ matches: q === '(pointer: coarse)' }) })).toBe(true)
    expect(isPhoneProfile({ ...desktop, matchMedia: () => { throw new Error('unsupported') } })).toBe(false)
  })

  it('honours the localStorage override (MiB) and ignores garbage', () => {
    const storage = (value) => ({ getItem: () => value })
    expect(getHistoryBudgetBytes({ ...desktop, storage: storage('10') })).toBe(10 * MIB)
    expect(getHistoryBudgetBytes({ ...desktop, innerWidth: 390, storage: storage('96') })).toBe(96 * MIB)
    expect(getHistoryBudgetBytes({ ...desktop, storage: storage('abc') })).toBe(64 * MIB)
    expect(getHistoryBudgetBytes({ ...desktop, storage: storage('-5') })).toBe(64 * MIB)
    expect(getHistoryBudgetBytes({ ...desktop, storage: { getItem: () => { throw new Error('denied') } } })).toBe(64 * MIB)
  })
})

describe('derived sizes', () => {
  it('background sessions keep min(8 MiB, budget/4)', () => {
    expect(backgroundKeepBytes(64 * MIB)).toBe(8 * MIB)
    expect(backgroundKeepBytes(32 * MIB)).toBe(8 * MIB)
    expect(backgroundKeepBytes(16 * MIB)).toBe(4 * MIB)
  })
  it('an idle trim stops at 0.75 x budget', () => {
    expect(trimTargetBytes(64 * MIB)).toBe(48 * MIB)
    expect(trimTargetBytes(32 * MIB)).toBe(24 * MIB)
  })
})
