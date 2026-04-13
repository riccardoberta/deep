from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
import requests


# ---------------------------------------------------------------------------
# Period-prefix → column suffix used when flattening scopus_metrics.
# The actual period strings look like "05 years (2021-2026)";
# startswith matching keeps the mapping stable across different year ranges.
# ---------------------------------------------------------------------------
_PERIOD_PREFIXES: List[tuple[str, str]] = [
    ("05 years",  "5y"),
    ("10 years",  "10y"),
    ("15 years",  "15y"),
]


def _suffix_for_period(period: str) -> Optional[str]:
    p = period.strip()
    for prefix, suffix in _PERIOD_PREFIXES:
        if p.startswith(prefix):
            return suffix
    if p.lower() == "absolute":
        return "abs"
    return None


def _get_metric(mbs: Dict[str, Dict[str, Any]], suffix: str, field: str) -> Optional[float]:
    v = mbs.get(suffix, {}).get(field)
    return float(v) if v is not None else None


# ---------------------------------------------------------------------------
# Teaching helper: count total courses across all academic years.
# ---------------------------------------------------------------------------
def _count_courses(teaching: Any) -> Optional[int]:
    if not isinstance(teaching, dict):
        return None
    return sum(len(v) for v in teaching.values() if isinstance(v, list))


def _teaching_years(teaching: Any) -> Optional[str]:
    if not isinstance(teaching, dict) or not teaching:
        return None
    return ", ".join(sorted(teaching.keys()))


# ---------------------------------------------------------------------------
# load_all_runs: one flat row per (run × member) with ALL scalar fields
# ---------------------------------------------------------------------------
def load_all_runs(data_dir: Path) -> tuple[pd.DataFrame, List[Dict[str, Any]]]:
    """
    Walk every data/<YYYY_MM_DD_N>/source/*.json file under *data_dir*.

    Returns
    -------
    df : pd.DataFrame
        One row per (run, member).  All scalar/countable fields are columns.
    records : list[dict]
        Full raw payloads with two extra keys injected: ``run_date`` and
        ``run_label``.  Useful for queries on nested structures (career,
        teaching, location, scopus_products, iris_products).
    """
    run_pattern = re.compile(r"^(\d{4}_\d{2}_\d{2})_(\d+)$")
    rows: List[Dict[str, Any]] = []
    records: List[Dict[str, Any]] = []

    if not data_dir.is_dir():
        return pd.DataFrame(), []

    for run_dir in sorted(data_dir.iterdir()):
        if not run_dir.is_dir():
            continue
        m = run_pattern.match(run_dir.name)
        if not m:
            continue

        date_str  = m.group(1)
        run_idx   = int(m.group(2))
        parts     = date_str.split("_")
        run_label = f"{parts[0]}/{parts[1]}/{parts[2]} #{run_idx}"

        source_dir = run_dir / "source"
        if not source_dir.is_dir():
            continue

        for json_path in sorted(source_dir.glob("*.json")):
            try:
                payload: Dict[str, Any] = json.loads(
                    json_path.read_text(encoding="utf-8")
                )
            except Exception:
                continue

            # --- Inject run info into raw record ---
            enriched = dict(payload)
            enriched["run_date"]  = date_str
            enriched["run_label"] = run_label
            records.append(enriched)

            # --- Index metrics by period suffix ---
            mbs: Dict[str, Dict[str, Any]] = {}
            for entry in payload.get("scopus_metrics") or []:
                suffix = _suffix_for_period(entry.get("period", ""))
                if suffix:
                    mbs[suffix] = entry

            scores   = payload.get("scores") or {}
            career   = payload.get("career") or []
            teaching = payload.get("teaching") or {}
            location = payload.get("location") or []

            def score(indicator: str) -> Optional[float]:
                v = (scores.get(indicator) or {}).get("score")
                return float(v) if v is not None else None

            # Latest career entry (first in list = most recent)
            latest_role = career[0].get("role") if career else None
            latest_from = career[0].get("from")  if career else None

            rows.append({
                # ── Run info ─────────────────────────────────────────────
                "run_date":          date_str,
                "run_index":         run_idx,
                "run_label":         run_label,
                # ── Identity ─────────────────────────────────────────────
                "surname":           payload.get("surname", ""),
                "name":              payload.get("name", ""),
                "unit":              payload.get("unit", ""),
                "grade":             payload.get("grade", ""),
                "role":              payload.get("role", ""),
                "ssd":               payload.get("ssd", ""),
                "scopus_id":         payload.get("scopus_id", ""),
                "unige_id":          payload.get("unige_id", ""),
                # ── Contact ──────────────────────────────────────────────
                "email":             payload.get("email", ""),
                "phone":             payload.get("phone", ""),
                "page":              payload.get("page", ""),
                "website":           payload.get("website", ""),
                # ── Location (first entry) ────────────────────────────────
                "building":          location[0].get("building") if location else None,
                "floor":             location[0].get("floor")    if location else None,
                "room":              location[0].get("room")     if location else None,
                # ── Career (latest entry) ─────────────────────────────────
                "current_role":      latest_role,
                "role_since":        latest_from,
                "career_entries":    len(career),
                # ── Teaching ─────────────────────────────────────────────
                "teaching_courses":  _count_courses(teaching),
                "teaching_years":    _teaching_years(teaching),
                # ── Bibliometrics ─────────────────────────────────────────
                "products_5y":       _get_metric(mbs, "5y",  "total_products"),
                "products_10y":      _get_metric(mbs, "10y", "total_products"),
                "products_15y":      _get_metric(mbs, "15y", "total_products"),
                "products_abs":      _get_metric(mbs, "abs", "total_products"),
                "citations_5y":      _get_metric(mbs, "5y",  "citations"),
                "citations_10y":     _get_metric(mbs, "10y", "citations"),
                "citations_15y":     _get_metric(mbs, "15y", "citations"),
                "citations_abs":     _get_metric(mbs, "abs", "citations"),
                "h_index_5y":        _get_metric(mbs, "5y",  "hindex"),
                "h_index_10y":       _get_metric(mbs, "10y", "hindex"),
                "h_index_15y":       _get_metric(mbs, "15y", "hindex"),
                "h_index_abs":       _get_metric(mbs, "abs", "hindex"),
                # ── Threshold scores ──────────────────────────────────────
                "score_articles":    score("articles"),
                "score_citations":   score("citations"),
                "score_hindex":      score("hindex"),
                # ── Publication counts ────────────────────────────────────
                "scopus_products":   len(payload.get("scopus_products") or []),
                "iris_products":     len(payload.get("iris_products")   or []),
                # ── Retrieval ────────────────────────────────────────────
                "retrieved_at":      payload.get("retrieved_at", ""),
            })

    return pd.DataFrame(rows), records


# ---------------------------------------------------------------------------
# Safety tokens – if any appear in LLM-generated code we refuse to run it.
# ---------------------------------------------------------------------------
_FORBIDDEN_TOKENS = (
    "import ",
    "__",
    "open(",
    "exec(",
    "eval(",
    "subprocess",
    "os.",
    "sys.",
)


def _extract_code(text: str) -> str:
    """Pull a ```python … ``` block out of *text*, or return *text* stripped."""
    fenced = re.search(r"```(?:python)?\s*\n(.*?)```", text, re.DOTALL)
    if fenced:
        return fenced.group(1).strip()
    return text.strip()


# ---------------------------------------------------------------------------
# Schema description injected into the prompt
# ---------------------------------------------------------------------------
_SCHEMA_DESCRIPTION = """
`df` columns (one row per run × member):
  run_date, run_index, run_label          – which import this row comes from
  surname, name, unit, grade, role, ssd   – identity & academic role
  scopus_id, unige_id                     – system identifiers
  email, phone, page, website             – contact info
  building, floor, room                   – office location (first address)
  current_role, role_since, career_entries – current career position
  teaching_courses, teaching_years        – total courses taught & years active
  products_5y/10y/15y/abs                 – publication counts per window
  citations_5y/10y/15y/abs               – citation counts per window
  h_index_5y/10y/15y/abs                 – h-index per window
  score_articles, score_citations, score_hindex  – threshold scores (0/0.4/0.8/1.2)
  scopus_products, iris_products          – number of products in each archive
  retrieved_at                            – timestamp of data retrieval

`records` is a list of raw payload dicts (one per run × member).
Each dict has the same run_date/run_label keys plus all nested fields:
  career   – list of {role, from, to}
  teaching – dict of year → list of courses
  location – list of {building, floor, room}
  scopus_products / iris_products – full publication lists
Use `records` when you need to look inside nested structures.
To convert a filtered subset back to a DataFrame: pd.DataFrame([...]).
""".strip()


def query_llm(
    question: str,
    df: pd.DataFrame,
    records: List[Dict[str, Any]],
    *,
    ollama_url: str,
    model: str,
) -> tuple[pd.DataFrame, str]:
    """
    Send *question* to a local Ollama LLM and return a DataFrame answer.

    Both the flat ``df`` and the raw ``records`` list are available to the
    generated code so it can handle both scalar and nested queries.

    Raises RuntimeError on network errors, safety violations, or exec failures.
    """
    n_runs   = df["run_label"].nunique() if "run_label" in df.columns else 0
    preview  = df.head(3).to_json(orient="records", indent=2)

    prompt = f"""You are a Python data analyst working with faculty bibliometric data.

{_SCHEMA_DESCRIPTION}

Dataset: {len(df)} rows, {n_runs} distinct import(s).

Sample rows from `df` (first 3):
{preview}

User question: {question}

Instructions:
- You have access to `df` (pandas DataFrame) and `records` (list of dicts).
- Also available: the `pd` alias.
- Store the final answer in a variable named `result_df` (must be a DataFrame).
- Do NOT import any module. Do NOT use open(), exec(), eval(), os, sys or subprocess.
- Include surname and name in the result; keep only columns relevant to the answer.
- Output ONLY the Python code wrapped in triple backticks.
"""

    try:
        resp = requests.post(
            ollama_url.rstrip("/") + "/api/generate",
            json={"model": model, "prompt": prompt, "stream": False},
            timeout=120,
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise RuntimeError(f"Ollama request failed: {exc}") from exc

    code = _extract_code(resp.json().get("response", ""))

    for token in _FORBIDDEN_TOKENS:
        if token in code:
            raise RuntimeError(
                f"Generated code contains forbidden token {token!r} – refusing to execute."
            )

    local_ns: Dict[str, Any] = {}
    try:
        exec(code, {"pd": pd, "df": df.copy(), "records": list(records)}, local_ns)  # noqa: S102
    except Exception as exc:
        raise RuntimeError(
            f"Generated code raised an error: {exc}\n\nGenerated code:\n{code}"
        ) from exc

    result = local_ns.get("result_df")
    if not isinstance(result, pd.DataFrame):
        raise RuntimeError(
            f"`result_df` is not a DataFrame (got {type(result).__name__}).\n\n"
            f"Generated code:\n{code}"
        )

    return result, code
