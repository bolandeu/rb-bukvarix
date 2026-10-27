---
id: BUKVARIX-001
title: 'Deps: setuptools for GHSA-xmj3-9gf7-h52w'
status: ideas
attempts: 0
blocked: false
blocked_reason: null
phase: null
tests_hash: null
publish_stage: null
created_by: architect
approved_by: null
worker: claude-code
type: chore
owns_paths:
- uv.lock
test_paths: []
docs: none
tools: []
worker_args: null
class: mechanical
decision: false
visual: null
depends_on: []
budget_usd: null
epic: null
spec_refs: []
checks:
- uv run pytest
resident: null
tool: null
pr: null
---

osv-scanner flags setuptools 84.0.0 (GHSA-xmj3-9gf7-h52w, fixed in 84.1.0) in uv.lock. Run 'uv lock --upgrade-package setuptools', then 'uv run pytest'. Change only uv.lock. No ignores, no osv config. If 84.1.0 or newer is not reachable (a pin above it), do not work around it: report which package holds it back and block.

## Acceptance

- Given uv.lock, When the gate's vulnerability audit runs, Then osv-scanner reports none; the tests pass. [check: uv run pytest]

## Out of scope

- Other dependency upgrades; osv ignores or config
