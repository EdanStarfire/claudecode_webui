import { describe, it, expect, beforeEach } from 'vitest'
import {
  getStoppedSet,
  setStoppedSet,
  addToStoppedSet,
  clearStoppedSet,
  pruneStoppedSet,
  removeFromStoppedSet,
} from '@/utils/stoppedSet'

const PROJECT = 'proj-abc'

beforeEach(() => {
  clearStoppedSet(PROJECT)
})

describe('removeFromStoppedSet', () => {
  it('removes listed ids and returns the remainder', () => {
    setStoppedSet(PROJECT, ['a', 'b', 'c'])
    const remaining = removeFromStoppedSet(PROJECT, ['b'])
    expect(remaining).toEqual(['a', 'c'])
    expect(getStoppedSet(PROJECT)).toEqual(['a', 'c'])
  })

  it('removes multiple ids at once', () => {
    setStoppedSet(PROJECT, ['a', 'b', 'c', 'd'])
    const remaining = removeFromStoppedSet(PROJECT, ['a', 'c'])
    expect(remaining).toEqual(['b', 'd'])
  })

  it('clears the storage key when all ids are removed', () => {
    setStoppedSet(PROJECT, ['x'])
    removeFromStoppedSet(PROJECT, ['x'])
    expect(getStoppedSet(PROJECT)).toEqual([])
    expect(sessionStorage.getItem(`webui:stopped-set:${PROJECT}`)).toBeNull()
  })

  it('is a no-op when the id is not in the set', () => {
    setStoppedSet(PROJECT, ['a', 'b'])
    const remaining = removeFromStoppedSet(PROJECT, ['z'])
    expect(remaining).toEqual(['a', 'b'])
  })

  it('is a no-op when the set is empty', () => {
    const remaining = removeFromStoppedSet(PROJECT, ['a'])
    expect(remaining).toEqual([])
  })

  it('round-trip: add then remove leaves expected remainder', () => {
    addToStoppedSet(PROJECT, ['s1', 's2', 's3'])
    removeFromStoppedSet(PROJECT, ['s2'])
    expect(getStoppedSet(PROJECT)).toEqual(['s1', 's3'])
  })
})
