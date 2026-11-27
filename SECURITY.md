# Security policy

## Defaults

Second Mind is local-first. Core binds only to loopback and validation rejects non-loopback LLM URLs. Telegram uses long polling, so no inbound port is exposed. A non-empty User ID allowlist is mandatory. Secrets are stored via Windows Credential Manager and are never returned by the API.

Retrieved memory, attachments, documents and forwarded messages are treated as untrusted content. They are isolated with data markers in model context and never grant tools. Tool arguments are validated by Pydantic. Agent allowlists and `auto`, `confirm`, `deny` policies are enforced by the backend; prompts and imported Skills cannot bypass them. Dry Run validates mutating calls without invoking their handlers. Pending approvals bind the exact agent, tool and arguments before Desktop or Telegram can execute them.

Windows updates are accepted only from the configured GitHub repository when the release contains an Ed25519-signed manifest matching the repository, tag, version, installer name, size and SHA-256. The detached updater performs the same verification again before changing the installation. A failed installer or post-update Core health/version check restores both the previous program files and the pre-migration SQLite backup. GitHub Actions also publishes a build-provenance attestation; Authenticode signing is applied when the repository certificate secrets are configured.

## Reporting

Do not open a public issue containing tokens, personal data, a local database or private prompts. Use GitHub private vulnerability reporting after the repository is published.

## Known MVP boundaries

- The local desktop user can access the loopback API; OS account isolation is the trust boundary.
- SQLite and attachments are not encrypted at rest. Use BitLocker/EFS when device theft is in scope.
- Local models vary in tool-call reliability. Tool validation limits effects but cannot guarantee correct intent interpretation.
- Third-party MCP servers are not installed automatically. Review their permissions and source before adding them.
- Agent prompts and Skills are powerful configuration. Import only trusted files; their instructions still cannot grant a backend tool, but they can change how an allowed model action is selected.
- The embedded public update key authenticates Second Mind release metadata. Protect the corresponding GitHub Actions secret and rotate it only through a compatibility release.
