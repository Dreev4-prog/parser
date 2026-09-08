# v4.23.11 — Radar Live & Checkpoint Visibility

Apply on top of **v4.23.10**, preserving archive paths. This is a Kleinanzeigen Radar 3.2-only patch.

Replace the files in this archive and push to GitHub. Redeploy **Parser / Bot**. Other workers are functionally unchanged; if Railway automatically redeploys them from the same commit, that is fine. No new required variables and no manual SQL migration. The new `radar_checkpoint_events` table is created automatically by the existing serialized `init_db()` path.

Do not delete existing Radar data, reset the database or clear Redis. Old History is deliberately not bulk-restored. See `RELEASE_4_23_11.md` for precise semantics and validation limits.

After deployment, open Admin → Radar → Analytics. Check the new checkpoint funnel and queue lag. Baseline history starts with this version, so early counts may be incomplete until real new cycles have been observed. The AutoScan screen shows only a cached queue summary and must remain responsive.
