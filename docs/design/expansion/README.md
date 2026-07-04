# Access Drift — post-PoC Expansion Design

> **Nature**: design reference (not a binding contract, no code/DDL).
> **Status**: design for the **expansion scope** after PoC completion (2026-06-25). The actual DDL/pipeline/verification code
> is submitted together in each implementation task's PR.
> **Author**: DS team

---

## Position

Preserve the post-PoC expansion ideas, but **limit what's merged to main to a design-reference document that doesn't force implementation**.
To keep the reviewable unit small, this PR contains no DDL/pipeline/verification code.
The MVP-mandatory contracts (formal Intermediate/Gold contracts, etc.) go up in follow-up task PRs together with the code/DDL/verification.

---

## Current implementation status (to avoid misunderstanding)

- The PoC is **complete** and its outputs exist: `intermediate.nhi_residual_access_findings` (2 rows), `gold.gold_core` (2 rows)
  (verified on the shared Databricks on 2026-06-25 — [poc-scope.md](../../product/poc-scope.md)).
- The layer DDL is in [`sql/databricks/migrations/`](../../../sql/databricks/migrations/), covering 001 Bronze through 005 Gold, but
  **004/005 are only at the schema-creation level**.
- The **formal Intermediate/Gold contracts and DDL for the expansion scope** this document covers are **still undecided**, and are added in a follow-up DS implementation PR.
  In other words, the PoC outputs exist, but the formal contract for this expansion design is still a follow-up scope.

---

## Relationship to the MVP scope (per the 2026-06-26 MVP confirmation meeting)

- The MVP is **ML-free** — it keeps the NHI residual access **rule-based + Gold verification structure**.
- Therefore this document's **Isolation Forest / IF feature allowlist / rule vs IF comparison** are not confirmed MVP implementation but
  **post-MVP verification experiment candidates** (per the DS sprint Post-MVP Backlog).
- **MVP drift types = A-S1, A-S2, B-S2.** A-S3 and B-S1 are **future considerations** (PRD §8.1).
- The representative PoC pattern `nhi_residual_access` = this document's **B-S2**.

---

## Included in this PR (design only, no code/DDL)

| Document | Contents | MVP relevance |
|---|---|---|
| [01_label-taxonomy.md](01_label-taxonomy.md) | drift types A-S1~B-S2 (MVP/Future split), grace-period, sensitivity, scale, leakage prevention | MVP (A-S1/A-S2/B-S2) + Future |
| [02_distractor-catalog.md](02_distractor-catalog.md) | 12 traps that look risky but are normal — **for the rule-verification / synthetic-data spec** | synthetic-composition spec (rule verification) / IF comparison is post-MVP |
| [04_time-axis-policy.md](04_time-axis-policy.md) | exclude time-derived values from severity + show them as Risk Card context | §1~5 MVP / §6 IF allowlist is post-MVP |

> Numbers `03` (holdout policy) and `05` (rule vs IF comparison) are **not in this PR.** They're added with code in a follow-up implementation task PR.

---

## Already on main (not redefined here)

| Already present (main) | Location |
|---|---|
| Bronze / Silver / Eval schema contracts | [`docs/contracts/`](../../contracts/) |
| Layer DDL (001 Bronze ~ 005 Gold) | [`sql/databricks/migrations/`](../../../sql/databricks/migrations/) |
| PoC E2E pipeline notebooks (00~07) | [`notebooks/`](../../../notebooks/) |
| PoC scope / completion results | [`docs/product/poc-scope.md`](../../product/poc-scope.md) |
| `eval.ground_truth_case` (PoC minimal ground truth) | [`docs/contracts/eval-schema.md`](../../contracts/eval-schema.md) |
| Silver common model (`silver_principals`/`silver_credentials`/`silver_assets`/`silver_edges`) | [`docs/contracts/silver-schema.md`](../../contracts/silver-schema.md) |

---

## Split into follow-up task PRs (outside this PR's scope)

This PR holds **design only**. For the items below, we submit the **document + code/DDL/verification in the same PR** when working that task.

| Follow-up deliverable | Contents |
|---|---|
| `docs/contracts/intermediate-schema.md` + 004 expansion DDL | formal finding contract: `finding_id`, `risk_case_key`, `drift_type`, `subject_principal_id`, `asset_id`, `severity`, `finding_state`, evidence refs, `detected_at`, `run_id` |
| `docs/contracts/gold-schema.md` + 005 expansion DDL | formal Risk Card contract: `risk_case_key`, Risk Card display fields, `recommended_action`, context, **lineage keys**, `status`, `updated_at` |
| **Gold lineage keys (UC-first)** | Gold preserves at least `finding_id`·`credential_id`·`owner_edge_id`·`access_edge_id`·`subject_principal_id`·`asset_id` → Risk Card evidence tracing (per the UC-usage discussion, P1) |
| `03_holdout-policy.md` + DDL + split code | connected-component holdout — `eval.scenario_membership`/`component_split` + the code that produces them |
| `05_detection-comparison.md` + code (**post-MVP**) | eval precision verification (Precision/Recall/FPR, `eval.eval_universe`) · Isolation Forest comparison · IF feature builder |
| Pattern 1 SaaS/GitHub Silver mapping | mapping A-S1/A-S2 onto the current Silver model (`silver_principals`/`silver_edges`) |
| Action State / Audit / Ticket storage contract | operational state/audit/ticket history, separate from finding state |
| RAG chunk/citation contract | evidence-document chunk / citation metadata for action assist (LLM) |

---

## eval isolation (current state)

- The current eval contract is the **PoC minimal ground truth** (`eval.ground_truth_case`) ([eval-schema.md](../../contracts/eval-schema.md)).
- **holdout is expanded on a `scenario_id`/`scenario_family` basis in a follow-up implementation PR** (`03_holdout-policy.md` is added in the follow-up implementation PR).
- Detection jobs/apps don't read eval (isolation). This principle is kept in the MVP too.

---

## Unresolved / open decisions

| Item | Current proposal | Basis |
|---|---|---|
| Synthetic scale (MVP cap) | **identity ≤ 1,000 / positive ≤ 40** | 6/26 MVP meeting — 01 §6 |
| distractor placement | balance by breaking the positive conditions one at a time | 6/26 MVP meeting — 02 |
| `grace_period_days` | 7 days (proposed) | 01 §4 |
| **asset sensitivity ↔ risk severity mapping table** | **confirmed in the asset-sensitivity-mapping task after the PO's sensitive-asset classification table is finalised** | 01 §5 |
| (post-MVP) `split_seed`·holdout ratio·IF allowlist | confirmed in the relevant task | 03·04 |

---

## Background (why we preserve this design)

Decision-log (0621) [confirmed] principle.

> **Isolation Forest = baseline comparison role (post-MVP).**
> If ground-truth labels leak into the synthetic data, the comparison becomes trivial, so
> we build a fair comparison structure with distractors + holdout separation.

The MVP uses rule-based only, but this principle — **design the synthetic data so it "can't be solved trivially from a single signal, and the answer doesn't leak"** — remains valid for the MVP's rule verification too. The IF comparison itself is left as a post-MVP experiment.
