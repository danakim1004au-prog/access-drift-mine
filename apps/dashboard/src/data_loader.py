from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import hashlib
import json
import re
import time
import tomllib
import urllib.error
import urllib.request

import pandas as pd

from src.mock_data import generate_mock_access_drift_data
from src.schema import DATE_COLUMNS, EXPECTED_COLUMNS, get_missing_columns


DEFAULT_DATA_PATH = Path(__file__).resolve().parents[1] / "data" / "sample_access_drift.csv"
DEFAULT_SECRET_PATH = Path(__file__).resolve().parents[1] / ".streamlit" / "secrets.toml"

CASE_LIST_VIEW = "risk_case_list_view"
CARD_DETAIL_VIEW = "risk_card_detail_view"
ACCESS_PATH_VIEW = "risk_access_path_view"
ACTION_STATE_TABLE = "risk_action_state"
GOLD_CORE_TABLE = "gold_core"
RAG_RECOMMENDED_ACTION_TABLE = "recommended_action"


@dataclass(frozen=True)
class DataLoadResult:
    data: pd.DataFrame
    using_mock: bool
    source_path: Path | str
    missing_columns: list[str]


@dataclass(frozen=True)
class DatabricksConfig:
    server_hostname: str
    http_path: str
    access_token: str
    catalog: str
    gold_schema: str
    app_schema: str

    @property
    def warehouse_id(self) -> str:
        match = re.search(r"/warehouses/([^/?]+)", self.http_path)
        if not match:
            raise ValueError("Databricks http_path does not contain a warehouse id.")
        return match.group(1)

    @property
    def host_url(self) -> str:
        host = self.server_hostname.strip().rstrip("/")
        if not host.startswith(("http://", "https://")):
            host = f"https://{host}"
        return host


def load_access_drift_data(path: str | Path | None = None) -> DataLoadResult:
    if path is not None:
        return _load_csv_or_mock(Path(path))

    databricks_config = _load_databricks_config()
    if databricks_config is not None:
        try:
            raw_data = load_databricks_dashboard_data(databricks_config)
            normalized = normalize_to_internal_schema(raw_data)
            return DataLoadResult(
                data=normalized,
                using_mock=False,
                source_path=_databricks_source_label(databricks_config),
                missing_columns=get_missing_columns(raw_data.columns.tolist()),
            )
        except Exception:
            # Keep the dashboard usable if Databricks is unavailable. Do not surface
            # connection details here because secrets may be part of lower-level errors.
            pass

    return _load_csv_or_mock(DEFAULT_DATA_PATH)


def load_databricks_dashboard_data(config: DatabricksConfig) -> pd.DataFrame:
    case_list = _query_databricks_table(config, config.app_schema, CASE_LIST_VIEW)
    if case_list.empty:
        gold_core = _query_databricks_table(config, config.gold_schema, GOLD_CORE_TABLE)
        return _normalize_gold_core_rows(gold_core)

    detail = _query_databricks_table(config, config.app_schema, CARD_DETAIL_VIEW)
    access_path = _query_databricks_table(config, config.app_schema, ACCESS_PATH_VIEW)
    action_state = _query_databricks_table(config, config.app_schema, ACTION_STATE_TABLE)
    rag = _query_databricks_table(config, "rag", RAG_RECOMMENDED_ACTION_TABLE)

    return _normalize_app_view_rows(case_list, detail, access_path, action_state, rag)


def normalize_to_internal_schema(data: pd.DataFrame) -> pd.DataFrame:
    # TODO: Keep source-specific CSV/DB/Gold Layer mappings in this loader so pages
    # and components can continue reading the stable internal dashboard schema.
    normalized = data.copy()

    for column in EXPECTED_COLUMNS:
        if column not in normalized.columns:
            normalized[column] = None

    if "case_id" in normalized.columns:
        normalized["risk_case_key"] = normalized["risk_case_key"].where(
            _has_value(normalized["risk_case_key"]), normalized["case_id"]
        )
    if "risk_case_key" in normalized.columns:
        normalized["case_id"] = normalized["case_id"].where(
            _has_value(normalized["case_id"]), normalized["risk_case_key"]
        )
        normalized["display_case_id"] = normalized["display_case_id"].where(
            ~_has_value(normalized["risk_case_key"]), normalized["risk_case_key"].map(_display_case_id_from_key)
        )
    normalized["asset_full_name"] = normalized["asset_full_name"].where(
        _has_value(normalized["asset_full_name"]), normalized["asset_name"]
    )

    normalized = normalized[EXPECTED_COLUMNS]

    for column in DATE_COLUMNS:
        normalized[column] = pd.to_datetime(normalized[column], errors="coerce")

    normalized["risk_score"] = pd.to_numeric(normalized["risk_score"], errors="coerce").fillna(0)

    return normalized


def _load_csv_or_mock(source_path: Path) -> DataLoadResult:
    if source_path.exists():
        raw_data = pd.read_csv(source_path)
        using_mock = False
    else:
        raw_data = generate_mock_access_drift_data()
        using_mock = True

    normalized = normalize_to_internal_schema(raw_data)
    return DataLoadResult(
        data=normalized,
        using_mock=using_mock,
        source_path=source_path,
        missing_columns=get_missing_columns(raw_data.columns.tolist()),
    )


def _normalize_app_view_rows(
    case_list: pd.DataFrame,
    detail: pd.DataFrame,
    access_path: pd.DataFrame,
    action_state: pd.DataFrame,
    rag: pd.DataFrame | None = None,
) -> pd.DataFrame:
    rows = case_list.copy()

    rows = _merge_by_case_key(rows, detail, "detail")
    rows = _merge_by_case_key(rows, access_path, "path")
    rows = _merge_by_case_key(rows, action_state, "state")
    if rag is not None:
        rows = _merge_by_case_key(rows, rag, "rag")

    normalized = pd.DataFrame()
    normalized["risk_case_key"] = _first_present(rows, ["risk_case_key"])
    normalized["case_id"] = normalized["risk_case_key"]
    normalized["display_case_id"] = normalized["risk_case_key"].map(_display_case_id_from_key)
    normalized["risk_title"] = _first_present(rows, ["card_title", "drift_type_label", "drift_type"])
    normalized["risk_level"] = _normalize_severity(_first_present(rows, ["severity", "severity_detail", "severity_path"]))
    normalized["drift_type"] = _first_present(rows, ["drift_type_label", "drift_type_detail", "drift_type"])
    normalized["identity_name"] = _first_present(rows, ["identity_name", "subject_display_name", "subject_display_name_detail", "subject_display_name_path"])
    normalized["identity_type"] = _normalize_identity_type(_first_present(rows, ["identity_type", "subject_type", "subject_type_detail", "subject_type_path"]))
    normalized["object_name"] = _first_present(rows, ["subject_display_name_detail", "subject_display_name_path", "identity_name"])
    normalized["object_type"] = _humanize_label_series(
        _first_present(rows, ["subject_object_type", "subject_object_type_path", "subject_type_detail", "subject_type_path"])
    )
    normalized["credential_type"] = _humanize_label_series(
        _first_present(rows, ["credential_type", "credential_type_detail", "credential_type_path"])
    )
    normalized["credential_expires_at"] = _date_label_series(
        _first_present(rows, ["credential_expires_at", "credential_expires_at_detail", "credential_expires_at_path"])
    )
    normalized["credential_status"] = _first_present(rows, ["credential_status", "credential_status_path"])
    normalized["credential_status"] = normalized["credential_status"].where(
        normalized["credential_status"].astype(str).str.len().gt(0),
        _credential_status_from_active(_first_present(rows, ["credential_is_active", "credential_is_active_detail", "factor_credential_active"])),
    )
    normalized["owner_status"] = _first_present(rows, ["owner_account_status", "factor_owner_status", "subject_status"])
    normalized["owner_name"] = _first_present(rows, ["owner_name", "owner_display_name", "owner_display_name_detail", "owner_display_name_path"])
    normalized["owner_team"] = _first_present(rows, ["assignee_team", "reviewer_team"])
    normalized["related_person_status"] = _related_person_status(rows)
    normalized["linked_owner_label"] = _linked_owner_label(normalized["owner_name"])
    asset_full_name = _first_present(rows, ["asset_display_name", "asset_display_name_detail", "asset_display_name_path"])
    normalized["asset_name"] = _asset_summary_series(asset_full_name)
    normalized["asset_full_name"] = asset_full_name
    normalized["asset_type"] = _first_present(rows, ["asset_type"])
    normalized["asset_sensitivity"] = _first_present(rows, ["asset_sensitivity", "asset_sensitivity_detail", "asset_sensitivity_path"])
    normalized["data_scope"] = _first_present(rows, ["data_scope_label"])
    normalized["data_categories"] = _array_to_display(_first_present(rows, ["sensitive_data_examples"]))
    normalized["permission_level"] = _first_present(rows, ["access_permission", "access_permission_detail", "access_permission_path", "factor_permission"])
    normalized["last_accessed_at"] = pd.NaT
    normalized["detected_at"] = _first_present(rows, ["detected_at", "detected_at_detail", "detected_at_path"])
    normalized["created_at"] = pd.NaT
    normalized["rotation_status"] = _rotation_status_from_credential(rows)
    normalized["risk_score"] = _risk_score_from_severity(normalized["risk_level"])
    normalized["review_status"] = _normalize_review_status(_first_present(rows, ["status", "action_status", "action_status_detail"]))
    normalized["reviewer"] = _person_team(_first_present(rows, ["reviewer_name", "reviewer_name_detail", "reviewer_name_state"]), _first_present(rows, ["reviewer_team", "reviewer_team_detail", "reviewer_team_state"]))
    normalized["action_owner"] = _person_team(_first_present(rows, ["assignee_name", "assignee_name_detail"]), _first_present(rows, ["assignee_team", "assignee_team_detail"]))
    rag_answer = _first_present(rows, ["rag_answer_text", "answer_text", "answer_text_rag"])
    normalized["recommended_action"] = rag_answer.where(_has_value(rag_answer), _first_present(rows, ["deterministic_recommended_action", "recommended_action"]))
    normalized["risk_summary"] = _first_present(rows, ["risk_summary", "evidence_summary"])
    normalized["risk_factors"] = rows.apply(_risk_factors_from_row, axis=1)
    normalized["action_plan"] = rows.apply(_action_plan_from_row, axis=1)
    normalized["ai_agent_messages"] = rows.apply(_ai_messages_from_row, axis=1)
    normalized["overview_title"] = _first_present(rows, ["drift_type_label", "card_title", "drift_type"])
    normalized["ai_summary"] = rows.apply(_ai_summary_from_row, axis=1)
    normalized["reference_docs"] = rows.apply(_reference_docs_from_row, axis=1)

    return normalized


def _normalize_gold_core_rows(gold_core: pd.DataFrame) -> pd.DataFrame:
    normalized = pd.DataFrame()
    normalized["risk_case_key"] = _first_present(gold_core, ["risk_case_key", "finding_id"])
    normalized["case_id"] = normalized["risk_case_key"]
    normalized["display_case_id"] = normalized["risk_case_key"].map(_display_case_id_from_key)
    normalized["risk_title"] = _first_present(gold_core, ["drift_type"])
    normalized["risk_level"] = _normalize_severity(_first_present(gold_core, ["severity"]))
    normalized["drift_type"] = _first_present(gold_core, ["drift_type"])
    normalized["identity_name"] = _first_present(gold_core, ["subject_display_name"])
    normalized["identity_type"] = _normalize_identity_type(_first_present(gold_core, ["subject_type"]))
    normalized["object_name"] = _first_present(gold_core, ["subject_display_name"])
    normalized["object_type"] = _humanize_label_series(_first_present(gold_core, ["subject_type"]))
    normalized["credential_type"] = ""
    normalized["credential_expires_at"] = ""
    normalized["credential_status"] = ""
    normalized["owner_status"] = ""
    normalized["owner_name"] = _first_present(gold_core, ["owner_display_name"])
    normalized["owner_team"] = ""
    normalized["related_person_status"] = ""
    normalized["linked_owner_label"] = _linked_owner_label(normalized["owner_name"])
    asset_full_name = _first_present(gold_core, ["asset_display_name"])
    normalized["asset_name"] = _asset_summary_series(asset_full_name)
    normalized["asset_full_name"] = asset_full_name
    normalized["asset_type"] = ""
    normalized["asset_sensitivity"] = _first_present(gold_core, ["asset_sensitivity"])
    normalized["data_scope"] = ""
    normalized["data_categories"] = ""
    normalized["permission_level"] = ""
    normalized["last_accessed_at"] = pd.NaT
    normalized["detected_at"] = _first_present(gold_core, ["detected_at"])
    normalized["created_at"] = pd.NaT
    normalized["rotation_status"] = ""
    normalized["risk_score"] = _risk_score_from_severity(normalized["risk_level"])
    normalized["review_status"] = "Open"
    normalized["reviewer"] = ""
    normalized["action_owner"] = _first_present(gold_core, ["owner_display_name"])
    normalized["recommended_action"] = _first_present(gold_core, ["recommended_action"])
    normalized["risk_summary"] = _first_present(gold_core, ["evidence_summary"])
    normalized["risk_factors"] = [[] for _ in range(len(gold_core))]
    normalized["action_plan"] = [[] for _ in range(len(gold_core))]
    normalized["ai_agent_messages"] = [[] for _ in range(len(gold_core))]
    normalized["overview_title"] = _first_present(gold_core, ["drift_type"])
    normalized["ai_summary"] = _first_present(gold_core, ["evidence_summary"])
    normalized["reference_docs"] = [[] for _ in range(len(gold_core))]
    return normalized



def _json_array(value: object) -> list[object]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if value is None or pd.isna(value):
        return []
    text = str(value).strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


def _row_first_value(row: pd.Series, columns: list[str], default: object = "") -> object:
    for column in columns:
        if column in row and not pd.isna(row[column]) and str(row[column]) != "":
            return row[column]
    return default


def _reference_docs_from_row(row: pd.Series) -> list[dict[str, str]]:
    chunk_ids = _json_array(_row_first_value(row, ["rag_citations", "citations", "citations_rag"], "[]"))
    titles = _json_array(_row_first_value(row, ["rag_citation_titles", "citation_titles", "citation_titles_rag"], "[]"))
    docs = []
    for index, chunk_id in enumerate(chunk_ids):
        title = str(titles[index]) if index < len(titles) and titles[index] else str(chunk_id)
        docs.append({"title": title, "source": str(chunk_id), "type": "RAG evidence"})
    return docs


def _action_plan_from_row(row: pd.Series) -> list[str]:
    answer = str(_row_first_value(row, ["rag_answer_text", "answer_text", "answer_text_rag", "deterministic_recommended_action", "recommended_action"], ""))
    parts = _extract_action_steps(answer)
    return parts[:5] if parts else []


def _ai_summary_from_row(row: pd.Series) -> str:
    answer = str(_row_first_value(row, ["rag_answer_text", "answer_text", "answer_text_rag", "deterministic_recommended_action", "recommended_action", "risk_summary"], ""))
    steps = _extract_action_steps(answer)
    if steps:
        return " ".join(steps[:2])
    return _clean_recommendation_text(answer)


def _extract_action_steps(answer: str) -> list[str]:
    cleaned = _clean_recommendation_text(answer)
    if not cleaned:
        return []

    numbered_parts = re.split(r"(?:^|\s)(?:\d+)[.)]\s+", cleaned)
    candidates = numbered_parts[1:] if len(numbered_parts) > 1 else re.split(r"[\n]+|(?<=[.!?。])\s+", cleaned)

    steps: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        step = _strip_step_noise(candidate)
        if not step or step in seen:
            continue
        steps.append(step)
        seen.add(step)
    return steps


def _clean_recommendation_text(answer: str) -> str:
    text = str(answer or "").strip()
    if not text:
        return ""
    text = re.sub(r"^(?:RAG|Deterministic fallback)(?:\s*[·:]\s*[^:]+)?:\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _strip_step_noise(text: str) -> str:
    text = re.sub(r"\s*\[[^\]]+\]", "", str(text or ""))
    text = text.strip(" -•\t\n\r")
    text = re.sub(r"^\d+[.)]\s*", "", text).strip()
    text = re.sub(r"\s+", " ", text)
    if not text or re.fullmatch(r"\d+[.)]?", text):
        return ""
    if re.fullmatch(r"\[[^\]]+\]", text):
        return ""
    return text


def _ai_messages_from_row(row: pd.Series) -> list[str]:
    source = str(_row_first_value(row, ["rag_answer_source", "answer_source", "answer_source_rag"], "deterministic_fallback"))
    docs = _reference_docs_from_row(row)
    doc_note = f"Checked {len(docs)} reference document(s)." if docs else "There is currently no citable LLM response, so the deterministic fallback was used."
    return [
        "I've drafted a recommended action.",
        "What is this action based on?",
        f"answer_source={source}. {doc_note}",
    ]

def _query_databricks_table(config: DatabricksConfig, schema_name: str, table_name: str) -> pd.DataFrame:
    statement = f"SELECT * FROM {_qualified_name(config.catalog, schema_name, table_name)} LIMIT 1000"
    return _execute_databricks_statement(config, statement)


def _execute_databricks_statement(config: DatabricksConfig, statement: str) -> pd.DataFrame:
    base_url = f"{config.host_url}/api/2.0/sql/statements"
    headers = {
        "Authorization": f"Bearer {config.access_token}",
        "Content-Type": "application/json",
    }
    payload = json.dumps(
        {
            "warehouse_id": config.warehouse_id,
            "statement": statement,
            "wait_timeout": "10s",
            "disposition": "INLINE",
            "format": "JSON_ARRAY",
        }
    ).encode("utf-8")
    request = urllib.request.Request(base_url, data=payload, headers=headers, method="POST")

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Databricks statement failed with HTTP {exc.code}.") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError("Databricks statement failed due to a network error.") from exc

    statement_id = result.get("statement_id")
    state = result.get("status", {}).get("state")
    for _ in range(20):
        if state in {"SUCCEEDED", "FAILED", "CANCELED", "CLOSED"}:
            break
        time.sleep(1)
        poll_request = urllib.request.Request(f"{base_url}/{statement_id}", headers=headers)
        with urllib.request.urlopen(poll_request, timeout=30) as response:
            result = json.loads(response.read().decode("utf-8"))
        state = result.get("status", {}).get("state")

    if state != "SUCCEEDED":
        raise RuntimeError("Databricks statement did not succeed.")

    columns = [
        column["name"]
        for column in result.get("manifest", {}).get("schema", {}).get("columns", [])
    ]
    rows = result.get("result", {}).get("data_array", [])
    return pd.DataFrame(rows, columns=columns)


def _load_databricks_config() -> DatabricksConfig | None:
    secrets = _read_databricks_secrets()
    required = ["server_hostname", "http_path", "access_token", "catalog", "gold_schema", "app_schema"]
    if not all(secrets.get(key) for key in required):
        return None
    return DatabricksConfig(
        server_hostname=str(secrets["server_hostname"]),
        http_path=str(secrets["http_path"]),
        access_token=str(secrets["access_token"]),
        catalog=str(secrets["catalog"]),
        gold_schema=str(secrets["gold_schema"]),
        app_schema=str(secrets["app_schema"]),
    )


def _read_databricks_secrets() -> dict[str, object]:
    try:
        import streamlit as st

        if "databricks" in st.secrets:
            return dict(st.secrets["databricks"])
    except Exception:
        pass

    if DEFAULT_SECRET_PATH.exists():
        return dict(tomllib.loads(DEFAULT_SECRET_PATH.read_text(encoding="utf-8")).get("databricks", {}))
    return {}


def _merge_by_case_key(left: pd.DataFrame, right: pd.DataFrame, suffix: str) -> pd.DataFrame:
    if right.empty or "risk_case_key" not in right.columns:
        return left
    return left.merge(right, on="risk_case_key", how="left", suffixes=("", f"_{suffix}"))


def _first_present(data: pd.DataFrame, columns: list[str], default: object = "") -> pd.Series:
    result = pd.Series([default] * len(data), index=data.index, dtype="object")
    for column in columns:
        if column not in data.columns:
            continue
        candidate = data[column]
        should_fill = ~_has_value(result) & _has_value(candidate)
        result = result.where(~should_fill, candidate)
    return result


def _has_value(series: pd.Series) -> pd.Series:
    return series.notna() & series.astype(str).ne("")


def _normalize_severity(series: pd.Series) -> pd.Series:
    mapping = {
        "critical": "Critical",
        "high": "High",
        "medium": "Medium",
        "low": "Low",
        "none": "None",
    }
    return series.fillna("").astype(str).str.strip().str.lower().map(mapping).fillna(series.fillna("").astype(str))


def _normalize_identity_type(series: pd.Series) -> pd.Series:
    mapping = {
        "nhi": "NHI",
        "non_human_identity": "NHI",
        "non-human identity": "NHI",
        "service_principal": "NHI",
        "service principal": "NHI",
        "hi": "HI",
        "human_identity": "HI",
        "human identity": "HI",
        "user": "HI",
    }
    return series.fillna("").astype(str).str.strip().str.lower().map(mapping).fillna(series.fillna("").astype(str))


def _normalize_review_status(series: pd.Series) -> pd.Series:
    mapping = {
        "open": "Open",
        "in_review": "In Review",
        "in review": "In Review",
        "reviewing": "In Review",
        "in progress": "In Review",
        "resolved": "Resolved",
        "closed": "Resolved",
    }
    normalized = series.fillna("").astype(str).str.strip()
    return normalized.str.lower().map(mapping).fillna(normalized.where(normalized.ne(""), "Open"))


def _credential_status_from_active(series: pd.Series) -> pd.Series:
    active = series.astype(str).str.lower().isin({"true", "1", "yes", "active"})
    inactive = series.astype(str).str.lower().isin({"false", "0", "no", "inactive"})
    return pd.Series(["Active" if is_active else "Inactive" if is_inactive else "" for is_active, is_inactive in zip(active, inactive)], index=series.index)


def _related_person_status(data: pd.DataFrame) -> pd.Series:
    termination = _first_present(data, ["owner_termination_date"])
    status = _first_present(data, ["owner_account_status", "factor_owner_status", "subject_status"])
    formatted_dates = pd.to_datetime(termination, errors="coerce").dt.strftime("%Y/%m/%d")
    return pd.Series(
        [
            f"Termination date {date_value}" if isinstance(date_value, str) and date_value else status_value
            for date_value, status_value in zip(formatted_dates.fillna(""), status.fillna(""))
        ],
        index=data.index,
    )


def _linked_owner_label(owner_name: pd.Series) -> pd.Series:
    return owner_name.fillna("").astype(str).map(lambda value: f"Linked to {value}" if value else "")


def _person_team(name: pd.Series, team: pd.Series) -> pd.Series:
    return pd.Series(
        [
            f"{name_value} · {team_value}" if name_value and team_value else name_value or team_value
            for name_value, team_value in zip(name.fillna("").astype(str), team.fillna("").astype(str))
        ],
        index=name.index,
    )


def _array_to_display(series: pd.Series) -> pd.Series:
    return series.map(_value_to_display)


def _value_to_display(value: object) -> str:
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    if isinstance(value, tuple):
        return ", ".join(str(item) for item in value)
    if pd.isna(value):
        return ""
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            try:
                parsed = json.loads(stripped)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, list):
                return ", ".join(str(item) for item in parsed)
            return stripped.strip("[]").replace('"', "").replace("'", "")
    return str(value)


def _humanize_label_series(series: pd.Series) -> pd.Series:
    return series.map(_humanize_label)


def _humanize_label(value: object) -> str:
    if value is None or pd.isna(value):
        return ""
    text = str(value).strip()
    if not text:
        return ""
    mapping = {
        "app_registration": "App Registration",
        "service_principal": "Service Principal",
        "client_secret": "Client Secret",
        "managed_identity": "Managed Identity",
        "personal_access_token": "Personal Access Token",
        "access_key": "Access Key",
        "sas_token": "SAS Token",
    }
    key = text.lower().replace("-", "_").replace(" ", "_")
    if key in mapping:
        return mapping[key]
    return " ".join(part.capitalize() for part in re.split(r"[_\-\s]+", text) if part)


def _date_label_series(series: pd.Series) -> pd.Series:
    parsed = pd.to_datetime(series, errors="coerce")
    return pd.Series(
        [value.strftime("%Y-%m-%d") if not pd.isna(value) else "" for value in parsed],
        index=series.index,
    )


def _asset_summary_series(series: pd.Series) -> pd.Series:
    return series.map(_asset_summary)


def _asset_summary(value: object) -> str:
    if value is None or pd.isna(value):
        return ""
    text = str(value).strip()
    if not text:
        return ""

    tokens = [token for token in re.split(r"[-_\s]+", text.lower()) if token]
    environment_tokens = {"prod", "production", "dev", "development", "staging", "stage", "qa", "test"}
    tokens = [token for token in tokens if token not in environment_tokens]
    if not tokens:
        return text

    if "storage" in tokens:
        storage_index = tokens.index("storage")
        prefix = tokens[storage_index - 1] if storage_index > 0 else ""
        if prefix in {"customer", "crm", "data"}:
            prefix = ""
        summary_tokens = [token for token in [prefix, "storage"] if token]
        return _title_from_tokens(summary_tokens)

    if "github" in tokens:
        return "GitHub"

    return _title_from_tokens(tokens[-2:] if len(tokens) > 2 else tokens)


def _title_from_tokens(tokens: list[str]) -> str:
    acronyms = {"api": "API", "crm": "CRM", "db": "DB", "rbac": "RBAC"}
    return " ".join(acronyms.get(token, token.capitalize()) for token in tokens)


def _rotation_status_from_credential(data: pd.DataFrame) -> pd.Series:
    expires_at = _first_present(data, ["credential_expires_at"])
    parsed = pd.to_datetime(expires_at, errors="coerce")
    return pd.Series(
        ["Expires at " + value.strftime("%Y/%m/%d") if not pd.isna(value) else "" for value in parsed],
        index=data.index,
    )


def _risk_score_from_severity(severity: pd.Series) -> pd.Series:
    score = {"Critical": 100, "High": 90, "Medium": 70, "Low": 40, "None": 0}
    return severity.map(score).fillna(0)


def _display_case_id_from_key(value: object) -> str:
    if value is None or pd.isna(value):
        return ""
    key = str(value)
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:6].upper()
    return f"CASE-{digest}"


def _risk_factors_from_row(row: pd.Series) -> list[str]:
    factors: list[str] = []
    owner_status = _row_value(row, ["factor_owner_status", "owner_account_status"])
    credential_active = _row_value(row, ["factor_credential_active", "credential_is_active", "credential_is_active_detail"])
    permission = _row_value(row, ["factor_permission", "access_permission", "access_permission_detail", "access_permission_path"])
    sensitivity = _row_value(row, ["factor_asset_sensitivity", "asset_sensitivity", "asset_sensitivity_detail", "asset_sensitivity_path"])
    sensitive_access = _row_value(row, ["factor_sensitive_access"])

    if owner_status:
        factors.append(f"Owner status: {owner_status}")
    if credential_active != "":
        factors.append("Credential active" if str(credential_active).lower() in {"true", "1", "yes"} else f"Credential status: {credential_active}")
    if permission:
        factors.append(f"Permission: {permission}")
    if sensitivity:
        factors.append(f"Asset sensitivity: {sensitivity}")
    if str(sensitive_access).lower() in {"true", "1", "yes"}:
        factors.append("Sensitive asset access")
    return factors


def _row_value(row: pd.Series, columns: list[str]) -> object:
    for column in columns:
        if column in row and not pd.isna(row[column]) and str(row[column]) != "":
            return row[column]
    return ""


def _qualified_name(catalog: str, schema_name: str, table_name: str) -> str:
    return ".".join(_quote_identifier(part) for part in [catalog, schema_name, table_name])


def _quote_identifier(value: str) -> str:
    return f"`{value.replace('`', '``')}`"


def _databricks_source_label(config: DatabricksConfig) -> str:
    return f"{config.catalog}.{config.app_schema}.{CASE_LIST_VIEW}"
