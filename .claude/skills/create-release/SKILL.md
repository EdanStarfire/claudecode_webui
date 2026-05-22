---
name: create-release
description: Create a versioned release of claudecode_webui — bump version fields, update CHANGELOG.md, tag the commit, push, and create the GitHub Release.
---

# Create Release

## Purpose

Tag a stable commit on `main` as an official release of `claudecode_webui`, update
the changelog, and publish a GitHub Release so self-hosters know a stable baseline exists.

This skill is for **repository management only** — it has no effect on running app instances.

## Arguments

- `<version>` — required. The version number to release, without the `v` prefix. Examples: `1.0.0`, `1.1.0`, `1.0.3`.
- `[notes hint]` — optional. A one-sentence theme for this release (e.g. "navigation reliability fixes"). Used to frame the CHANGELOG entry if no detailed notes are supplied interactively.

## Versioning Scheme (SemVer)

`MAJOR.MINOR.PATCH`

| Segment | When to bump | Examples |
|---------|-------------|---------|
| **PATCH** | Bug fixes, polish, dependency bumps with no behavior change | `:8001` crash fix, message contamination fix, tint colour tweak |
| **MINOR** | New backward-compatible features | Message Queue improvements, Legion minion tree, MCP OAuth support |
| **MAJOR** | Breaking changes requiring user action to upgrade | Docker setup change, DB schema migration, config format change, renamed env vars |

Pre-1.0 (`0.x.y`) signals unstable API; `1.0.0` signals the project is self-hostable and intentionally stable.

## Pre-Release Checklist

Run these before starting. Stop and resolve any failures.

```bash
# Must be on main
git branch --show-current

# Must be clean (no uncommitted changes)
git status --short

# Must be up to date with remote
git fetch origin && git status -sb | head -1

# Confirm all intended PRs are merged
gh pr list --repo EdanStarfire/claudecode_webui --state open
```

If there are open PRs that belong in this release, merge them first.

## Step-by-Step Process

### 1. Confirm the version

Decide which SemVer segment to bump (see table above).
The new version is `<MAJOR>.<MINOR>.<PATCH>` — no `v` prefix in the files, `v` prefix on the tag.

### 2. Update version fields in source files

Two files track the version number:

**`pyproject.toml`** — Python backend:
```toml
version = "<new-version>"
```

**`frontend/package.json`** — Vue frontend:
```json
"version": "<new-version>",
```

Edit both. Do NOT edit `frontend/package-lock.json` — `npm install` regenerates it; the version field there updates when the user next runs npm install, which is fine.

### 3. Update CHANGELOG.md

The changelog lives at the project root: `CHANGELOG.md`.

**Format — [Keep a Changelog](https://keepachangelog.com):**

```markdown
# Changelog

All notable changes to claudecode_webui are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning: [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [<version>] - <YYYY-MM-DD>

### Added
- Brief description of new features

### Changed
- Behaviour changes, dependency updates, refactors visible to users

### Fixed
- Bug fixes (reference issue numbers where helpful, e.g. "Fix #1511 — …")

### Removed
- Removed features or deprecated items

[<version>]: https://github.com/EdanStarfire/claudecode_webui/releases/tag/v<version>
```

**Rules:**
- Newest release always appears at the top, below `## [Unreleased]`.
- Keep `## [Unreleased]` as an empty placeholder for ongoing work.
- Use plain English — describe user-visible impact, not internal implementation details.
- Omit sections that have no entries for this release.
- Reference issue numbers for bug fixes (`Fix #1511`).

**First release (v1.0.0):** Create `CHANGELOG.md` from scratch. Summarise the project's
major capabilities (not every commit) — aim for 15–30 bullet points covering the notable
features shipped since the project started. Use closed issues and merged PRs for reference:
```bash
gh pr list --repo EdanStarfire/claudecode_webui --state merged --limit 50 \
  --json number,title,mergedAt | jq 'sort_by(.mergedAt) | reverse | .[] | "#\(.number) \(.title)"'
```

**Subsequent releases:** Add only changes since the previous tag:
```bash
git log v<previous>..HEAD --oneline --merges | head -40
gh pr list --repo EdanStarfire/claudecode_webui --state merged \
  --search "merged:>$(git log v<previous> -1 --format=%aI)" \
  --json number,title --jq '.[] | "#\(.number) \(.title)"'
```

### 4. Commit the version bump and changelog

Stage only the version files and changelog — do not accidentally include unrelated
uncommitted work:

```bash
git add pyproject.toml frontend/package.json CHANGELOG.md
git status  # verify only the right files are staged
git commit -m "chore: release v<version>

Update version fields and changelog for v<version>."
```

### 5. Create the annotated tag

Annotated tags carry a message and appear properly in GitHub Releases:

```bash
git tag -a v<version> -m "Release v<version>"
```

### 6. Push the commit and tag

```bash
git push origin main
git push origin v<version>
```

Verify the tag appears on GitHub:
```bash
gh api repos/EdanStarfire/claudecode_webui/git/refs/tags | jq '.[-1]'
```

### 7. Create the GitHub Release

```bash
gh release create v<version> \
  --repo EdanStarfire/claudecode_webui \
  --title "v<version>" \
  --notes-file <(sed -n '/^## \[<version>\]/,/^## \[/p' CHANGELOG.md | head -n -1)
```

This extracts the relevant CHANGELOG section and uses it as the release body.
GitHub automatically attaches a source `.zip` and `.tar.gz` — no manual artifact
upload is needed for a source-only release.

Verify the release was created:
```bash
gh release view v<version> --repo EdanStarfire/claudecode_webui
```

### 8. Confirm and report

After the release is published:
- Report the release URL to the user: `https://github.com/EdanStarfire/claudecode_webui/releases/tag/v<version>`
- Confirm the tag, release page, and source archives are visible
- Note that self-hosters can now pin to `v<version>` via `git checkout v<version>`

## Ongoing Release Cadence

There is no fixed schedule. A release is appropriate when:
- A meaningful batch of user-visible fixes or features has accumulated on `main`
- A critical bug fix needs to be clearly marked for self-hosters to find
- A breaking change requires users to take action on upgrade

Releases do not need to be frequent — monthly or on significant milestones is fine.
Every commit to `main` is already stable (squash-merged PRs); tagging is about
communicating intentional stable points, not gatekeeping code quality.

## What NOT to do

- Do not tag a commit that has failing tests or a known broken state.
- Do not skip the CHANGELOG update — it is the human-readable record self-hosters rely on.
- Do not use lightweight tags (`git tag v1.0.0`) — always use annotated tags (`git tag -a`).
- Do not force-push or move a published tag — if a tag was pushed with an error, create a new patch release instead.
