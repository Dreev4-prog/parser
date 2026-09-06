# v4.23.8 GitHub patch — First-Pass Recovery & Radar Self-Heal

Apply **on top of v4.23.7** and preserve all paths from this archive.

After push/redeploy:

- redeploy **Parser / Bot** — required;
- Page Worker / Date Worker / View Worker code is functionally unchanged by this patch;
- Vinted Scan / Metrics / Session workers are unchanged.

No manual SQL migration and no new required Railway variables.

Main fixes:

1. a normal user scan now performs one fresh-context checkpoint-aware recovery inside the first launch before showing a partial result;
2. Radar page/view partials get one bounded inline self-heal pass before being stored as `допроверка`;
3. transient HTTP/timeout pressure is shown as `🌐 transport`, not hidden under `другое`;
4. Traffic Manager cooldown with zero users is displayed as Kleinanzeigen protection mode, not fake user priority;
5. idle Page Worker prefetch is reduced from a whole 20-page burst to a safer rolling 16-page window.

Accuracy gates are unchanged. See `RELEASE_4_23_8.md`.
