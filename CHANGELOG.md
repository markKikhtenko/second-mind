# Changelog

All notable changes follow Keep a Changelog. Versions use Semantic Versioning.

## [Unreleased]

## [0.3.1] - 2026-11-27

### Fixed

- Prevented a native Qt access violation at Desktop startup by capturing widget values on the GUI thread before automatic update checks run in a worker.
- Routed every background-worker completion callback explicitly through a GUI-thread QObject bridge.
- Removed the same unsafe cross-thread widget access from record loading/editing and Whisper model loading.
- Added an installed Desktop startup smoke test to the Windows release gate.
- Added a multi-resolution Windows icon for shortcuts, the application, installer, tray and High DPI displays.

## [0.3.0] - 2026-11-27

### Added

- Unified PySide6 design system with light, dark and system themes, shared tokens, reusable components and a consistent SVG icon set.
- Branded Second Mind application icon for the window, tray, packaged EXE, installer, Start menu and desktop shortcut.
- Grouped and collapsible navigation for Workspace, Intelligence Core and System sections.
- Live non-blocking Dashboard with Core uptime, LM Studio/model, Telegram, Whisper, MCP, CPU, RAM, GPU, VRAM, requests, queue, activity, errors and update status.
- Assistant conversation history, new/delete conversation actions, Markdown/code rendering, response copying, attachments, processing stages and tool-call summaries.
- Search, filtering, sorting, record cards and create/edit/delete flows for tasks, memory, projects and reminders, plus document and inbox management.
- Real LM Studio model discovery, Telegram token/connection testing and Whisper model/device/load/test/unload controls.
- Storage Manager with database, attachment and backup sizes, category counts, free space, schema version, integrity and last-backup information.
- WAL-safe SQLite Backup API archives containing the database, sanitized configuration and user attachments.
- Scheduled backups, retention pruning, restore, JSON export, SQLite integrity checks, optimization, temporary-file cleanup and safe storage relocation.
- Automated GUI construction tests, storage integration tests and real light/dark screenshots.

### Changed

- Rebuilt all desktop screens around the shared design system and Russian user-facing terminology.
- Raw JSON is limited to advanced diagnostics; regular screens use human-readable dates, statuses and labels.
- Long-running GUI operations use background workers so Dashboard, storage and diagnostics do not block Qt.
- The existing v0.2 installer, secure update, application/database backup and rollback architecture remains compatible.
- Application version is 0.3.0 across Python metadata, Core, Desktop and Inno Setup.

### Fixed

- Explicitly bound the packaged icon to Windows application identity and installed shortcut icon files.
- Corrected Cyrillic font rendering in generated desktop screenshots.
- Avoided late Qt worker signal delivery during application shutdown.

### Security

- Backup and export artifacts exclude secrets stored in Windows Credential Manager.
- Restore archives are validated against path traversal and database corruption before replacement.
- Destructive storage cleanup, restore and relocation operations require explicit confirmation.

## [0.2.0] - 2026-11-26

### Added

- Single per-user Windows Setup.exe bundling Core, Desktop, MCP, updater and runtime dependencies.
- Start menu/desktop shortcuts, installer autostart task and data-preserving uninstall/reinstall behavior.
- Dedicated Updates GUI with current/latest versions, release changelog, automatic checks and download progress.
- Ed25519-signed release manifests, SHA-256/size/source verification and optional Authenticode signing in CI.
- Detached update transaction with graceful Core shutdown, process quiescing, SQLite/application backups, post-install health validation and rollback.
- GitHub Actions release gate for clean installation, the detached updater transaction and upgrade from the previous stable installer while preserving data and settings.
- GitHub build-provenance attestation and signed release metadata assets.

### Changed

- Core and updater executables use the Windows GUI subsystem so no console window is shown.
- Application version is 0.2.0 and is reported consistently by Core and packaging.

## [0.1.0] - 2026-11-26

### Added

- Local FastAPI Core, PySide6 desktop and setup wizard.
- LM Studio Runtime provider with model discovery and multi-tool calling.
- Telegram long polling with allowlist, durable inbox and voice/image/document handling.
- Local SQLite tasks, projects, reminders, conversations, memory, FTS and deduplication.
- faster-whisper lifecycle controls and document extraction.
- MCP stdio server and permission-aware tool registry.
- Windows Credential Manager secrets, diagnostics, backup API and autostart support.
- Tests, Windows packaging scripts and GitHub Actions workflows.
