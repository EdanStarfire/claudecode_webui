"""Backend: single-tenant control plane (session execution, Legion, MCP tooling).

See plan-498-final.md (issue #498) for the architecture this package implements.
Structurally separate from the Frontend API (src/) — nothing under src/ may import
from this package (enforced by src/tests/test_import_boundary.py).
"""
