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
- [x] Updates GUI with automatic/manual checks, changelog and progress
- [x] Ed25519-signed manifest and SHA-256 verification
- [x] Detached shutdown, database/application backup and automatic rollback
- [x] CI clean-install, detached-updater and previous-version preservation tests

## 0.3 — UI/UX and Storage Management

- [x] Unified light, dark and system design system with SVG icons and High DPI support
- [x] Grouped collapsible navigation and honest status labels for future features
- [x] Live Dashboard without blocking the GUI or displaying raw JSON
- [x] Redesigned assistant with history, Markdown, files, stages and tool-call visibility
- [x] Searchable and editable tasks, memory, projects, documents, inbox and reminders
- [x] Real LM Studio, Telegram and Whisper diagnostics and controls
- [x] Complete SQLite Storage Manager with backup, restore, export and integrity checks
- [x] Scheduled backups, retention, optimization, cleanup and safe data relocation
- [x] Branded application, installer, tray and shortcut icon
- [x] Russian localization, release documentation and real theme screenshots
- [x] Preservation of the v0.2 installation, update and rollback mechanism

## 0.4 — Retrieval and knowledge

- [ ] Local embedding adapter and hybrid semantic/lexical ranking
- [ ] OCR for screenshots and scanned PDFs
- [ ] RAG quality evaluation and source-aware answers
- [ ] Large-library indexing and performance targets

## 0.5 — Extensibility and integrations

- [ ] Editable agent definitions with per-tool approval policies
- [ ] Expanded Skills library and lifecycle management
- [ ] Sandboxed third-party MCP management and OAuth status
- [ ] CalDAV/Outlook adapters behind explicit consent
- [ ] Optional research provider with source capture

## 1.0

- [ ] Full accessibility audit and additional localizations
- [x] Signed update metadata and GitHub build provenance
- [ ] Reproducible Authenticode-signed installer
- [ ] Threat-model review and migration compatibility guarantee
- [ ] Recovery testing for large production knowledge bases
