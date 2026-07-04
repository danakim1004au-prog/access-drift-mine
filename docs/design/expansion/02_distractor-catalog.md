# 02 · Distractor (trap) case catalogue

> **Purpose**: catalogue normal cases where `IS_PLANTED_RISK = false` but which *look* risky.
> There are two uses — (1) a **rule-verification / synthetic-data composition spec** (so a single-signal rule can't solve it trivially), and (2) post-MVP **Isolation Forest comparison** (decision log 0621 [confirmed]).
> Since the MVP is ML-free, the IF comparison is left as a **post-MVP experiment**.
> main's `eval.ground_truth_case` has an `is_distractor` column, but the **distractor generation logic and catalogue are not yet implemented** — this document is that design.

---

## Design principles

> **A distractor = a case that shares 1-2 signals with a positive but is missing one decisive condition.**

Conditions for a good distractor:
1. **Shares surface signals**: it has signals that a first-pass filter would catch, like offboarded / NHI active / residual permission.
2. **Missing decisive condition**: *exactly one* of the positive gates ([01 §2](01_label-taxonomy.md)) is missing → the answer is false.
3. **Same time/meta distribution**: it has `created_at`/`expires_at`/`last_used_at` in the same range as positives (so time alone can't separate them).
4. **False-positive candidate**: a well-built rule lets it through, but a naive threshold rule (or post-MVP IF) is led to get it wrong.

Target effect: rule-based treats the distractor as normal (low false positives) → demonstrates the robustness of an "explainable rule". (When IF is compared in post-MVP, IF throws false positives on distractors, exposing the baseline limitation.)

---

## Catalogue

Each distractor is defined by the positive type it resembles, the **shared signals**, and the **missing decisive condition**.

### Pattern 1 look-alikes (people)

| id | Name | Resembles | Shared signals | Missing decisive condition (→ why it's normal) |
|---|---|---|---|---|
| `D-P1-01` | Offboarded but only non-sensitive assets | A-S1/A-S2 | person offboarded + SaaS account active | All reached assets are `sensitivity=LOW` (internal wiki). **No sensitive asset reached** |
| `D-P1-02` | Normal active employee with broad permissions | A-S2 | many active permissions reaching sensitive assets | `employment_status=active` + SCIM healthy. **Not residual (legitimate current permissions)** |
| `D-P1-03` | Guest but expired/revoked | A-S2 (guest variant) | `invited_via=personal_email` external collaborator | account `status=suspended` + edges inactive. **No active path** |
| `D-P1-04` | Permissions properly revoked after role change | A-S2 | `role_changed` state | Old role edges all revoked, only new role permissions active. **No residual permissions** |
| `D-P1-05` | Offboarded + object handover complete | A-S3 | a previously owned automation rule exists | The automation rule's `owner` was transferred to a successor. **Not residual ownership** |
| `D-P1-06` | SCIM stale but actually blocked | A-S1 | `scim_sync_state=stale` | SSO enforced + account actually disabled. **No login path** |

### Pattern 2 look-alikes (NHI)

| id | Name | Resembles | Shared signals | Missing decisive condition (→ why it's normal) |
|---|---|---|---|---|
| `D-P2-01` | Active prod SP, clear owner | B-S1 | `lifecycle=active` + accesses a sensitive (prod) asset | `owner_type='identity'` + current employee + actually used |
| `D-P2-02` | Owner offboarded but decommissioned | B-S2 | `created_by` offboarded | `lifecycle_status='revoked'` (already decommissioned). **No active path** |
| `D-P2-03` | Unclear owner but only non-sensitive assets | B-S1 | `owner_type='unassigned'` + active | reached asset `sensitivity=LOW` (dev/test). **No sensitive asset reached** |
| `D-P2-04` | Token near expiry, clear owner, actually used | B-S1 | `expires_at` imminent (time signal) | `owner_type='identity'` + current employee + actually used. **Not risky from a time signal alone** (time-axis trap) |
| `D-P2-05` | Newly issued SP, owner registration pending | B-S1 | `owner_type='unassigned'` + `active` + reaches a sensitive asset | **grace-period exception**: `pending` + `created_at` within grace + `created_by` active → [01 §4](01_label-taxonomy.md) |
| `D-P2-06` | NHI created by a leaver, ownership transferred to a team | B-S2 | `created_by` offboarded + active | `owner_type='team'` + valid `owner_id` (team account). **Ownership transfer complete → not orphaned** |

> **D-P2-05 = the hardest distractor.** It **shares all** of B-S1's surface signals (unassigned + active + reaches a sensitive asset), and the only distinguishing basis is the **grace-period policy exception**. The data generator and the rule detector **must** apply the *same* grace rule ([01 §4](01_label-taxonomy.md)) (if only one side applies it, the answer key and the detection criteria diverge). A spot where a "policy-unaware" IF easily throws false positives.

---

## Distractor ↔ positive signal matrix

A table to see at a glance why a naive rule trips over distractors. `✓` = has the signal, `—` = doesn't.

| Signal | A-S1/2 positive | D-P1-01 | D-P1-02 | D-P2-01 | D-P2-04 |
|---|---|---|---|---|---|
| identity offboarded/role_changed | ✓ | ✓ | — | (NHI) | (NHI) |
| permission/entity active | ✓ | ✓ | ✓ | ✓ | ✓ |
| **reaches sensitive asset** | ✓ | **—** | ✓ | ✓ | ✓ |
| SCIM unmanaged/stale | ✓ | ✓ | — | — | — |
| clear owner + actually used | — | — | ✓ | **✓** | **✓** |
| time signal (old/near-expiry) | same distribution | same distribution | same distribution | same distribution | ✓ (trap) |

> The bold cells are the **decisive conditions that divide the answer**. The time signal is designed so neither side is decided by it (same distribution) — [04](04_time-axis-policy.md).

---

## Scale & placement rules

- **With positive ≤ 40 as the basis, distractors are placed in balance at roughly 1:2 against positives** ([01 §6](01_label-taxonomy.md) MVP cap).
- Each positive drift_type includes **at least one corresponding distractor type** (the catalogue above covers every type).
- Distractors are also grouped by `scenario_id`, and **some are placed in the same scenario_family as their corresponding positive** (risk/normal should be mixed within the same company story for realism, and they move together during holdout — the holdout policy is split into a follow-up task doc).
- Time/meta fields are drawn from the **same random distribution** as the positive group ([01 §7](01_label-taxonomy.md) invariant).

---

## Verification criteria (DS checks after data generation)

- [ ] Every positive drift_type has ≥ 1 corresponding distractor
- [ ] The time-field distributions of positives and distractors are statistically indistinguishable (KS-test p > 0.05 recommended)
- [ ] A single-signal rule like "offboarded = always risky" has precision below 1.0 (the distractors actually act as traps)
- [ ] In `eval.ground_truth_case`, every row with `is_distractor=true` has `is_planted_risk=false`
