# 01 · Label taxonomy — IS_PLANTED_RISK ground-truth criteria (expansion)

> **Expansion target**: main's [`eval-schema.md`](../../contracts/eval-schema.md) defines only a single `drift_type = nhi_residual_access`.
> This document fixes the ground-truth adjudication criteria for **expanding that to 5 types (A-S1~B-S2)**.
> The **base schema** (`eval.ground_truth_case` columns · eval key · label interpretation) follows eval-schema.md as-is and is not redefined here.

---

## 1. Label granularity

Labels are attached per **"risk case"**, not per row.

> **risk case = `(subject, final_asset, drift_type)`**
> e.g. `(account=kim@slack, asset=customer_DB, A-S2)` = one case.

- One subject with residual access to several assets → **a separate case per asset**.
- The evaluation join key is the same as eval-schema.md: **`(subject_type, subject_id, asset_id, drift_type)`**.
- `case_id` is a human-readable handle; machine joins use the eval key.

---

## 2. drift_type taxonomy (PoC `nhi_residual_access` → 5 types)

The PoC's `nhi_residual_access` corresponds to **B-S2** below. On expansion, people (Pattern 1) and NHIs (Pattern 2) are subdivided as follows.
**The MVP scope is A-S1·A-S2·B-S2**, and A-S3·B-S1 are **future considerations** (PRD §8.1, 6/26 MVP meeting).

| drift_type | Pattern | MVP? | One-line definition |
|---|---|---|---|
| `A-S1` | P1 person | **MVP** | Offboarded/role-changed, yet the SaaS **account** is still active |
| `A-S2` | P1 person | **MVP** | Offboarded/role-changed, yet in-app **permissions/memberships** remain |
| `A-S3` | P1 person | Future | Offboarded, yet an owned **object (automation rule, etc.)** is still active |
| `B-S1` | P2 NHI | Future | NHI with an **unclear owner** (unassigned/offboarded owner) + active |
| `B-S2` | P2 NHI | **MVP** | NHI not decommissioned after its **creator offboarded** (= current PoC `nhi_residual_access`) |

> The data generator places nodes/edges according to the **structural conditions** below. Every positive satisfies the §3 common gate.
> The conditions below are **stated against the expansion data model**. The mapping onto the current main Silver (`silver_principals`/`silver_credentials`/`silver_assets`/`silver_edges`) — especially Pattern 1 — is sorted out in a follow-up task.

### Pattern 1 — person accounts

**A-S1 · account residual**
1. `identity.employment_status ∈ {offboarded, role_changed}` or `idp_status=disabled`
2. The linked `account.account_status=active`
3. An active edge of that account reaches a sensitive asset
4. Cause: `scim_managed=false` or `scim_sync_state ∈ {stale, failed}`

**A-S2 · in-app permission/membership residual**
1. Person offboarded/role_changed
2. A separate active edge exists with `edge.relation ∈ {project_role, group_membership, space_permission}`
3. That edge reaches a sensitive asset
4. (Guest variant) `account.invited_via=personal_email`

**A-S3 · ownership/object residual**
1. Person offboarded (the account may be inactive)
2. `edge: (identity|account) --owns--> object` exists + `object.is_active=true`
3. `object.owner_type='identity'` and `owner_id` = that former employee (not handed over to a successor)
4. `edge: object --grants_access--> asset(sensitive)` active
> Key point: even if the account is inactive, it's a positive when the **object is active + ownership not transferred**. It's normal once handed to a successor ([02](02_distractor-catalog.md) D-P1-05).

### Pattern 2 — NHI

**B-S1 · NHI with unclear owner**
1. `nhi.owner_type='unassigned'` **or** `owner_type='identity'` but the owner is offboarded/role_changed
2. `nhi.lifecycle_status='active'`
3. An active NHI edge reaches a sensitive asset
4. **Not a grace-period exception** (§4)

**B-S2 · NHI linked to an offboarded/role-changed person, not decommissioned** (current PoC pattern)
1. `nhi.created_by_identity` or `last_accessed_by_id` is offboarded
2. That person's account is fully offboarded (the person path is severed)
3. `nhi.lifecycle_status='active'` + reaches a sensitive asset via an active credential (a path independent of the person)

---

## 3. Common gate & active/inactive

- **Every planted positive satisfies "an active path reaches a sensitive (HIGH/MEDIUM) asset"** → a positive is active by definition.
- Residual inactive paths are **not planted as positives.** If the detector surfaces one, mark it `intermediate.finding.finding_state='historical'`, and since it's **outside the evaluation universe (active paths only)**, it's **excluded** from TP/FP/FN (not scored as `normal`). The evaluation population / scoring design is split out into the rule-vs-IF comparison doc (follow-up task).
- Therefore, **no active/inactive demotion term is placed in the severity formula.** Active-ness is used only as a candidate-eligibility filter.

---

## 4. grace-period exception rule (B-S1 discrimination)

If "owner unregistered" is really **awaiting registration right after new issuance**, treat it as normal (legitimate operation).

> B-S1 is **excluded from positives** (normal/distractor) if any of the following is true:
> - `ownership_registration_status='pending'` **AND** `created_at` is within the grace_period **AND** `created_by` is active, or
> - `owner_type='team'` and `owner_id` is valid (ownership transfer complete → [02](02_distractor-catalog.md) D-P2-06)

- `grace_period_days` default proposal is **7 days** (needs DE/DS agreement — [README §unresolved](README.md)).
- This rule applies **equally to both data generation and rule detection** (the answer key and the rule must look at the same gate).
- **Boundary case**: if it's `pending` but `created_at` **exceeds** the grace, then registration never happened, so it's a **B-S1 positive**. Even for the same 'pending', the answer diverges with elapsed time. A rule that's aware of the time policy gets this right. (When IF is compared in post-MVP, this becomes a demonstration point for the baseline limitation of a time-blind IF confusing a within-grace trap with an over-grace positive.)

---

## 5. Asset sensitivity classification & severity mapping

**Primary basis = final asset sensitivity.** No time-derived values used → [04](04_time-axis-policy.md).

### Asset sensitivity (per the demo scenario — confirmed: Critical / High / Medium)

| Grade | Criteria | Examples (commerce) |
|---|---|---|
| **Critical** | Information legally obligated to be protected | customer PII · address/delivery · order/payment/refund history, card/account, seller settlement/contract |
| **High** | Core asset with major operational impact if leaked | DB encryption keys, core source code, production root access, production SSL/TLS private keys/secrets, revenue data, permission lists/security audit logs/ops runbooks |
| **Medium** | Internal asset that needs company-wide sharing | de-identified statistics, real-time inventory, dev logs/staging, internal manuals, seller status |

> A High asset that leads to access to customer PII / payment info is **promoted to Critical**.

### expected_severity mapping (confirmed in a follow-up task)

`expected_severity` is derived from the asset sensitivity above. **The exact asset sensitivity ↔ risk severity mapping table is confirmed in the asset-sensitivity-mapping task after the PO's sensitive-asset classification table is finalised** (follow-up PR).

> ⚠️ Consistency note: the current main `silver_assets.sensitivity` enum is `high`/`medium`/`low`/`unknown` (source level).
> The mapping between the demo sensitivity (Critical/High/Medium), the Silver enum, and the risk severity grades must be fixed in the mapping table.

Sort helpers (not a grade change): NHI first, then actionability.

---

## 6. Synthetic scale (MVP cap — 6/26 MVP meeting)

> The synthetic data request is delivered to DE not as a raw total but as a **positive / distractor / holdout composition spec** (not an ML training sample).

**MVP cap (confirmed draft):**

| Item | MVP cap |
|---|---|
| identity | **≤ 1,000** (mostly active / a few offboarded·role_changed) |
| **positive cases** | **≤ 40** (centred on MVP drift types A-S1·A-S2·B-S2) |
| distractor | balanced placement by breaking the positive conditions one at a time ([02](02_distractor-catalog.md)) |

- dev/holdout are **split at the scenario level** so the same case doesn't get mixed into both ([03] follow-up task).
- The detailed account/nhi/asset/edge scale and **large-scale synthetic for expansion experiments** are a separate agreement (post-MVP).
- Within the NetworkX single-node limit (identity < 10,000).

---

## 7. Leakage-prevention invariants (data generator invariants)

1. Answer/scenario/split columns are **never included in the Silver body**. They live only in `eval.*`.
   - The generator doesn't write directly to Silver. source-form raw → Landing/Bronze original preserved → Silver normalised. Answer labels aren't put in raw, but separated into `eval.*` as generation metadata.
2. Positives and distractors must have the **same global (marginal) time distribution** — if a single global threshold ("old = risky") divides the answer, the comparison is trivial.
   - However, **policy-conditional time gates are allowed and intended** (e.g. the answer diverging at the grace boundary within the `pending` subset). That's *policy*, not global time leakage.
3. Every positive is generated only from the §2 structural conditions — the rule must be able to score it.
4. No single "magic column" that identifies a positive. It's always established only from the combination of **identity/owner state × edge active × asset sensitivity**.
5. (post-MVP) When IF is introduced, use **structural/state features only** → [04 §6 IF allowlist](04_time-axis-policy.md). The MVP is ML-free (rule-based).
