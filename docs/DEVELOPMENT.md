# Development notes

Status: experimental and under active development. Personal, noncommercial use only.

## Verification

After installing the pinned dependencies into `.venv`, run:

```powershell
.\build\Verify.ps1
```

The script runs the production `unittest` modules, `pip check`, and the frontend
JavaScript syntax check. `test_discovery.py` and `test_ranking.py` are paused draft
contracts excluded by that script; they are not part of the passing production suite.
Do not interpret a passing production suite as a claim that these drafts pass.

The `-Live` option performs additional external-site checks and may require local
client configuration, a running service, network access, and manual website login.
Unit fixtures prove code behavior, not current website availability or account access.

One-off diagnostic scripts under `tests/` and archive inspection scripts under
`build/` can require separately supplied local evidence. Raw historical evidence
and source archives are not distributed. New reports belong under `evidence/`
and should be reviewed for private information before sharing.

## Source snapshot

The GitHub repository starts from the current source snapshot rather than uploading
the private development Git history. It contains the runtime, build and integration
scripts, tests, dependency pins, and current documentation. Raw handoffs, prompts,
screenshots, account/session data, downloaded media, and local environment files
are omitted. Historical helper names use neutral terms such as `Reconcile-Handoff`.

Keep real credentials out of tests and reports. Session tests use temporary browser
profiles and synthetic local fixtures, preserving ordinary user browser sessions.

Publication verification on 2026-10-21: 260 production tests passed; dependency
checks, frontend syntax validation, and an isolated dependency-free wheel build
also passed. The two paused draft contracts remain excluded.
