# Storage Manager

Second Mind keeps its primary data in local SQLite. No PostgreSQL, MongoDB or external database service is required.

## Layout

The default data directory is `%LOCALAPPDATA%\Second Mind\Second Mind` and contains:

- `second-mind.db` — the SQLite database;
- `attachments` — user documents and media;
- `backups` — Storage Manager archives;
- `updates` and `update-backups` — secure updater state;
- `config.json` — non-secret application configuration.

Telegram tokens and other secrets stay in Windows Credential Manager and are never written to backup manifests or JSON exports.

## Backup

Backups are ZIP archives created from a consistent SQLite snapshot through `sqlite3.Connection.backup()`. This remains safe while the working database uses WAL. The archive contains:

- the SQLite snapshot;
- sanitized application configuration;
- user attachments;
- a versioned manifest with creation time and file metadata.

Automatic backup can be enabled in Settings. The interval and retention period are configurable; expired archives are pruned after a successful backup.

## Restore and relocation

Restore requires an explicit GUI confirmation. The archive is extracted into a temporary staging directory, every path is checked against traversal, and SQLite integrity is verified before replacement. An emergency safety backup is created first.

Relocation also requires confirmation. Data is copied into a staging directory at the destination, the copied database is checked, and only then is the configured location switched. A failed copy leaves the current storage untouched.

## Maintenance

- **Integrity check** runs SQLite `PRAGMA integrity_check`.
- **Optimize database** checkpoints WAL and runs `VACUUM`.
- **Clean temporary files** removes only known temporary artifacts after confirmation.
- **Export data** writes user records to a readable JSON file without credentials.
- **Open folder** opens the active data directory in Windows Explorer.

All long-running operations are dispatched outside the GUI thread.
