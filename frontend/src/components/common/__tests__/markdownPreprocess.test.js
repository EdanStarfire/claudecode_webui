import { describe, it, expect } from 'vitest'
import { preprocessLeadingThematicBreak, preprocessPortColons } from '../markdownPreprocess'

describe('preprocessLeadingThematicBreak', () => {
  it('fixes bug case: leading ---\\n with no closing delimiter', () => {
    expect(preprocessLeadingThematicBreak('---\n\n## Hi')).toBe('* * *\n\n## Hi')
  })

  it('leaves genuine frontmatter alone: closing \\n---\\n', () => {
    const input = '---\ntitle: x\n---\n# Body'
    expect(preprocessLeadingThematicBreak(input)).toBe(input)
  })

  it('leaves genuine frontmatter alone: closing \\n--- at EOF', () => {
    const input = '---\ntitle: x\n---'
    expect(preprocessLeadingThematicBreak(input)).toBe(input)
  })

  it('leaves content unchanged when --- is not on line 0', () => {
    const input = '# H\n\n---\nrest'
    expect(preprocessLeadingThematicBreak(input)).toBe(input)
  })

  it('leaves plain text unchanged', () => {
    const input = 'Hello world'
    expect(preprocessLeadingThematicBreak(input)).toBe(input)
  })

  it('leaves empty string unchanged', () => {
    expect(preprocessLeadingThematicBreak('')).toBe('')
  })

  it('returns non-string values as-is', () => {
    expect(preprocessLeadingThematicBreak(null)).toBe(null)
    expect(preprocessLeadingThematicBreak(undefined)).toBe(undefined)
    expect(preprocessLeadingThematicBreak(42)).toBe(42)
  })

  it('leaves --- without trailing newline unchanged', () => {
    expect(preprocessLeadingThematicBreak('---')).toBe('---')
  })

  it('fixes leading ---\\n followed by mid-line --- (not on own line)', () => {
    expect(preprocessLeadingThematicBreak('---\nfoo---bar')).toBe('* * *\nfoo---bar')
  })
})

describe('preprocessPortColons', () => {
  it('escapes :digits preceded by space', () => {
    expect(preprocessPortColons('listening on :8001'))
      .toBe('listening on \\:8001')
  })

  it('escapes :digits at start of string', () => {
    expect(preprocessPortColons(':3000 is open'))
      .toBe('\\:3000 is open')
  })

  it('escapes :digits at start of line (after \\n)', () => {
    expect(preprocessPortColons('hello\n:3000 is open'))
      .toBe('hello\n\\:3000 is open')
  })

  it('escapes :digits after punctuation', () => {
    expect(preprocessPortColons('try (:8080) now'))
      .toBe('try (\\:8080) now')
  })

  it('leaves :digits inside URLs alone', () => {
    const input = 'Visit http://localhost:8001/api'
    expect(preprocessPortColons(input)).toBe(input)
  })

  it('leaves host:port alone', () => {
    const input = 'connect to host:8001 now'
    expect(preprocessPortColons(input)).toBe(input)
  })

  it('leaves :digits in fenced code blocks (```) alone', () => {
    const input = 'see below\n```\nserver.listen(:3000)\n```\nafter'
    expect(preprocessPortColons(input)).toBe(input)
  })

  it('leaves :digits in fenced code blocks (~~~) alone', () => {
    const input = 'see below\n~~~\nserver.listen(:3000)\n~~~\nafter'
    expect(preprocessPortColons(input)).toBe(input)
  })

  it('leaves :digits in inline code spans alone', () => {
    const input = 'use `:8001` for now'
    expect(preprocessPortColons(input)).toBe(input)
  })

  it('leaves :digits in indented code blocks alone', () => {
    const input = 'paragraph\n\n    server.listen(:3000)\n\nmore prose'
    expect(preprocessPortColons(input)).toBe(input)
  })

  it('escapes multiple independent :digits in running text', () => {
    expect(preprocessPortColons('try :80, :443, and :8080'))
      .toBe('try \\:80, \\:443, and \\:8080')
  })

  it('handles mixed prose and fenced code', () => {
    const input = 'use :8001 then\n```\nlisten :3000\n```\nor :443'
    const expected = 'use \\:8001 then\n```\nlisten :3000\n```\nor \\:443'
    expect(preprocessPortColons(input)).toBe(expected)
  })

  it('handles mixed prose and inline code', () => {
    expect(preprocessPortColons('see `:3000` vs :3000'))
      .toBe('see `:3000` vs \\:3000')
  })

  it('returns plain text unchanged', () => {
    expect(preprocessPortColons('Hello world')).toBe('Hello world')
  })

  it('returns empty string unchanged', () => {
    expect(preprocessPortColons('')).toBe('')
  })

  it('returns non-string values as-is', () => {
    expect(preprocessPortColons(null)).toBe(null)
    expect(preprocessPortColons(undefined)).toBe(undefined)
    expect(preprocessPortColons(42)).toBe(42)
  })

  it('leaves text without colons unchanged via fast-path', () => {
    const input = 'no port refs here, just 8001 alone'
    expect(preprocessPortColons(input)).toBe(input)
  })

  it('leaves non-digit :name tokens alone', () => {
    const input = 'some :word here'
    expect(preprocessPortColons(input)).toBe(input)
  })

  // §4.1 — letter emoticons
  it('escapes :D in prose', () => {
    expect(preprocessPortColons('nice work :D'))
      .toBe('nice work \\:D')
  })

  it('escapes :P, :O, :S, :X uppercase emoticons', () => {
    expect(preprocessPortColons('feeling :P then :O then :S then :X'))
      .toBe('feeling \\:P then \\:O then \\:S then \\:X')
  })

  it('escapes :D at start of line', () => {
    expect(preprocessPortColons(':D wow'))
      .toBe('\\:D wow')
  })

  it('escapes :D followed by punctuation', () => {
    expect(preprocessPortColons('cool :D.'))
      .toBe('cool \\:D.')
  })

  it('escapes :D at end of string', () => {
    expect(preprocessPortColons('nice :D'))
      .toBe('nice \\:D')
  })

  // §4.2 — paren / pipe / semicolon emoticons
  it('escapes :) emoticon', () => {
    expect(preprocessPortColons('hello :) world'))
      .toBe('hello \\:) world')
  })

  it('escapes :( emoticon', () => {
    expect(preprocessPortColons('oh no :( sad'))
      .toBe('oh no \\:( sad')
  })

  it('escapes ;) wink', () => {
    expect(preprocessPortColons('right ;) ok'))
      .toBe('right \\;) ok')
  })

  it('escapes :| neutral emoticon', () => {
    expect(preprocessPortColons('meh :| whatever'))
      .toBe('meh \\:| whatever')
  })

  it('escapes mixed emoticons in one line', () => {
    expect(preprocessPortColons('hi :D, sad :(, wink ;)'))
      .toBe('hi \\:D, sad \\:(, wink \\;)')
  })

  // §4.3 — must NOT escape valid MDC components
  it('leaves multi-letter MDC component :Alert alone', () => {
    const input = 'see :Alert for details'
    expect(preprocessPortColons(input)).toBe(input)
  })

  it('leaves :Alert{type="info"} component invocation alone', () => {
    const input = ':Alert{type="info"} heads up'
    expect(preprocessPortColons(input)).toBe(input)
  })

  it('leaves single-letter MDC component with attributes :D{prop=x} alone', () => {
    const input = ':D{prop=x} component'
    expect(preprocessPortColons(input)).toBe(input)
  })

  it('leaves :Door (starts with :D but continues with letters) alone', () => {
    const input = 'open the :Door now'
    expect(preprocessPortColons(input)).toBe(input)
  })

  // §4.4 — code / table / boundary preservation
  it('leaves emoticons in fenced code blocks alone', () => {
    const input = 'see\n```\nprint(":D")\nemoji = ":)"\n```\nafter'
    expect(preprocessPortColons(input)).toBe(input)
  })

  it('leaves emoticons in inline code spans alone', () => {
    const input = 'use `:D` literally'
    expect(preprocessPortColons(input)).toBe(input)
  })

  it('leaves emoticons in indented code blocks alone', () => {
    const input = 'para\n\n    log(":D happened")\n\nmore'
    expect(preprocessPortColons(input)).toBe(input)
  })

  it('leaves GFM table alignment row unchanged', () => {
    const input = '| col | num |\n|---:|:---|\n| 1   | a   |'
    expect(preprocessPortColons(input)).toBe(input)
  })

  it('leaves lowercase :d alone (not a conventional emoticon)', () => {
    const input = 'the :d thing'
    expect(preprocessPortColons(input)).toBe(input)
  })

  it('leaves :D embedded after a word character alone', () => {
    const input = 'abc:D thing'
    expect(preprocessPortColons(input)).toBe(input)
  })
})
