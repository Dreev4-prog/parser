# v4.23.12 Local Validation

Source: complete reconstructed DT Parser v4.23.12 tree, based on v4.23.11. No live Railway or marketplace connection.

- Recursive Python compile: PASS
- Release smoke: PASS
- Runtime global-symbol audit: PASS
- pytest: 255 passed + 167 subtests passed; 0 failed on the clean-overlay reconstruction
- New v4.23.12 behavioral tests: 12 passed; actual SQLite SQLAlchemy execution, source-function AST loading, and additive DDL checks.
- Warnings: primarily Python 3.13 datetime.utcnow deprecations; no failed tests.
- PostgreSQL DDL compilation checked without a live server. SQLite migration block executed twice against a pre-upgrade schema, preserving rows and intentional NULL current evidence.

The full test suite is not a substitute for real PostgreSQL multi-replica concurrency tests or production traffic measurements. The validation record will be updated after the clean-overlay run.

Clean-overlay check: all 21 patch files reproduced the working tree hashes. Compile, release smoke, global-symbol audit and full pytest passed after applying the archive.
