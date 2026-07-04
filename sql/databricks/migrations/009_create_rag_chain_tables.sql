-- 009_create_rag_chain_tables.sql
-- DS RAG chain output tables.
-- rag.doc_chunks(008) is the retrieval corpus; the two tables below hold RAG chain results / evaluation results.
-- Queries are built from the Risk Card's current values (gold_core), and the grounded recommended actions and citations are loaded here.

CREATE SCHEMA IF NOT EXISTS ${catalog}.${schema};

-- The grounded recommended action the RAG chain generates per finding.
-- answer_source distinguishes a rag generation from a deterministic fallback.
CREATE TABLE IF NOT EXISTS ${catalog}.${schema}.recommended_action (
  risk_case_key STRING NOT NULL,
  finding_id STRING NOT NULL,
  drift_type STRING NOT NULL,
  query_text STRING NOT NULL,
  answer_text STRING NOT NULL,
  answer_source STRING NOT NULL,          -- 'rag' | 'deterministic_fallback'
  citations STRING NOT NULL,              -- JSON array of chunk_id
  citation_titles STRING NOT NULL,        -- JSON array of source_title
  retrieved_chunk_ids STRING NOT NULL,    -- JSON array of chunk_id (all retrieved)
  num_retrieved INT NOT NULL,
  llm_endpoint STRING NOT NULL,           -- endpoint used for generation ('none' if fallback)
  generated_at TIMESTAMP NOT NULL,
  run_id STRING NOT NULL,
  CONSTRAINT recommended_action_pk PRIMARY KEY (risk_case_key)
)
USING DELTA;

-- RAG evaluation results. Loads retrieval/citation/groundedness metrics per eval-set case.
-- The detection pipeline does not read this table (eval isolation principle).
CREATE TABLE IF NOT EXISTS ${catalog}.${schema}.eval_results (
  eval_case_id STRING NOT NULL,
  drift_type STRING NOT NULL,
  question STRING NOT NULL,
  retrieved_chunk_ids STRING NOT NULL,    -- JSON array
  expected_doc_ids STRING NOT NULL,       -- JSON array
  retrieval_hit BOOLEAN NOT NULL,         -- at least one expected_doc_id is in the top-k
  has_citation BOOLEAN NOT NULL,          -- whether the response carried a citation
  answer_source STRING NOT NULL,          -- 'rag' | 'deterministic_fallback'
  groundedness DOUBLE,                    -- Agent Evaluation judge score (null if absent)
  correctness DOUBLE,                     -- Agent Evaluation judge score (null if absent)
  answer_text STRING NOT NULL,
  llm_endpoint STRING NOT NULL,
  evaluated_at TIMESTAMP NOT NULL,
  run_id STRING NOT NULL,
  CONSTRAINT rag_eval_results_pk PRIMARY KEY (eval_case_id)
)
USING DELTA;
