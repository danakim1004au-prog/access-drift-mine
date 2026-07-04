# ADR-0002: RAG chain model choice and deterministic fallback

- **Status**: Proposed
- **Date**: 2026-07-02
- **Deciders**: DS/DE team (Dana Kim, Haewon Jeong, Jaeryun Joo)

## Context

DE has finished the evidence corpus `rag.doc_chunks` (migration 008, notebook 10) and its contract ([rag-evidence.md](../contracts/rag-evidence.md)). On top of that, DS needs to attach the retrieve → generate → evaluate chain.

The plan (Databricks-based RAG) left the choice open: embeddings `databricks-gte-large-en`, retrieval via Vector Search (Delta Sync), and the generation LLM either the built-in FM API (Llama/DBRX) or an External Model (Claude/OpenAI). The team discussion settled on the following.

- Remove the Azure OpenAI (Foundry) resource.
- For cost reasons, use OpenAI `gpt-4o-mini` via a Databricks External Model as the generation LLM.
- External Model endpoint registration (key/proxy) is out of scope for this implementation; the chain only takes the endpoint name as a parameter.
- Gold Core always produces an action, regardless of LLM/RAG failure (ADR-0001 principle).

## Decision

1. **Embeddings**: FM API `databricks-gte-large-en`. Used as the `embedding_model_endpoint_name` of the Vector Search Delta Sync index (`rag.doc_chunks_index`).
2. **Retrieval**: Databricks Vector Search STANDARD endpoint + Delta Sync (TRIGGERED) index. `chunk_id` is the primary key, `chunk_text` is the embedding source.
3. **Generation LLM**: Databricks External Model endpoint (default name `access_drift_llm`, target OpenAI `gpt-4o-mini`). The endpoint name is injected via a notebook / `databricks.yml` widget variable. **Registering the endpoint itself is outside this PR's scope.**
4. **deterministic fallback**: all of the below fall back to `gold_core.recommended_action`.
   - No retrieval results / LLM endpoint not registered or unavailable / LLM call failed / no citation in the response
5. **Citations enforced**: a rag answer must contain a cited `[chunk_id]`; without one it's handled as a fallback. The smoke test in `13_rag_chain` verifies this.
6. **Evaluation**: retrieval recall / citation rate are measured without an LLM. groundedness/correctness are filled via Agent Evaluation (`mlflow.evaluate`, `databricks-agent`) only when an endpoint is available, otherwise `null`.


## How to register the External Model endpoint (follow-up work)

This PR only uses the endpoint name; it doesn't do the registration. In follow-up work, store the OpenAI API key in a Databricks workspace secret, then create the `access_drift_llm` Serving endpoint in the form below.

```python
import mlflow.deployments

client = mlflow.deployments.get_deploy_client("databricks")
client.create_endpoint(
    name="access_drift_llm",
    config={
        "served_entities": [{
            "name": "openai-gpt-4o-mini",
            "external_model": {
                "name": "gpt-4o-mini",
                "provider": "openai",
                "task": "llm/v1/chat",
                "openai_config": {
                    "openai_api_key": "{{secrets/access-drift-openai/api-key}}"
                },
            },
        }]
    },
)
```

Never leave the raw API key in code, notebooks or docs. If you change the endpoint name, change only the `rag_llm_endpoint` bundle variable.

## Consequences

- **Pros**: even without an LLM endpoint, the whole chain (index → retrieve → fallback generation → evaluate) runs end to end, so demos/verification are possible. Once the endpoint is registered, rag generation switches on with no code change. Costs are bounded by `gpt-4o-mini` + Vector Search + gte embeddings.
- **Cons**: while the endpoint is unregistered, every answer is a deterministic fallback, so groundedness/correctness are empty and the actual generation quality can't be verified. This state is surfaced by `rag.recommended_action.answer_source` and eval's `llm_available`.
- **Follow-up**: registering the External Model endpoint (`access_drift_llm`), AI Gateway guardrails (PII/toxicity, rate limit), Model Serving deployment, and wiring the dashboard "Recommended actions" tab are separate scopes. The SaaS/HI (A-S1/A-S2) expansion is attached once `hi` chunks exist in the corpus and a separate drift_type/eval basis is defined.


## Implementation update (2026-07-02)

The repository now includes operational notebooks for endpoint registration, AI Gateway policy logging, UC source document manifest seeding, synthetic remediation history, governance assessment, cost estimate, and dashboard read-view integration.

Endpoint and AI Gateway notebooks are dry-run by default because they require workspace admin/serving permissions and a Databricks secret that stores the OpenAI API key. This keeps CI/dev execution deterministic while making the production activation path executable.

## Decision update (2026-07-02): switch the default generation LLM to the Databricks FM API

The External Model path (`access_drift_llm` → OpenAI `gpt-4o-mini`) was verified all the way through endpoint creation and reaching OpenAI, but the linked OpenAI account was on the Free tier (credit $0.00), so generation was blocked with `429 insufficient_quota`. As a matter of principle for a school team project, we don't use personal billing.

Accordingly, we **switch the default generation LLM to the Databricks Foundation Model API pay-per-token endpoint `databricks-meta-llama-3-3-70b-instruct`**.

- The FM API is a built-in workspace endpoint, so it works without an external API key or personal billing, and costs are consolidated onto workspace (team/school resource) billing. This corresponds to the "built-in FM API model (Llama)" option the plan left open.
- We changed the `13_rag_chain`/`14_rag_eval`/`19_rag_llm_serving_smoke` widgets and the `rag_llm_endpoint` bundle variable default to the FM endpoint. The enforced-citation and deterministic-fallback rules apply unchanged.
- The External Model path is not removed. The `rag_external_llm_endpoint` bundle variable (default `access_drift_llm`) and the rag-ops job (notebooks 15/16) continue to manage it, and once a shared team/school OpenAI/Azure OpenAI key is available, switching just the `rag_llm_endpoint` value moves over with no code change.
- The serving invocation URL had a problem where the regional/control-plane host (`ctx.apiUrl`) returned 404, so it was fixed to use the workspace URL (`spark.databricks.workspaceUrl`).
- The AI Agent panel in the dashboard "Recommended actions" tab is implemented as a conversational RAG chatbot (`apps/dashboard/src/rag_agent.py`) that directly calls the same Vector Search index + FM endpoint. Retrieval failures, LLM failures and missing citations are all handled via the deterministic fallback.
