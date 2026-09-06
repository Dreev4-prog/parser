# v4.23.7 GitHub patch — Full Audit Hardening

Apply **on top of v4.23.6** and preserve all paths from this archive.

After push/redeploy:

- redeploy **Parser / Bot** — required;
- redeploy all **View Worker** replicas — recommended for one-version consistency;
- Page Worker / Date Worker can remain running because their functional behavior is unchanged, though same-checkout redeploy is safe;
- Vinted Scan / Metrics / Session workers do not need a restart specifically for this patch.

No manual SQL migration and no new required Railway variables.

Main fixes: real idle x8 traffic borrowing, completed View Worker shard preservation on deadlines, and repaired full release QA. See `RELEASE_4_23_7.md` and `AUDIT_4_23_7_FULL.md`.
