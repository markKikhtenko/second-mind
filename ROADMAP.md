# Roadmap

## 0.1 — Working local MVP

- [x] Independent Core and tray desktop lifecycle
- [x] LM Studio Runtime adapter and multi-tool orchestration
- [x] Telegram text, voice, images and documents
- [x] Tasks, projects, notes, durable memory and reminders
- [x] stdio MCP server with strict schemas
- [x] faster-whisper model controls
- [x] SQLite migrations, FTS, queue and action log
- [x] PyInstaller/Inno Setup and Windows CI workflow

## 0.2 — Safe distribution and updates

- [x] Single per-user Windows Setup.exe with bundled runtimes and shortcuts
- [x] Configurable Windows autostart and data-preserving reinstall/uninstall
- [x] Dedicated Updates GUI with automatic/manual checks, changelog and progress
- [x] Ed25519-signed release manifest plus SHA-256 installer verification
- [x] Detached Core shutdown, database/application backup and automatic rollback
- [x] Release CI with clean-install, detached-updater and previous-version preservation tests

## 0.3 — Retrieval and operations

- [ ] Local embedding adapter and hybrid semantic/lexical ranking
- [ ] OCR for screenshots and scanned PDFs
- [ ] Editable agent definitions with per-tool approval policies
- [ ] Restore UI, encrypted portable backup and retention rules
- [x] Signed update manifest and in-app release installation

## 0.4 — Integrations

- [ ] CalDAV/Outlook adapters behind explicit consent
- [ ] Optional research provider with source capture
- [ ] Sandboxed third-party MCP management and OAuth status
- [ ] Proactive rules with reviewable triggers and budgets

## 1.0

- [ ] Accessibility and localization pass
- [x] Signed update metadata and GitHub build provenance
- [ ] Reproducible Authenticode-signed installer
- [ ] Threat-model review and migration compatibility guarantee
- [ ] Recovery testing and performance targets for large knowledge bases
