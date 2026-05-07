"""Lint guard for issue #1331 — fail if any flat CONFIG_FIELD access reappears.

Walks src/ and frontend/src/ for `<subject>.<field>` patterns where:
  - subject ∈ {session, session_info, sinfo, _sinfo, _session, template,
               _template, child_info, parent_info, minion_info, info, _info}
  - field   ∈ CONFIG_FIELDS

Allowlists the dataclass-definition modules and migration helpers.
"""
import re
import tokenize
from pathlib import Path

import pytest

from src.session_config import CONFIG_FIELDS

PY_SUBJECT_NAMES = {
    "session", "session_info", "sinfo", "_sinfo", "_session",
    "template", "_template",
    "child_info", "parent_info", "minion_info", "info", "_info",
}

# Files allowed to reference flat fields (definitions / migration / tests)
ALLOWED_PY_FILES = {
    "src/session_config.py",       # CONFIG_FIELDS definition
    "src/session_manager.py",      # SessionInfo dataclass + migration helper
    "src/models/minion_template.py",  # MinionTemplate dataclass
    "src/template_manager.py",     # MinionTemplate migration helper
    "src/tests/test_no_flat_config_field_access.py",  # this test
}

REPO_ROOT = Path(__file__).resolve().parents[2]


def _scan_python_file(path: Path) -> list[tuple[int, str, str]]:
    """Return list of (line_no, subject, field) violations."""
    violations = []
    with path.open("rb") as fp:
        tokens = list(tokenize.tokenize(fp.readline))
    for i in range(len(tokens) - 2):
        a, b, c = tokens[i], tokens[i + 1], tokens[i + 2]
        if (a.type == tokenize.NAME and a.string in PY_SUBJECT_NAMES
                and b.type == tokenize.OP and b.string == "."
                and c.type == tokenize.NAME and c.string in CONFIG_FIELDS):
            violations.append((a.start[0], a.string, c.string))
    return violations


def test_no_flat_config_field_access_in_python():
    """No `session.<config_field>` style access outside the dataclass owner."""
    src = REPO_ROOT / "src"
    failures: list[str] = []
    for py_file in src.rglob("*.py"):
        rel = py_file.relative_to(REPO_ROOT).as_posix()
        if rel in ALLOWED_PY_FILES:
            continue
        for line_no, subject, field in _scan_python_file(py_file):
            failures.append(f"{rel}:{line_no}: {subject}.{field}")
    if failures:
        msg = "\n".join(failures)
        pytest.fail(
            f"Found {len(failures)} flat CONFIG_FIELD access(es) — "
            f"migrate to .config.get(...) or resolve_effective_config():\n{msg}"
        )


VUE_SUBJECT_GROUP = (
    r"(session|sessionInfo|template|currentSession|selectedSession|"
    r"minion|info|props\.session)"
)
VUE_RE = re.compile(
    rf"\b{VUE_SUBJECT_GROUP}"
    rf"\.({'|'.join(re.escape(f) for f in CONFIG_FIELDS)})\b"
)


def test_no_flat_config_field_access_in_frontend():
    """Same guard for Vue/JS sources."""
    fe = REPO_ROOT / "frontend" / "src"
    if not fe.exists():
        pytest.skip("frontend/src not present")
    failures: list[str] = []
    for path in list(fe.rglob("*.vue")) + list(fe.rglob("*.js")):
        rel = path.relative_to(REPO_ROOT).as_posix()
        for line_no, line in enumerate(path.read_text().splitlines(), 1):
            stripped = line.lstrip()
            if stripped.startswith(("//", "*", "<!--")):
                continue
            for m in VUE_RE.finditer(line):
                failures.append(f"{rel}:{line_no}: {m.group(0)}")
    if failures:
        pytest.fail(
            f"Found {len(failures)} flat CONFIG_FIELD access(es) in frontend — "
            f"use session.config?.<field>:\n" + "\n".join(failures)
        )
