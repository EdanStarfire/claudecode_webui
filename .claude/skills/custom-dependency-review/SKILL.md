---
name: custom-dependency-review
description: Project-specific Dependabot PR review process. Diffs the bumped package's source, cross-references usage in claudecode_webui, and reports findings.
allowed-tools: [Bash, WebFetch, Read, Grep]
---

# Custom Dependency Review

## Purpose

This is a **project-specific custom skill** for reviewing a Dependabot dependency-bump PR
beyond "CI passes." It diffs the bumped package's old vs. new source, identifies changed
functions/APIs, cross-references this codebase's actual usage, analyzes impact for affected
call sites, and runs the full test suite as a final gate — not the only gate.

## When Called

Invoked manually against an open Dependabot PR — e.g. "run the dependency review process on
PR #1624" — not part of the automatic per-issue Builder pipeline.

## Input

- `pr_number`: The Dependabot PR number to review (e.g., 1624)

## Process

### 1. Gather PR facts

```bash
gh pr view <pr_number> --json title,body,files,headRefName,baseRefName,state,updatedAt
```

Extract: package name, old version, new version, ecosystem (uv/pypi vs npm_and_yarn), and
which files changed. Confirm the PR is still open and hasn't drifted.

### 2. Determine direct vs. transitive scope

```bash
# Python: is it a direct dependency?
grep -n "<package>" pyproject.toml

# Python: which package(s) pull it in transitively? Match the exact
# dependency-declaration form, not a bare substring — a bare package-name
# match also hits every wheel/sdist URL in that package's own block and
# buries the real signal in noise.
awk '/^\[\[package\]\]/{pkg=""} /^name = /{pkg=$0} /{ name = "<package>" }/{print pkg}' uv.lock

# npm: is it a direct dependency?
grep -n "\"<package>\"" frontend/package.json
```

If **transitive**, scope down per AC3: skip tracing call sites through the intermediate
dependency's internals. Review the package's own changelog for consumer-facing breaking
changes broad enough to matter generically, rely more on the test-suite gate, and say so
explicitly in findings.

### 3. Source diff of the bumped package

- Prefer the GitHub compare view between version tags via WebFetch, or clone both tags
  shallowly and `diff -rq` them.
- For a multi-major jump, read each major version's CHANGELOG/migration-guide breaking-changes
  section rather than raw diff noise.
- If no meaningful source diff is available, fall back to changelog/release-notes review and
  say so explicitly.

### 4. Identify changed functions/APIs

List functions/classes/config options that changed: signature changes, changed defaults,
deprecations, removals. Keep only what's relevant to how this repo could plausibly use the
package.

### 5. Cross-reference codebase usage

```bash
# Python
grep -rn "^import <package>\|^from <package>" src --include="*.py"
grep -rn "<package>\." src --include="*.py"

# JS/TS
grep -rn "from ['\"]<package>['\"]" frontend/src frontend/*.config.js
```

### 6. Sinks-and-sources impact analysis

For each call site that intersects a changed API: trace what flows into the call (sources)
and what the code does with the return value/side effects (sinks). Judge whether the changed
behavior could alter that flow silently.

### 7. Run the test suite as final gate

```bash
uv run pytest src/tests/ -v      # uv/pypi-ecosystem bumps
cd frontend && npm run test      # npm-ecosystem bumps
```

Run against a checkout that actually has the bump applied. Confirmation, not a substitute for
steps 3–6.

### 8. Report findings

Draft findings using the Output Format below. **Do not post to GitHub automatically** —
present the draft for review first; only post via `gh pr comment` once told to proceed.

## Output Format

```markdown
## Dependency Review: <package> <old> → <new>

**Scope:** Direct / Transitive (via <parent-package>)
**Diff source:** <link used>

### Changed APIs relevant to this codebase
<list, or "None — no overlap">

### Call sites reviewed
<file:line list, or "N/A — not directly imported">

### Impact analysis
<per-call-site notes, or scale-down justification per AC3>

### Test suite result
- `pytest`: PASS/FAIL (N passed, M failed)
- `vitest`: PASS/FAIL (N passed, M failed)

### Verdict
Safe to merge / Needs human attention because <reason>
```

## Usage

Standalone, manually-invoked skill:
```
Invoke custom-dependency-review skill with pr_number=1624
```
