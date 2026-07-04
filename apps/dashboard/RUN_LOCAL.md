# Local run guide for the dashboard + AI Agent chatbot (for teammates)

This doc walks a teammate through spinning up the Access Drift dashboard on their own PC and having a go at the **AI Agent chatbot** (RAG built on Databricks Vector Search + an FM LLM) in the "Recommended actions" tab of the Risk Card.

## 0. Prerequisites (one-off)

- Python 3.10+ (3.11 / 3.12 recommended)
- A login account for the team Databricks workspace (your own)
- The RAG result tables need to be loaded already
  - `access_drift_dev.rag.recommended_action` (for the AI Summary)
  - `access_drift_dev.rag.doc_chunks_index` (Vector Search index, for the chatbot search)
  - The Vector Search endpoint `access_drift_vs` in `ONLINE` state
  - (Already loaded / ONLINE — if empty, run `notebooks/12_rag_vector_index` and `13_rag_chain` first)

## 1. Install dependencies

```bash
cd apps/dashboard
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## 2. Set up the Databricks connection details (secrets)

```bash
cd apps/dashboard/.streamlit
cp secrets.toml.example secrets.toml
```

Open `secrets.toml` and swap `access_token` for **your own PAT**.

- Getting a PAT: workspace → profile → **Settings → Developer → Access tokens → Generate new token**
- If you have the Databricks CLI locally, you can reuse the `token` value from `~/.databrickscfg`
- Leave `server_hostname` / `http_path` / `catalog` etc. at the template defaults (shared team workspace)

> `secrets.toml` is listed in `.gitignore`, so it won't be committed. **Never commit your token.**
> If you don't create secrets, the dashboard automatically falls back to mock data, and the chatbot only returns deterministic fallback answers.

## 3. Run it

```bash
cd apps/dashboard
streamlit run app.py
```

In your browser, go to the address it prints (default `http://localhost:8501`) → left sidebar **Risk Card** → **Recommended actions** tab.

- On the left, **AI Summary**: the RAG recommended action plus reference docs the pipeline prepared earlier (shown automatically, no live call)
- On the right, **AI Agent**: type a question and hit Send → live answer from evidence retrieval + LLM generation (with citations)

### Suggested demo questions (on the NHI case)
- "Can I just delete this credential straight away?"
- "Who should I reassign the owner to?"
- "Is it safe to run this action automatically?"

## 4. Common issues

| Symptom | Cause / fix |
| --- | --- |
| `Using mock data` on screen | secrets.toml missing/typo → check `access_token` and `http_path` |
| Chatbot keeps saying "deterministic fallback" | Vector Search endpoint `access_drift_vs` is OFFLINE or the index is empty → re-run `12_rag_vector_index` |
| Chatbot response is slow (5–10s) | Normal. That's the FM LLM call round-trip (Llama 3.3 70B) |
| `ModuleNotFoundError` | Activate the venv, then re-check `pip install -r requirements.txt` |

## 5. Cost note

- The LLM token cost is tiny (≈ $0.0005 per answer). Even hundreds of chatbot clicks stay under $1.
- The always-on charge is the Vector Search endpoint (24/7). That's a running cost for the demo period and is billed to the workspace regardless of anyone running it on a personal PC.
