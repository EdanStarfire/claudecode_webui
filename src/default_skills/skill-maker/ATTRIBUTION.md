# Attribution

This skill is adapted from the **skill-creator** skill by Anthropic.

- **Original source**: https://github.com/anthropics/skills/tree/main/skills/skill-creator
- **Copyright**: Copyright (c) Anthropic, PBC
- **License**: Apache License 2.0 (see https://github.com/anthropics/skills/blob/main/skills/skill-creator/LICENSE.txt)

## Modifications

- Renamed from `skill-creator` to `skill-maker` to avoid collision with the original
- Trimmed to creation, validation, update, and delete guidance only
- Removed eval/benchmark/iteration/blind-comparison/packaging/description-optimization infrastructure
- Removed agents/, eval-viewer/, assets/, claude.ai/cowork sections
- Removed PyYAML dependency from validation scripts (replaced with string-based frontmatter parsing)
- Added `restart_session` MCP tool guidance for minion context (session reload after skill changes)
