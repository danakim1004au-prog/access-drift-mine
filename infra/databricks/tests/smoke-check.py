# Databricks notebook source
# MAGIC %md
# MAGIC # Databricks Smoke Check (Day 2-3 gating prerequisite)
# MAGIC
# MAGIC Proves that **CREATE TABLE · INSERT · MERGE INTO · SELECT** can actually
# MAGIC run against the shared catalog/schema. Rather than inspecting (SHOW GRANTS), it **runs them directly**.
# MAGIC
# MAGIC It verifies two things separately:
# MAGIC
# MAGIC 1. **Part A - CRUD permissions per target schema**: for **each** of bronze/silver/intermediate/gold/eval,
# MAGIC    it creates a per-user temp table and runs CREATE/INSERT/MERGE/SELECT/DROP directly.
# MAGIC    → Proves, per schema, "can we actually CREATE TABLE / MODIFY / SELECT in silver".
# MAGIC 2. **Part B - format of existing contract tables**: checks that the already-present silver_*, intermediate, gold, etc.
# MAGIC    are in **delta format** per `DESCRIBE DETAIL`.
# MAGIC    → Proves "are the Day 3 Rule join-target tables in a MERGE-capable Delta state".
# MAGIC
# MAGIC - Each step is wrapped in try/except to collect "where it breaks" into a report (it doesn't stop at the first failure).
# MAGIC - Temp tables are made with a `_smoke_<user>` suffix and dropped immediately → no pollution of real data/namespaces.

# COMMAND ----------
dbutils.widgets.text("catalog", "access_drift")
dbutils.widgets.text("target_schemas", "bronze,silver,intermediate,gold,eval")
# Whether to enforce the gate-completion criterion ("contract tables confirmed in Delta format").
# true  = if a contract table is missing (SKIP) and its format couldn't be checked, treat the gate as incomplete and fail (default).
# false = when running just to check permissions before migration. Allows SKIP, but ends as PARTIAL rather than PASSED.
dbutils.widgets.dropdown("fail_on_skip", "true", ["true", "false"])

CATALOG = dbutils.widgets.get("catalog")
TARGET_SCHEMAS = [s.strip() for s in dbutils.widgets.get("target_schemas").split(",") if s.strip()]
FAIL_ON_SKIP = dbutils.widgets.get("fail_on_skip") == "true"

import re
_user = spark.sql("SELECT current_user() AS u").collect()[0].u
SUFFIX = re.sub(r"[^a-z0-9]", "_", _user.split("@")[0].lower())  # avoid collisions from concurrent runs on the shared cluster

# Day 3 Rule join targets - if present, check they are in delta format (SKIP if absent)
CONTRACT_TABLES = {
    "silver": ["silver_principals", "silver_credentials", "silver_assets", "silver_edges"],
    "intermediate": ["nhi_residual_access_findings"],
    "gold": ["gold_core"],
    "eval": ["ground_truth_case"],
}

print(f"current_user   : {_user}")
print(f"catalog        : {CATALOG}")
print(f"target_schemas : {TARGET_SCHEMAS}")

# COMMAND ----------
# MAGIC %md
# MAGIC ## Report-collection helper
# MAGIC Gathers PASS / FAIL / SKIP and a message per step. SKIP = not a permission problem but an unmet prerequisite (e.g. table not created).

# COMMAND ----------
results = []

class Skip(Exception):
    """Unmet prerequisite (table missing, etc.) - kept distinct from a permission failure."""

def step(name, fn):
    try:
        fn()
        results.append((name, "PASS", ""))
        print(f"[PASS] {name}")
    except Skip as e:
        results.append((name, "SKIP", str(e)[:300]))
        print(f"[SKIP] {name} — {e}")
    except Exception as e:
        msg = str(e).strip().splitlines()[0][:300]  # UC permission/format errors usually have the gist on the first line
        results.append((name, "FAIL", msg))
        print(f"[FAIL] {name}\n       {msg}")

# COMMAND ----------
# MAGIC %md
# MAGIC ## Part A - CRUD permissions per target schema
# MAGIC For each schema, a `_smoke_<user>` temp table: CREATE → INSERT → MERGE → SELECT → DROP.
# MAGIC MERGE passes only if both (1) target MODIFY permission and (2) Delta format are in place.

# COMMAND ----------
def run_crud(schema):
    tbl = f"{CATALOG}.{schema}._smoke_{SUFFIX}"
    p = f"{schema}: "

    def _use():
        # USE SCHEMA takes only a single schema name (passing catalog.schema raises UC_INVALID_NAMESPACE).
        # Set the catalog context with USE CATALOG, then set the schema with USE SCHEMA.
        spark.sql(f"USE CATALOG {CATALOG}")
        spark.sql(f"USE SCHEMA {schema}")
    step(p + "USE SCHEMA", _use)

    def _create():
        # Verify USING DELTA + NOT NULL + PRIMARY KEY, exactly like the contract DDL.
        # → Confirms that PK constraint creation actually works on the shared cluster / UC.
        spark.sql(f"DROP TABLE IF EXISTS {tbl}")
        spark.sql(f"""
            CREATE TABLE {tbl} (
              id STRING NOT NULL,
              payload STRING,
              updated_at TIMESTAMP NOT NULL,
              CONSTRAINT smoke_pk_{SUFFIX} PRIMARY KEY (id)
            ) USING DELTA
        """)
    step(p + "CREATE TABLE (Delta + PK)", _create)

    def _insert():
        spark.sql(f"""
            INSERT INTO {tbl} VALUES
              ('k1', 'v1', current_timestamp()),
              ('k2', 'v2', current_timestamp())
        """)
    step(p + "INSERT", _insert)

    def _merge():
        src = spark.createDataFrame(
            [("k1", "v1-updated"), ("k3", "v3-new")], "id string, payload string"
        )
        src.createOrReplaceTempView(f"smoke_src_{SUFFIX}")
        spark.sql(f"""
            MERGE INTO {tbl} AS t
            USING smoke_src_{SUFFIX} AS s
            ON t.id = s.id
            WHEN MATCHED THEN UPDATE SET t.payload = s.payload, t.updated_at = current_timestamp()
            WHEN NOT MATCHED THEN INSERT (id, payload, updated_at)
                VALUES (s.id, s.payload, current_timestamp())
        """)
    step(p + "MERGE INTO", _merge)

    def _select():
        rows = {r.id: r.payload for r in spark.sql(f"SELECT id, payload FROM {tbl}").collect()}
        assert rows.get("k1") == "v1-updated", f"k1 not updated: {rows.get('k1')}"
        assert rows.get("k2") == "v2",         f"k2 changed: {rows.get('k2')}"
        assert rows.get("k3") == "v3-new",     f"k3 not inserted: {rows.get('k3')}"
        assert len(rows) == 3,                 f"expected 3 rows, got {len(rows)}"
    step(p + "SELECT + verify MERGE result", _select)

    def _drop():
        spark.sql(f"DROP TABLE IF EXISTS {tbl}")
    step(p + "DROP TABLE (cleanup)", _drop)

for schema in TARGET_SCHEMAS:
    run_crud(schema)

# COMMAND ----------
# MAGIC %md
# MAGIC ## Part B - format of existing contract tables (Day 3 Rule targets)
# MAGIC Uses `DESCRIBE DETAIL` to check that the already-created silver_*, intermediate, gold, etc. are in **delta** format.
# MAGIC If a table doesn't exist yet, SKIP (migration not run) - kept distinct from a permission failure.

# COMMAND ----------
def check_format(schema, table):
    fq = f"{CATALOG}.{schema}.{table}"

    def _fmt():
        try:
            detail = spark.sql(f"DESCRIBE DETAIL {fq}").collect()[0]
        except Exception as e:
            first = str(e).splitlines()[0]
            if "NOT_FOUND" in first.upper() or "cannot be found" in first.lower():
                raise Skip("table missing - migration not run")
            raise
        fmt = (detail.format or "").lower()
        assert fmt == "delta", f"format={fmt} (not delta → MERGE not possible)"
        # For reference: also print the protocol version
        print(f"       {fq}: format=delta, "
              f"minReaderVersion={getattr(detail, 'minReaderVersion', '?')}, "
              f"minWriterVersion={getattr(detail, 'minWriterVersion', '?')}")
    step(f"format: {schema}.{table}", _fmt)

for schema, tables in CONTRACT_TABLES.items():
    if schema in TARGET_SCHEMAS:
        for table in tables:
            check_format(schema, table)

# COMMAND ----------
# MAGIC %md
# MAGIC ## Result summary

# COMMAND ----------
from pyspark.sql import Row
report = spark.createDataFrame([Row(step=n, status=s, detail=d) for n, s, d in results])
display(report)

n_pass = sum(1 for _, s, _ in results if s == "PASS")
skipped = [n for n, s, _ in results if s == "SKIP"]
failed = [n for n, s, _ in results if s == "FAIL"]
print(f"PASS={n_pass}  SKIP={len(skipped)}  FAIL={len(failed)}")

# 1) Any permission/format failure fails the gate outright
if failed:
    raise AssertionError(
        "smoke check FAILED: " + ", ".join(failed) +
        "\n  → On MERGE/CREATE failure: check that schema's CREATE TABLE/MODIFY permissions / Delta format / cluster access mode"
    )

# 2) If there are SKIPs, the gate-completion criterion ("contract tables confirmed in Delta format") wasn't met → PARTIAL
if skipped:
    msg = (
        "smoke check PARTIAL — CRUD permissions passed, but contract table format could not be confirmed (SKIP): "
        + ", ".join(skipped)
        + "\n  → run the migration (00_apply_migrations) then re-run to complete the gate"
    )
    if FAIL_ON_SKIP:
        raise AssertionError(msg)
    print(msg)
else:
    print("smoke check PASSED — CRUD on target schemas + contract table format all OK")
