# RAG Chain Contract (DS)

```text
version: 0.2.0
status: draft
updated_at: 2026-07-02
```

## Scope

This document defines the criteria for the RAG **chain** (retrieve → generate → evaluate) that DS owns.

The evidence corpus (`rag.doc_chunks`) and its schema are managed in the [RAG Doc Chunks Contract](./rag-evidence.md) (DE). This document defines the search index, query generation, grounded responses, enforced citations, deterministic fallback and evaluation metrics that operate on top of the corpus.

RAG does not make any new detection decisions. It's an action-assist layer that takes the Risk Cases already produced in Gold Core (`gold_core`) as input and produces grounded recommended actions and citations.

## Components (Databricks-native)

| Stage | Implementation | notebook |
| --- | --- | --- |
| Search index | Databricks Vector Search, Delta Sync index (auto-syncs from `rag.doc_chunks`) | `12_rag_vector_index.ipynb` |
| Embeddings | FM API `databricks-gte-large-en` | (specified when the index is created) |
| Query generation | `gold_core` finding → query text + metadata filters | `13_rag_chain.ipynb` |
| Generation LLM | Databricks FM API pay-per-token endpoint (default `databricks-meta-llama-3-3-70b-instruct`, no external API key or personal billing needed). The External Model (`access_drift_llm` → OpenAI `gpt-4o-mini`) can be swapped in via the `rag_llm_endpoint` variable | `13_rag_chain.ipynb` |
| Trace | MLflow run, records the query/evidence/response per finding | `13_rag_chain.ipynb` |
| Evaluation | retrieval recall / citation rate + Agent Evaluation (groundedness/correctness) | `14_rag_eval.ipynb` |

## Output tables

The DS chain results live in the `rag` schema, kept separate from the corpus (`doc_chunks`).

- `rag.recommended_action` — grounded recommended action per finding (migration `009`)
- `rag.eval_results` — evaluation metrics per eval-set case (migration `009`)

Both tables are loaded with overwrite (same principle as the existing Gold/Silver). The columns follow `sql/databricks/migrations/009_create_rag_chain_tables.sql`.

## Search keys

`rag.doc_chunks` is not joined directly to the Risk Card. The query and filters are built from the current `gold_core` values.

| Gold Core value | Used in search |
| --- | --- |
| `drift_type` | Vector Search `filters.drift_type` |
| `subject_type` | `filters.principal_kind` (mapped to `nhi`/`hi`; the current seed is `nhi`) |
| `asset_display_name`, `asset_sensitivity`, `asset_environment`, `owner_display_name` | Compose the query text |

Default filter for the representative NHI residual access case:

```text
drift_type = nhi_residual_access
principal_kind = nhi
top_k = 4
```

## Generation rules (enforced citations / hallucination prevention)

1. If there are search results, the LLM proposes an action **using only the content in the provided evidence chunks** and appends a `[chunk_id]` citation to the end of each sentence.
2. If the response cites no evidence at all, that response is not trusted and it falls back.
3. All of the following are handled as a **deterministic fallback** (`answer_source = 'deterministic_fallback'`).
   - No search results
   - LLM endpoint not registered / unavailable
   - LLM call failed
   - No citation in the response
4. The fallback answer uses `gold_core.recommended_action` as-is. An action is always produced, regardless of LLM/RAG failure.
5. The raw values of secrets, tokens or keys are never included in the evidence or the answer (same as the corpus contract).

> Even without an LLM endpoint yet, the chain runs end to end. In that case every answer becomes a `deterministic_fallback`, and the search index / citation checks are still performed.

## Evaluation criteria

`14_rag_eval.ipynb` scores `data/dev/rag/rag_eval_set.jsonl`.

| Category | Metric | Method | Target |
| --- | --- | --- | --- |
| Retrieval | retrieval recall@k | at least one of `expected_doc_ids` is in the top-k | higher is better |
| Quality | citation rate | proportion of responses with a cited chunk | mandatory for rag answers |
| Quality | groundedness | Agent Evaluation LLM judge | ≥ 0.9 |
| Quality | correctness | Agent Evaluation LLM judge (against `expected_response`) | per the eval set |

retrieval recall / citation rate are measured even without an LLM. groundedness / correctness are filled only when an LLM endpoint is available, otherwise stored as `null`. The eval set's `expected_facts` are converted to `expected_response` when fed to Agent Evaluation.

Eval is an isolated layer the detection pipeline doesn't read. `14_rag_eval` is run manually/separately rather than added to the main pipeline job.

## Prohibited

- Do not trust a rag answer without a citation.
- Do not instruct automatic revoke/delete/suspend. Describe it as a procedure carried out after the owner has confirmed.
- Do not store or pass the raw value of a secret/token/key to the LLM.
- Do not replace Gold Core `recommended_action`; reinforce it with evidence. On failure, always fall back.
- Do not change the corpus schema/chunking criteria in this document (that's the remit of [rag-evidence.md](./rag-evidence.md)).


## Operations/governance additions

The extra operational notebooks run as the `access-drift-rag-ops` job, separate from the main bronze/silver pipeline.

| Scope | Implementation | notebook / table |
| --- | --- | --- |
| External Model registration | `access_drift_llm` -> OpenAI `gpt-4o-mini`, using a Databricks Secret reference | `15_register_external_model_endpoint.ipynb` |
| AI Gateway policy | records the rate limit, payload logging, and PII/unsafe-content/prompt-injection guardrail contract to MLflow | `16_configure_ai_gateway_guardrails.ipynb` |
| UC source layer | `rag.source_docs` Volume + sanitized source manifest | migration `010`, `rag.source_docs_manifest` |
| Past remediation history | synthetic remediation history corpus | `rag.remediation_history` |
| Ethics/quality audit | citation, fallback, secret leak, destructive action, eval coverage checks | `rag.governance_assessment` |
| Cost plan | OpenAI token estimate + Vector Search/Serving endpoint metadata | `rag.cost_estimate` |
| Dashboard integration | shows the RAG/fallback answer and reference docs in the Risk Card recommended-actions tab | `app.risk_card_detail_view`, Streamlit loader |
| Dashboard AI Agent | conversational chatbot in the Risk Card recommended-actions tab. Directly calls the same Vector Search index + FM endpoint and follows the enforced-citation / deterministic-fallback rules | `apps/dashboard/src/rag_agent.py`, `components/risk_text_panels.py` |

`rag_ops_dry_run=true` is the default. Actually creating the External Model endpoint and applying AI Gateway is run separately with `rag_ops_dry_run=false`, after confirming the secret scope, preview enablement and workspace permissions.
