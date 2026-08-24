# DT PARSER v4.9.0 — Free Trial Launch

Product/access release on top of v4.8.9 Queue UX. The proven parsing core and Date/Page/View worker protocols are unchanged.

## Launch offer
- New never-paid users get 2 free scan credits while the campaign is enabled.
- Each trial scan: 1 category, 15 or 25 pages (maximum 25).
- Trial includes the real scan result, real views, TOP-12/TOP-50 and XLSX.
- Subscription remains required for 50 pages, multi-category scans, repeat/recheck/manual view refresh and +3/+6/+12h auto measurements.
- Trial scans are saved in `My scans` like normal scans.
- A queued trial cancelled before network work returns its credit. If a queued job is retired as stale before it starts, its credit is also refunded. Distributed queues otherwise survive normal parser-service restarts and continue normally.

## Admin
`🎁 Бесплатные сканы` shows campaign state and funnel stats:
- used at least one trial
- used all free credits
- converted to a paid subscription
- conversion percentage

The campaign can be enabled/disabled from the admin panel without redeploying.

## Database
Additive migration only:
- `bot_users.trial_scans_used`
- `user_scans.is_trial`
- `user_scans.trial_credit_refunded`

No Redis cleanup is required.
