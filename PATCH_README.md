# v4.23.5 GitHub patch

Apply on top of **v4.23.4**.

This patch removes the remaining Vinted Lab UI pauses: stale-while-revalidate Redis/PostgreSQL UI caches with strict timeouts, cached category-tree navigation, background-only stale Radar refresh, O(1) Radar item lookup, and fast full-market result paging without repeated `COUNT(*)` / `view_count` sorting.

Replace the files from this archive preserving paths, then redeploy:

- Parser / Bot
- all Vinted Scan Worker replicas
- all Vinted Metrics Worker replicas

No SQL migration and no new Railway variables are required. Vinted Session Worker is unchanged.
