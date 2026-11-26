-- Human-readable mirror of migration 1 embedded in second_mind.db.MIGRATIONS.
-- The application applies the embedded transaction automatically at Core startup.
-- See src/second_mind/db.py for the executable schema, including FTS triggers.
CREATE TABLE schema_migrations (
  version INTEGER PRIMARY KEY,
  applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

