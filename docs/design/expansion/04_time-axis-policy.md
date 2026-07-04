# 04 · Time-axis handling policy

> **Purpose**: fix **how far** time-derived information (residual duration, expiry date, review date, etc.) is used and **where** it's excluded.
> **Basis**: PRD §8.3 (list of excluded independent-score elements), decision log [confirmed] "add Risk Card time data".
>
> **Scope split**: §1~5 (exclude time from the severity score + show it as Risk Card context) = **valid for MVP** (rule-based).
> §6 IF feature allowlist = **post-MVP** (the MVP is ML-free).

---

## 1. One-line policy

> **Time-derived values are not used as an independent element of the risk (severity) score.**
> Instead, they're shown only as the risk card's **context fields** and via **storytelling** (left for months).

---

## 2. Distinguishing the two kinds of time information

| Kind | Examples | Use |
|---|---|---|
| **Fact timestamps** | `created_at`, `offboarded_at`, `last_used_at`, `expires_at`, `granted_at` | **Stored verbatim** in Silver. Used for the graph/context/display |
| **Time-derived score features** | residual duration (days), days until expiry, time since last review, whether renewal is overdue | **Excluded from score input.** Surfaced on the Gold card only as "context" |

> In short: **storing timestamps is OK, but derived features that score them are prohibited.**

---

## 3. PRD §8.3 exclusion list (not to be used as independent score elements)

The following are not used as independent score elements in the MVP (they may still be shown as context):

- credential time-since-issuance · number of owners · last review date · expiry / time-to-expiry · whether renewal is overdue · whether there's a backup owner

→ These go only into the **context block of `risk_card`**, not the severity formula.

---

## 4. So what is the score based on?

Per [01 §5](01_label-taxonomy.md), severity's primary basis is **final asset sensitivity**. Time is left out.
Path active-ness is used only as **candidate eligibility, not a score adjustment** (only active paths are evaluated / severity-eligible; inactive = historical = out of scope).

```
severity     = f(final_asset_sensitivity)   # primary basis (time-independent)
eligibility  = active path only             # inactive/historical is outside the universe/severity
sort helper  : NHI first, actionability     # not a grade change
excluded     : all time-derived values      # this policy
```

> An inactive path isn't demoted — it's **outside the evaluation/severity scope** ([01 §3](01_label-taxonomy.md)).

---

## 5. Showing time on the Risk Card

For storytelling ("left for months"), time **context** is shown on the card. But it's **explanatory text/fields, not a score**.

| Card field | Source | Nature |
|---|---|---|
| `days_since_offboarding` | `now − offboarded_at` | Context. "Accessible N days after leaving" (residual duration) |
| `days_since_first_detection` | `now − intermediate.finding.first_seen_at` | Context. "N days since the system first observed it" (detection observation) |
| `last_used_at` | fact timestamp | Context. Shows recent activity |
| `expires_at` / near-expiry badge | fact timestamp | Context badge (no score impact) |
| `created_at` | fact timestamp | Shows the NHI issuance time |

> The two residual fields (`days_since_offboarding` vs `days_since_first_detection`) mean different things, so don't mix them into one field.
> In the card UI, group them under a "Context" section to make clear they're separate from severity.

---

## 6. IF feature allowlist (block raw timestamps from leaking in) — ⚪ post-MVP

> **The MVP does not introduce ML (Isolation Forest).** This section is the feature contract to apply if IF is introduced as a **post-MVP experiment**.
> That said, the principle "don't put time-derived values into the severity score" (§4) **remains valid for the MVP rule-based approach too**.

Silver stores fact timestamps (for context/graph), but when IF is introduced, **only an explicit allowlist goes into the features**.
Not just derived values — **all raw timestamps, IDs, and scenario/eval metadata are excluded**.

### ✅ Allowed (structural/state features only)
- Person: `employment_status`, `idp_status`, `scim_managed`, `scim_sync_state`
- Account: `account_status`, `sso_enforced`, `scim_linked`, `invited_via`
- NHI: `nhi_type`, `owner_type`, `ownership_registration_status`, `lifecycle_status`
- Object: `object_type`, `is_active`, `owner_type`
- Asset: `asset_type`, `sensitivity`, `environment`
- Edge: `relation`, `permission`, `is_active`
- Graph-structure derivations (time-independent): degree, path length to a sensitive asset, number of reachable sensitive assets, active-path ratio

### ⛔ Excluded (not allowed as training/score input)
- **All timestamps**: `created_at`, `expires_at`, `last_used_at`, `offboarded_at`, `granted_at`, `last_active_at`, `_ingested_at` + Intermediate observation times `detected_at`/`first_seen_at`/`last_seen_at`
- **All IDs**: `*_id` (leakage/overfit risk)
- **All time-derived numerics**: residual days, time-to-expiry, time since last review, etc. (§3 list)
- **Scenario/eval metadata**: `scenario_id`, `split`, `case_id`, `truth_class`, `is_planted_risk`, `is_distractor`

### Implementation contract
- The feature builder selects columns via an **allowlist (whitelist) approach only** (no blacklist — to stop a new column leaking in by mistake).
- **IF**: only the **structural/state features** in the allowlist above. No timestamps at all.
- **rule-based**: allowlist features + **explicit policy gates are allowed to use timestamps**. e.g. the `created_at` comparison for the grace-period decision ([01 §4](01_label-taxonomy.md)) is a *policy rule*, not a training feature. This is the rule's design-level strength (policy awareness), and the spot where a time-*blind* IF confuses D-P2-05/B-S1.
- **The basis for a fair comparison is not "identical input" but "identical holdout · identical universe · identical eval key · identical answer key".** Neither approach puts time-derived values into the severity score itself (§4).
- The allowlist is fixed as a code constant; update this document when it changes.

---

## 7. Checks (verify at implementation)

**MVP (rule-based)**
- [ ] No time-derived variable enters the severity calculation code as input (code review)
- [ ] No derived columns like "residual days / time-to-expiry" in the Silver/Graph feature set (fact timestamps only)
- [ ] The time fields of Gold `risk_card` sit only in the context block

**post-MVP (when IF is introduced)**
- [ ] The IF feature builder uses only the §6 allowlist whitelist (0 timestamps/IDs/metadata)
- [ ] Passes the positive/distractor time-distribution equality check (KS-test)
