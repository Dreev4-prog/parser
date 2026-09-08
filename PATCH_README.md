# v4.23.12 GitHub patch — Radar 48H Demand Quality

Apply **on top of v4.23.11**, preserving paths. This is a Kleinanzeigen Radar-only patch. Do not use it as a replacement for an entire repository.

Required: redeploy Parser / Bot and all Lifecycle Worker replicas from the same commit after the additive migration completes. Other workers are functionally unchanged. No manual SQL or new required Railway variables. Back up PostgreSQL first. Do not reset the database, clear Redis, or bulk-restore History. See RELEASE_4_23_12.md for the 48h/6h distinction, evidence safeguards, deployment order, rollback limitations, and validation scope.
