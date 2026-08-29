# DT Parser 4.20.0 — Whole-project audit

This document records the full local/static/contract audit of the consolidated
DT Radar Core 2.0 checkout before final packaging. It lives under `docs/` so the
repository root remains deployment-focused.

## Scope

The audit covered the complete runtime chain:

- Railway service launcher and role routing;
- PostgreSQL startup and additive migrations;
- Redis Page/Date/View worker namespaces and job/cache contracts;
- category/date/page parsing, chronology and payload round-trip;
- exact public view-counter extraction, endpoint/ad identity and browser recovery;
- live Organic Detail Gate and paid/reduced-price integrity;
- AutoScan Fresh/48H Context/retry/hard-stop/watchdog state transitions;
- Verified Organic Velocity (`first exact >=400` baseline policy);
- DT Demand Score age cohorts and evidence-adaptive scoring;
- AI and Lifecycle/Fast Sold consumers;
- release layout, syntax, tests and clean-room package validation.

## Corrections made during the audit

1. **Page Worker cache contract** — Page Manager and Page Worker now use the same
   schema-scoped cache key. A successful remote page can no longer be invisible to
   Parser and trigger an unnecessary local re-fetch.
2. **Promotion parsing false positives** — bump tokens are accepted only in real
   promotion/icon semantics. Product slugs/titles such as `push-up-board`,
   `hochschiebe-regal`, `boost-adapter`, and `TOP Zustand` remain organic.
3. **Promotion parsing false negatives** — detail checks now recognize explicit
   Highlight/Galerie feature classes and metadata in addition to TOP/Hochschieben.
   Integrity parsing exceptions fail closed instead of silently becoming organic.
4. **Cache invalidation** — Page/Date/stable-page payload schema is
   `v4200-core2-audit3`, preventing payloads parsed with older promotion semantics
   from being replayed into this audited checkout.
5. **Redis rolling-deploy isolation** — Page/Date/View runtime namespaces use
   `v4200-core2-audit3`. Incompatible legacy prefix overrides cannot satisfy a current job
   contract accidentally.
6. **Exact-view identity** — HTTP, request-context, passive network capture and
   rendered-browser recovery are bound to the exact requested `external_id` and
   official `adId` endpoint. Wrong-ad/category redirects fail closed.
7. **Exact-view text parsing** — after date/time removal, extra-info fallback accepts
   exactly one standalone integer. A second number (for example a postcode) makes
   the counter unknown instead of selecting an arbitrary integer.
8. **View Worker corrupt-message recovery** — a malformed/missing job payload writes
   a failed result and immediately hands control back to local fallback instead of
   leaving Parser waiting for the long remote timeout.
9. **Lifecycle/Fast Sold identity** — redirect identity is verified before
   unavailable text is interpreted. A redirect to a different/category page is
   UNKNOWN, not a false Fast Sold disappearance.
10. **Background/foreground browser isolation** — low-priority view and browser work
    obeys AutoScan's explicit background pause.
11. **Foreground Organic Gate priority** — normal Radar/User/AutoScan admission can
    no longer infer `background` from traffic configuration and wait on its own
    AutoScan pause. Maintenance callers must request background priority explicitly.
12. **Verified Velocity priority** — the 400+ maintenance scheduler explicitly keeps
    its second Radar detail verification in the background lane, so it cannot steal
    a foreground scan lane after a race at the scheduler boundary.
13. **AutoScan exact views priority** — the category's own exact counters always use
    foreground `scan_inline`; they are never throttled as maintenance traffic.
14. **Detail browser lane accounting** — rendered detail fallback leases the browser
    lane, not the lightweight HTTP view lane, so browser concurrency is enforced.
15. **AutoScan state/UX cleanup** — duplicate duration helper and duplicate unreachable
    live-stage return were removed; failed-category depth and active Context depth
    remain consistent; hard-stop/watchdog/cooldown paths stay interruptible.
16. **48H Context scheduling** — every completed manual or daily Fresh layer can queue
    one yesterday Context layer per Moscow day. Context enriches history and may publish
    a yesterday listing only through demand-safe unified 48H scoring + Organic Gate.
17. **48H age cohorts** — Relative View Velocity does not borrow a different age band
    when its own cohort is sparse. Missing evidence stays missing and evidence-adaptive
    scoring renormalizes the available factors.
18. **PostgreSQL migration concurrency** — `init_db()` uses a transaction-scoped
    PostgreSQL advisory lock before create/additive migration work, preventing
    simultaneous Parser/AI/Lifecycle starts from racing check-then-ALTER operations.
19. **Release structure** — historical deploy/checksum files are consolidated under
    `docs/`; the root contains only runtime/deployment files plus README/VERSION.
20. **Search-card attribute safety** — ordinary product/accessibility metadata such as
    `Highlight Lampe` or `Gallery Bilderrahmen` no longer looks like a paid feature;
    explicit `feature-*`/`paid-*` markers still do.
21. **Strict result-card identity** — a malformed `/s-anzeige/` slug can no longer
    donate an arbitrary long number as an `external_id`; only the structural
    `<adId>-<category>-<location>` identity is admitted.
22. **Remote exact-view contract revalidation** — Parser rechecks View Worker results
    at the Redis manager boundary. Official counter URLs must carry the exact `adId`;
    browser results must finish on the exact listing.
23. **Page payload trust boundary** — Redis and durable stable-page payloads are
    revalidated as `external_id <-> listing URL <-> kleinanzeigen.de`. Mismatched
    payloads are rejected and refetched locally. Foreign/non-location shard URLs are
    never replayed as network targets.
24. **Date probe trust boundary** — remote page hints have bounded numeric fields and
    the returned `page` must equal the page encoded by the current cache/job request.
    A stale/corrupt hint cannot silently move the chronology bracket.
25. **Exact-view completeness outside AutoScan** — ordinary scan enrichment and manual
    refresh now treat an omitted URL in a partial exact-result map as UNKNOWN, clear
    the stale counter and count a failure. Old views cannot survive a missing result.
26. **Category redirect identity** — result-page verification binds the requested and
    resolved category/location code. A page-1 redirect to another feed cannot be
    cached or interpreted as a valid empty date page.
27. **Galerie detail variants** — German `feature-galerie` / `paid-galerie` attributes
    and `isGalerieAd` metadata are recognized without turning a generic gallery word
    into promotion evidence.
28. **Audited rolling-deploy contract** — final Page/Date/View runtime namespaces and
    parsed-page schemas use `v4200-core2-audit3`. The earlier pre-audit 4.20.0 build
    cannot consume or satisfy audited jobs/caches during a rolling deployment.

29. **Unified 48H clock separation** — listing age now exclusively chooses the
    `0–3 / 3–6 / 6–12 / 12–24 / 24–48h` cohort and absolute Demand Gate, while
    verified post-baseline delta uses its own DT observation window only for
    views/hour. A 29h-old listing observed for 1h can no longer be rejuvenated into
    the 0–3h cohort; inherited baseline views still contribute zero.

## Invariants intentionally unchanged

- DT Demand Score weights are **40 / 20 / 15 / 15 / 10**.
- A first exact counter `>=400` is an untrusted baseline and contributes zero until
  two later clean checkpoints; only the DT-observed delta can score.
- Fresh Layer is today, 15 pages/category.
- Context Layer is yesterday, 15 pages/category, at most once per Moscow calendar
  day. Unknown inherited totals still cannot score; qualified demand-safe yesterday
  evidence may enter the same public 48H Radar.
- Sticky TOP/Hochschieben/Highlight/Galerie/sponsored/reduced/resurrection integrity
  remains fail-closed.

## Validation boundary

The included tests and release smoke verify local code/contracts without live external
services. A final production smoke still has to be performed after Railway deployment
against the real Kleinanzeigen site, Railway PostgreSQL, Redis, and the live Page/Date/
View/AI/Lifecycle worker fleet. The local audit cannot promise that an external site
will never change its HTML, rate limits, redirects, or counter endpoint in the future.

## Final Unified 48H ranking audit addendum

After the parser/infrastructure audit, the public Radar ranking layer received one final correctness pass:

- removed the legacy synthetic AutoScan TOP-position score path;
- today and yesterday now feed one 48H Radar only through demand-safe evidence;
- added absolute age-aware Demand Gate (`30/40/60/80/100`) so tiny relative cohorts cannot call 15 views Hot;
- added separate Radar Rank (`70% DT Score / 20% confidence / 10% maturity`) without changing public DT Score;
- removed post-model repeat/confirmed score bonuses so `current_score` is always a real 40/20/15/15/10 model score;
- live product aggregate fields now come from one coherent representative signal instead of mixing confidence/time/demand across different listings;
- stale frozen demand is aged conservatively against stricter gates and can downgrade Hot -> Strong -> Early;
- public AI Picks and Lifecycle admission require unified Hot/Strong status;
- startup repair removes old synthetic `radar_autoscan` / `scan_hot` snapshots, clears contaminated live scores/statuses and recomputes Peak only from surviving non-synthetic evidence;
- explicit age cohort boundaries are non-overlapping.
- baseline/delta observation time is decoupled from listing age, so verified delta keeps its true views/hour while Demand Gate/cohort use the actual ad age.
