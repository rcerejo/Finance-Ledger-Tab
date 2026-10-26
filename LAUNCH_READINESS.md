# Launch Readiness

Finance Ledger Lab is currently intended for one person running it locally. The Streamlit app accepts PDF/CSV uploads and a SQLite path, but has no authentication or user-level data isolation. Multiple users of one hosted instance would share the same database. Keep it bound to localhost and do not expose it as a shared public service with real bank data.

## Before a public multi-user launch

- Choose an identity provider and require authentication for every app route.
- Replace the shared SQLite file with storage that supports authenticated per-user access; enforce ownership in the data layer, not only in Streamlit filters.
- Define statement upload limits, private temporary-file handling, retention/deletion behavior, backups, and recovery procedures.
- Encrypt stored files and databases, restrict service credentials, and keep secrets outside the repository.
- Decide whether parsing stays local to the user's machine or sends documents to a hosted model. If hosted, obtain explicit consent, minimize/redact data, and document retention and vendor handling.
- Add security checks for uploaded files, authorization tests between users, dependency scanning, and an operational error/audit trail that does not log sensitive statement contents.
- Add production monitoring, resource limits, deployment configuration, and a tested restore process.

## Sensible next release target

Make the current local workflow reliable for one user first: run the documented pipeline from a clean checkout, review parser validation output, and keep statements and SQLite databases out of Git. The CI workflow covers synthetic inputs only and does not establish accuracy for every bank's statement format.
