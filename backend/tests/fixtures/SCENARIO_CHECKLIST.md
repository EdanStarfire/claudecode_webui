# Recording Scenario Checklist (superseded — issue #2038)

This manual checklist has been replaced by the scripted scenario driver
(`backend/tools/scenario_driver/`). The 22 scenarios that reproduce the
`2026-09-23-primary` fixture — and every other equivalence-fixture recording
— are now defined as data in `backend/tools/scenario_driver/scenarios.py`
(`build_scenarios()`), which is the source of truth (issue #2038's AC8).

`coverage_markers` on each `Scenario` cross-references
`backend.fixture_export.REQUIRED_MARKERS`; a self-check test
(`backend/tests/test_scenario_driver_dry_run.py::test_required_markers_all_covered_by_scenarios`)
asserts every required marker is claimed by at least one scenario.

To re-record a fixture, run `backend/tools/scenario_driver_cli.py` against a
test instance with real credentials (see its `--help`) rather than following
this checklist by hand.
