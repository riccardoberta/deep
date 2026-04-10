from __future__ import annotations

import base64
import json
import os
import re
import shutil
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

import dash
import dash_bootstrap_components as dbc
from dash import Dash, Input, Output, State, dash_table, dcc, html, no_update

try:  # pragma: no cover - cosmetic tweak
    from flask.cli import show_server_banner as _show_server_banner

    def _suppress_server_banner(*args, **kwargs) -> None:
        return None

    show_server_banner = _suppress_server_banner  # type: ignore
except Exception:  # pragma: no cover
    pass
from dotenv import load_dotenv
from openpyxl import load_workbook

from collaborations import CollaborationBuilder
from data_preparation import DataPreparation
from export import Exporter
from importer import Importer
from thresholds import compute_scores, load_thresholds

_THRESHOLDS = load_thresholds()

load_dotenv()


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y"}


@dataclass(frozen=True)
class AppSettings:
    input_folder: Path
    year_windows: str
    sleep_seconds: float
    fetch_scopus: bool
    fetch_unige: bool
    fetch_iris: bool
    data_dir: Path


def _load_settings() -> AppSettings:
    raw_sleep = os.getenv("SLEEP_SECONDS", "3.0")
    try:
        sleep_seconds = float(raw_sleep)
    except (TypeError, ValueError):
        sleep_seconds = 3.0

    input_folder = Path(os.getenv("INPUT_FOLDER", "./input")).expanduser()
    input_folder.mkdir(parents=True, exist_ok=True)
    data_dir = Path(os.getenv("DATA_DIR", "data")).expanduser()
    return AppSettings(
        input_folder=input_folder,
        year_windows=os.getenv("YEAR_WINDOWS", "15,10,5"),
        sleep_seconds=sleep_seconds,
        fetch_scopus=_env_bool("FETCH_SCOPUS", True),
        fetch_unige=_env_bool("FETCH_UNIGE", True),
        fetch_iris=_env_bool("FETCH_IRIS", True),
        data_dir=data_dir,
    )


ALLOWED_INPUT_SUFFIXES = {".xlsx", ".xlsm"}


def _list_input_files() -> List[Path]:
    folder = SETTINGS.input_folder
    folder.mkdir(parents=True, exist_ok=True)
    items = [
        path
        for path in folder.iterdir()
        if path.is_file() and path.suffix.lower() in ALLOWED_INPUT_SUFFIXES
    ]
    return sorted(items, key=lambda p: p.name.lower())


def _input_file_options() -> List[Dict[str, str]]:
    return [{"label": path.name, "value": str(path.resolve())} for path in _list_input_files()]


def _default_input_file() -> Optional[str]:
    options = _input_file_options()
    return options[0]["value"] if options else None


def _safe_uploaded_path(filename: str) -> Path:
    sanitized = re.sub(r"[^A-Za-z0-9._-]", "_", Path(filename).name)
    if not sanitized:
        sanitized = "input.xlsx"
    suffix = Path(sanitized).suffix.lower() or ".xlsx"
    if suffix not in ALLOWED_INPUT_SUFFIXES:
        raise ValueError("Unsupported file type. Upload XLSX/XLSM workbooks.")
    base = Path(sanitized).stem or "input"
    counter = 1
    target = SETTINGS.input_folder / f"{base}{suffix}"
    while target.exists():
        target = SETTINGS.input_folder / f"{base}_{counter}{suffix}"
        counter += 1
    return target


def _save_uploaded_input_file(filename: str, contents: str) -> Path:
    _, data = contents.split(",", 1)
    decoded = base64.b64decode(data)
    target = _safe_uploaded_path(filename)
    target.write_bytes(decoded)
    return target


def _delete_input_file(path_value: Optional[str]) -> None:
    if not path_value:
        return
    path = Path(path_value)
    try:
        if not path.exists():
            return
        if path.resolve().parent != SETTINGS.input_folder.resolve():
            return
        path.unlink()
    except Exception:
        pass


def _build_input_preview(path_value: Optional[str], limit: Optional[int] = None) -> tuple[List[Dict[str, str]], List[Dict[str, Any]], str]:
    if not path_value:
        return [], [], "No workbook selected."
    path = Path(path_value)
    if not path.exists():
        return [], [], "Selected workbook was not found on disk."
    try:
        workbook = load_workbook(filename=path, read_only=True, data_only=True)
        sheet = workbook.active
        rows = sheet.iter_rows(values_only=True)
        header: List[str] = []
        for row in rows:
            header = [str(value).strip() if value is not None else "" for value in row]
            if any(header):
                break
        if not header:
            workbook.close()
            return [], [], "Workbook is empty."
        data_rows: List[Dict[str, Any]] = []
        for row in rows:
            values = [row[idx] if idx < len(row) else "" for idx in range(len(header))]
            if not any(value is not None and str(value).strip() for value in values):
                continue
            record: Dict[str, Any] = {}
            for idx, column in enumerate(header):
                key = column or f"Column {idx + 1}"
                record[key] = values[idx]
            data_rows.append(record)
            if limit is not None and len(data_rows) >= limit:
                break
        workbook.close()
        columns: List[Dict[str, str]] = []
        seen_ids: set[str] = set()
        for idx, name in enumerate(header):
            display = name or f"Column {idx + 1}"
            col_id = re.sub(r"[^A-Za-z0-9_]+", "_", display) or f"column_{idx + 1}"
            if col_id in seen_ids:
                suffix = 1
                candidate = f"{col_id}_{suffix}"
                while candidate in seen_ids:
                    suffix += 1
                    candidate = f"{col_id}_{suffix}"
                col_id = candidate
            seen_ids.add(col_id)
            columns.append({"name": display, "id": col_id})

        normalized_data: List[Dict[str, Any]] = []
        for record in data_rows:
            normalized_record: Dict[str, Any] = {}
            for column in columns:
                display = column["name"]
                key = column["id"]
                normalized_record[key] = record.get(display, "")
            normalized_data.append(normalized_record)
        return columns, normalized_data, f"Previewing {path.name}"
    except Exception as exc:  # pragma: no cover - preview helper
        return [], [], f"Unable to read workbook: {exc}"


def _input_file_exists(path_value: Optional[str]) -> bool:
    return bool(path_value and Path(path_value).exists())


class ImportManager:
    """Run Importer in the background while keeping progress accessible to Dash callbacks."""

    def __init__(self, *, sleep_seconds: float, data_dir: Path) -> None:
        self.sleep_seconds = sleep_seconds
        self.data_dir = data_dir
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._status: str = "idle"
        self._logs: List[str] = []
        self._error: Optional[str] = None
        self._result: Optional[Dict[str, Any]] = None
        self._started_at: Optional[str] = None
        self._finished_at: Optional[str] = None
        self._stop_event = threading.Event()

    def start(
        self,
        *,
        input_workbook: str,
        year_windows: Iterable[int],
        fetch_scopus: bool,
        fetch_unige: bool,
        fetch_iris: bool,
    ) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                raise RuntimeError("An import is already running.")
            self._status = "running"
            self._logs = []
            self._error = None
            self._result = None
            self._started_at = datetime.now(UTC).isoformat()
            self._finished_at = None
            self._stop_event = threading.Event()

        thread = threading.Thread(
            target=self._run_import,
            args=(input_workbook, tuple(year_windows), fetch_scopus, fetch_unige, fetch_iris),
            daemon=True,
        )
        self._thread = thread
        thread.start()

    def stop(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive() and not self._stop_event.is_set():
                self._stop_event.set()
                timestamp = datetime.now(UTC).strftime("%H:%M:%S")
                self._logs.append(f"[{timestamp}] ⏹️ Stop requested by user.")

    def get_state(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "status": self._status,
                "logs": list(self._logs),
                "error": self._error,
                "result": self._result,
                "started_at": self._started_at,
                "finished_at": self._finished_at,
            }

    def _run_import(
        self,
        input_workbook: str,
        year_windows: Tuple[int, ...],
        fetch_scopus: bool,
        fetch_unige: bool,
        fetch_iris: bool,
    ) -> None:
        def logger(message: str) -> None:
            timestamp = datetime.now(UTC).strftime("%H:%M:%S")
            with self._lock:
                self._logs.append(f"[{timestamp}] {message}")

        importer = Importer(
            input_workbook=input_workbook,
            year_windows=year_windows,
            sleep_seconds=self.sleep_seconds,
            fetch_scopus=fetch_scopus,
            fetch_unige=fetch_unige,
            fetch_iris=fetch_iris,
            data_dir=self.data_dir,
            logger=logger,
            should_stop=self._stop_event.is_set,
        )

        try:
            run_dir, payloads, metadata = importer.run()
            if metadata:
                _perform_elaborations(payloads, run_dir, metadata, logger)
            result = _build_run_store(run_dir, payloads, metadata)
            with self._lock:
                self._result = result
        except Exception as exc:  # pragma: no cover - surfaced in UI
            with self._lock:
                self._error = str(exc)
        finally:
            with self._lock:
                if self._stop_event.is_set() and not self._error:
                    self._status = "cancelled"
                else:
                    self._status = "failed" if self._error else "completed"
                self._finished_at = datetime.now(UTC).isoformat()


SETTINGS = _load_settings()
IMPORT_MANAGER = ImportManager(sleep_seconds=SETTINGS.sleep_seconds, data_dir=SETTINGS.data_dir)
DATA_PREPARER = DataPreparation()
EXPORTER = Exporter()


def _perform_elaborations(
    payloads: List[Dict[str, Any]],
    run_dir: Path,
    metadata: Dict[str, Any],
    logger: Callable[[str], None],
) -> None:
    input_file = metadata.get("input_file") or (Path(_default_input_file()).name if _default_input_file() else None)
    outputs_written = False
    if input_file:
        input_path = next((path for path in SETTINGS.input_folder.glob("*") if path.name == input_file), None)
        workbook_path = str(input_path) if input_path else _default_input_file()
        try:
            summary_path = DATA_PREPARER.prepare(payloads, run_dir, workbook_path)
            logger(f"📘 Results workbook saved to {summary_path}")
            metadata["summary_path"] = str(summary_path)
            outputs_written = True
        except Exception as exc:  # pragma: no cover
            logger(f"⚠️ Results workbook failed: {exc}")
    else:
        logger("⚠️ Skipping results workbook: unable to determine input workbook.")

    windows = metadata.get("year_windows") or []
    try:
        builder = CollaborationBuilder(windows, logger=logger)
        builder.build(payloads, run_dir)
        logger("🔗 Collaboration graph generated.")
    except Exception as exc:  # pragma: no cover
        logger(f"⚠️ Collaboration graph failed: {exc}")

    try:
        markdown_dir = EXPORTER.export(payloads, run_dir)
        logger(f"📄 Export completed: {markdown_dir}")
        outputs_written = True
    except Exception as exc:  # pragma: no cover
        logger(f"⚠️ Export failed: {exc}")

    if outputs_written:
        metadata["last_outputs_updated_at"] = datetime.now(UTC).isoformat()
        _write_metadata(run_dir, metadata)


def _parse_year_windows(value: str) -> List[int]:
    parts = [part.strip() for part in (value or "").split(",") if part.strip()]
    if not parts:
        raise ValueError("Year windows cannot be empty.")
    try:
        return [int(part) for part in parts]
    except ValueError as exc:
        raise ValueError("Year windows must contain integers separated by commas.") from exc


def _latest_run_dir(base_dir: Path) -> Optional[Path]:
    runs = _list_run_directories(base_dir)
    return runs[0] if runs else None


def _list_run_directories(base_dir: Path) -> List[Path]:
    if not base_dir.is_dir():
        return []
    pattern = re.compile(r"^(\d{4}_\d{2}_\d{2})_(\d+)$")
    candidates: List[Tuple[str, int, Path]] = []
    for child in base_dir.iterdir():
        if child.is_dir():
            match = pattern.match(child.name)
            if match:
                candidates.append((match.group(1), int(match.group(2)), child))
    candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [item[2] for item in candidates]


def _load_payloads_from_dir(run_dir: Path) -> List[Dict[str, Any]]:
    source_dir = run_dir / "source"
    if not source_dir.is_dir():
        return []
    payloads: List[Dict[str, Any]] = []
    for path in sorted(source_dir.glob("*.json")):
        try:
            payloads.append(json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            continue
    return payloads


def _load_metadata(run_dir: Path) -> Dict[str, Any]:
    path = run_dir / "metadata.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _build_run_store(run_dir: Path | None, payloads: List[Dict[str, Any]], metadata: Dict[str, Any]) -> Dict[str, Any]:
    sorted_payloads = sorted(
        payloads,
        key=lambda item: (
            str(item.get("surname", "")).lower(),
            str(item.get("name", "")).lower(),
        ),
    )
    return {
        "run_dir": str(run_dir) if run_dir else None,
        "payloads": sorted_payloads,
        "metadata": metadata,
    }


def _load_run_store_for_value(value: Optional[str]) -> Dict[str, Any]:
    if not value:
        return {"run_dir": None, "payloads": [], "metadata": {}}
    path = Path(value)
    if not path.exists():
        return {"run_dir": None, "payloads": [], "metadata": {}}
    payloads = _load_payloads_from_dir(path)
    metadata = _load_metadata(path)
    return _build_run_store(path, payloads, metadata)


def _run_dropdown_options() -> List[Dict[str, str]]:
    options = []
    for path in _list_run_directories(SETTINGS.data_dir):
        meta_path = path / "metadata.json"
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
        except Exception:
            meta = {}
        input_file = Path(meta.get("input_file", "")).stem or path.name
        count = meta.get("source_count", "?")
        # path.name is like "2026_04_10_3" → "2026/04/10 #3"
        parts = path.name.split("_")
        if len(parts) == 4:
            date_label = f"{parts[0]}/{parts[1]}/{parts[2]} #{parts[3]}"
        else:
            date_label = path.name
        label = f"{date_label} – {input_file} ({count} membri)"
        options.append({"label": label, "value": str(path.resolve())})
    return options


def _sync_run_dropdown(preferred: Optional[str]) -> Tuple[List[Dict[str, str]], Optional[str]]:
    options = _run_dropdown_options()
    values = {option["value"] for option in options}
    if not options:
        return options, None
    if preferred not in values:
        preferred = options[0]["value"]
    return options, preferred


def _delete_run_directory(value: Optional[str]) -> bool:
    if not value:
        return False
    path = Path(value)
    try:
        if not path.exists():
            return False
        if path.resolve().parent != SETTINGS.data_dir.resolve():
            return False
        shutil.rmtree(path)
        return True
    except Exception:
        return False


def _regenerate_run_outputs(value: Optional[str]) -> str:
    if not value:
        return "⚠️ Select a run before regenerating outputs."
    run_dir = Path(value)
    if not run_dir.exists():
        return "⚠️ Run directory not found."
    payloads = _load_payloads_from_dir(run_dir)
    metadata = _load_metadata(run_dir)
    if not payloads:
        return "⚠️ Run has no payloads to process."

    messages: List[str] = []

    def _collector(message: str) -> None:
        messages.append(message)

    _perform_elaborations(payloads, run_dir, metadata or {}, _collector)
    return messages[-1] if messages else "✅ Outputs regenerated."


def _write_metadata(run_dir: Path, metadata: Dict[str, Any]) -> None:
    metadata_path = run_dir / "metadata.json"
    metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")


_TREE_KEY_STYLE   = {"color": "#0550ae", "fontWeight": "500", "fontFamily": "monospace", "fontSize": "0.85rem"}
_TREE_INDENT      = {"paddingLeft": "1.1rem", "borderLeft": "2px solid #e9ecef", "marginLeft": "2px", "marginTop": "2px"}
_TREE_ITEM_STYLE  = {"marginBottom": "3px", "lineHeight": "1.5"}
_TREE_SUMMARY_STYLE = {"cursor": "pointer", "userSelect": "none", "marginBottom": "2px", "paddingLeft": "2px"}
# Dict keys that are collapsed by default even at level 1
_TREE_COLLAPSED_KEYS = {"teaching", "scores", "career", "location", "scopus_metrics", "iris_products"}


def _tree_leaf_value(value: Any) -> html.Span:
    if value is None:
        return html.Span("null", style={"color": "#6c757d", "fontFamily": "monospace", "fontSize": "0.85rem"})
    if isinstance(value, bool):
        return html.Span(str(value).lower(), style={"color": "#d63384", "fontFamily": "monospace", "fontSize": "0.85rem"})
    if isinstance(value, (int, float)):
        return html.Span(str(value), style={"color": "#b45309", "fontFamily": "monospace", "fontSize": "0.85rem"})
    text = str(value)
    display = text if len(text) <= 120 else f'{text[:120]}…'
    return html.Span(display, style={"color": "#198754", "fontFamily": "monospace", "fontSize": "0.85rem", "wordBreak": "break-all"})


def _build_json_tree(value: Any, label: str = "value", level: int = 0) -> html.Div | html.Details:
    # ── Dict ──────────────────────────────────────────────────────────────────
    if isinstance(value, dict):
        if not value:
            return html.Div([
                html.Span(label, style=_TREE_KEY_STYLE),
                html.Span(": {}", style={"color": "#6c757d", "fontFamily": "monospace", "fontSize": "0.85rem"}),
            ], style=_TREE_ITEM_STYLE)

        children = [_build_json_tree(val, str(key), level + 1) for key, val in value.items()]
        inner = html.Div(children, style=_TREE_INDENT)

        # Root level: skip wrapper, render children directly
        if level == 0:
            return html.Div(children)

        return html.Details(
            [
                html.Summary(html.Span(label, style=_TREE_KEY_STYLE), style=_TREE_SUMMARY_STYLE),
                inner,
            ],
            open=level <= 1 and label not in _TREE_COLLAPSED_KEYS,
            style=_TREE_ITEM_STYLE,
        )

    # ── List ──────────────────────────────────────────────────────────────────
    if isinstance(value, list):
        if not value:
            return html.Div([
                html.Span(label, style=_TREE_KEY_STYLE),
                html.Span(": []", style={"color": "#6c757d", "fontFamily": "monospace", "fontSize": "0.85rem"}),
            ], style=_TREE_ITEM_STYLE)

        children = [_build_json_tree(item, f"[{i}]", level + 1) for i, item in enumerate(value)]
        inner = html.Div(children, style=_TREE_INDENT)

        return html.Details(
            [
                html.Summary(html.Span(label, style=_TREE_KEY_STYLE), style=_TREE_SUMMARY_STYLE),
                inner,
            ],
            open=False,
            style=_TREE_ITEM_STYLE,
        )

    # ── Leaf ──────────────────────────────────────────────────────────────────
    return html.Div(
        [
            html.Span(label, style=_TREE_KEY_STYLE),
            html.Span(": ", style={"color": "#6c757d", "fontFamily": "monospace", "fontSize": "0.85rem"}),
            _tree_leaf_value(value),
        ],
        style=_TREE_ITEM_STYLE,
    )


def _score_color(score: Optional[float]) -> str:
    if score is None:   return "secondary"
    if score >= 1.2:    return "success"
    if score >= 0.8:    return "primary"
    if score >= 0.4:    return "warning"
    return "danger"


def _ratio_color(ratio: Optional[float]) -> str:
    if ratio is None:   return "text-muted"
    if ratio >= 1.0:    return "text-success fw-semibold"
    if ratio >= 0.7:    return "text-warning fw-semibold"
    return "text-danger fw-semibold"


def _ratio_text(ratio: Optional[float]) -> str:
    return f"{ratio:.2f}" if ratio is not None else "N/D"


def _level_row(row_label: str, level: Dict[str, Any]) -> html.Tr:
    years     = level.get("years")
    value     = level.get("value")
    threshold = level.get("threshold")
    ratio     = level.get("ratio")

    label_text = f"{row_label} ({years}a)" if years else row_label

    if value is None or threshold is None:
        formula = html.Span("N/D", className="text-muted small")
    else:
        ratio_str = f"{ratio:.2f}" if ratio is not None else "—"
        formula = html.Span(
            [
                html.Span(f"{value}", className="fw-semibold"),
                html.Span(" / ", className="text-muted"),
                html.Span(f"{threshold}", className="fw-semibold"),
                html.Span(" = ", className="text-muted"),
                html.Span(ratio_str, className=f"fw-bold {_ratio_color(ratio)}"),
            ]
        )

    return html.Tr([
        html.Td(label_text, className="text-muted small pe-3", style={"whiteSpace": "nowrap"}),
        html.Td(formula, className="small text-end"),
    ])


def _indicator_card(label: str, block: Dict[str, Any]) -> dbc.Col:
    score = block.get("score")
    score_text = f"{score:.1f}" if score is not None else "N/D"
    color = _score_color(score)

    ratio_rows = [
        _level_row("Associate Prof.",  block.get("ii_fascia")   or {}),
        _level_row("Full Prof.",       block.get("i_fascia")    or {}),
        _level_row("Commissioner",     block.get("commissario") or {}),
    ]

    return dbc.Col(
        dbc.Card(
            [
                dbc.CardHeader(
                    dbc.Row(
                        [
                            dbc.Col(html.Span(label, className="fw-bold small"), className="align-self-center"),
                            dbc.Col(
                                dbc.Badge(score_text, color=color, className="float-end", style={"fontSize": "0.875rem"}),
                                className="text-end",
                            ),
                        ],
                        align="center",
                    ),
                    className="py-2 px-3",
                ),
                dbc.CardBody(
                    html.Table(
                        html.Tbody(ratio_rows),
                        className="w-100 mb-0",
                    ),
                    className="py-2 px-3",
                ),
            ],
            color=color,
            outline=True,
        ),
        md=4,
    )


def _member_detail_component(payload: Dict[str, Any]) -> html.Div:
    ssd = payload.get("ssd")
    metrics = payload.get("scopus_metrics", [])

    # Always recompute from live metrics so the display is never stale
    # even when the payload was generated with an older scores format.
    scores = compute_scores(ssd, metrics, _THRESHOLDS)

    scores_panel = dbc.Card(
        dbc.CardBody(
            [
                html.H6("Bibliometric Indicators", className="mb-2"),
                html.Div(
                    f"SSD: {ssd}" if ssd else "SSD non disponibile",
                    className="text-muted small mb-2",
                ),
                dbc.Row(
                    [
                        _indicator_card("Articles",  scores["articles"]),
                        _indicator_card("Citations", scores["citations"]),
                        _indicator_card("H-index",   scores["hindex"]),
                    ],
                    className="g-2",
                ),
                html.Div(
                    [
                        dbc.Badge("0.0 below Associate Prof.", color="danger",    className="me-1 mt-2"),
                        dbc.Badge("0.4 Associate Prof.",      color="warning",   className="me-1 mt-2"),
                        dbc.Badge("0.8 Full Prof.",           color="primary",   className="me-1 mt-2"),
                        dbc.Badge("1.2 Commissioner",         color="success",   className="me-1 mt-2"),
                        html.Span(" · ratio: ", className="text-muted small ms-1 me-1"),
                        html.Span("≥ 1.0", className="text-success small fw-semibold me-1"),
                        html.Span("threshold met,", className="text-muted small me-1"),
                        html.Span("0.7–1.0", className="text-warning small fw-semibold me-1"),
                        html.Span("close,", className="text-muted small me-1"),
                        html.Span("< 0.7", className="text-danger small fw-semibold me-1"),
                        html.Span("below", className="text-muted small"),
                    ],
                    className="mt-2",
                ),
            ]
        ),
        className="mb-3",
    )

    raw_panel = dbc.Card(
        dbc.CardBody(
            [
                html.Div(
                    _build_json_tree(payload),
                    style={"fontFamily": "monospace", "fontSize": "0.85rem", "lineHeight": "1.6"},
                ),
            ]
        ),
        className="mb-3",
    )

    return html.Div([scores_panel, raw_panel])

RUN_OPTIONS_INITIAL = _run_dropdown_options()
DEFAULT_RUN_SELECTION = RUN_OPTIONS_INITIAL[0]["value"] if RUN_OPTIONS_INITIAL else None
RUN_STORE_INITIAL = _load_run_store_for_value(DEFAULT_RUN_SELECTION)
DEFAULT_RUN_MESSAGE = "Select a run to explore or manage its outputs."
DEFAULT_INPUT_FILE = _default_input_file()
DEFAULT_PREVIEW_COLUMNS, DEFAULT_PREVIEW_DATA, DEFAULT_PREVIEW_MESSAGE = _build_input_preview(DEFAULT_INPUT_FILE)

app = Dash(__name__, external_stylesheets=[dbc.themes.BOOTSTRAP])
app.title = "DEEP"
app._favicon = "logo.png"


def _build_import_tab() -> dbc.Container:
    left_panel = dbc.Card(
        dbc.CardBody(
            [
                # ── Input Selection ──────────────────────────────────────
                html.H5("Input Selection", className="mb-2"),
                dbc.Row(
                    [
                        dbc.Col(
                            dcc.Dropdown(
                                id="input-file-dropdown",
                                options=_input_file_options(),
                                value=DEFAULT_INPUT_FILE,
                                placeholder="Select a workbook from the input folder",
                                clearable=False,
                            ),
                        ),
                        dbc.Col(
                            dcc.Upload(
                                id="upload-input-file",
                                children=dbc.Button("Upload", color="secondary", className="w-100"),
                                multiple=False,
                                accept=".xlsx,.xlsm",
                            ),
                            width="auto",
                        ),
                        dbc.Col(
                            dbc.Button(
                                "Remove",
                                id="delete-input-btn",
                                color="danger",
                                outline=True,
                                className="w-100",
                                disabled=DEFAULT_INPUT_FILE is None,
                            ),
                            width="auto",
                        ),
                    ],
                    className="g-2 align-items-center",
                ),
                html.Hr(className="my-3"),
                # ── Import Settings ──────────────────────────────────────
                html.H5("Import Settings", className="mb-2"),
                dbc.Row(
                    [
                        dbc.Col(dbc.Label("Time windows", className="mb-0"), width="auto", className="align-self-center"),
                        dbc.Col(dbc.Input(id="year-windows", type="text", value=SETTINGS.year_windows), md=3),
                        dbc.Col(dbc.Label("Data sources", className="mb-0"), width="auto", className="align-self-center ms-3"),
                        dbc.Col(
                            dbc.Checklist(
                                id="fetch-options",
                                options=[
                                    {"label": "UNIGE",  "value": "unige"},
                                    {"label": "IRIS",   "value": "iris"},
                                    {"label": "Scopus", "value": "scopus"},
                                ],
                                value=[
                                    opt for opt, flag in {
                                        "scopus": SETTINGS.fetch_scopus,
                                        "unige":  SETTINGS.fetch_unige,
                                        "iris":   SETTINGS.fetch_iris,
                                    }.items() if flag
                                ],
                                switch=True,
                                inline=True,
                            ),
                        ),
                    ],
                    className="g-2 align-items-center",
                ),
                dbc.Row(
                    [
                        dbc.Col(dbc.Button("Start Import", id="start-import", color="primary"), width="auto"),
                        dbc.Col(dbc.Button("Stop Import", id="stop-import", color="danger", outline=True, disabled=True), width="auto"),
                        dbc.Col(html.Div(id="import-status-text", className="text-muted fw-semibold align-self-center")),
                    ],
                    className="g-2 align-items-center mt-2",
                ),
            ]
        ),
        className="shadow-sm",
    )

    right_panel = dbc.Card(
        dbc.CardBody(
            [
                html.H5("Import Log", className="mb-2"),
                html.Pre(
                    id="import-log",
                    style={
                        "flex": "1",
                        "minHeight": "0",
                        "overflowY": "auto",
                        "backgroundColor": "#f8f9fa",
                        "border": "1px solid #dee2e6",
                        "borderRadius": "4px",
                        "padding": "10px 12px",
                        "fontSize": "0.68rem",
                        "fontFamily": "monospace",
                        "lineHeight": "1.6",
                        "color": "#212529",
                        "whiteSpace": "pre-wrap",
                        "wordBreak": "break-word",
                        "margin": "0",
                    },
                ),
            ],
            style={"height": "100%", "display": "flex", "flexDirection": "column"},
        ),
        className="shadow-sm h-100",
    )

    preview_panel = dbc.Card(
        dbc.CardBody(
            [
                html.H5("Input Preview", className="mb-2"),
                dbc.Alert("", id="input-preview-message", color="light", className="mb-2 py-1 small"),
                dash_table.DataTable(
                    id="input-preview-table",
                    columns=DEFAULT_PREVIEW_COLUMNS,
                    data=DEFAULT_PREVIEW_DATA,
                    style_as_list_view=True,
                    style_table={"maxHeight": "340px", "overflowY": "auto", "overflowX": "auto"},
                    style_header={
                        "backgroundColor": "#f8f9fa",
                        "fontWeight": "600",
                        "fontSize": 13,
                        "color": "#495057",
                        "borderBottom": "2px solid #dee2e6",
                        "borderTop": "none",
                        "padding": "8px 10px",
                    },
                    style_cell={
                        "textAlign": "left",
                        "padding": "7px 10px",
                        "fontSize": 13,
                        "color": "#212529",
                        "borderBottom": "1px solid #f0f0f0",
                        "fontFamily": "inherit",
                    },
                    style_data_conditional=[
                        {"if": {"state": "active"}, "backgroundColor": "rgba(13,110,253,0.06)", "border": "none"},
                    ],
                    page_action="none",
                    cell_selectable=False,
                ),
            ]
        ),
        className="shadow-sm mt-3",
    )

    return dbc.Container(
        [
            dbc.Row(
                [
                    dbc.Col(left_panel,  md=7),
                    dbc.Col(right_panel, md=5),
                ],
                className="g-3 pt-3",
            ),
            preview_panel,
        ],
        fluid=True,
        className="px-3 pb-3",
    )


def _select_data_bar(dropdown_options: List[Dict[str, str]]) -> dbc.Card:
    return dbc.Card(
        dbc.CardBody(
            [
                dcc.Download(id="download-summary"),
                # Hidden elements kept for callback compatibility
                html.Div(id="current-run-label", style={"display": "none"}),
                html.Div(id="run-action-message", style={"display": "none"}),
                dbc.Row(
                    [
                        dbc.Col(
                            html.H5("Select data", className="mb-0"),
                            width="auto",
                            className="align-self-center",
                        ),
                        dbc.Col(
                            dcc.Dropdown(
                                id="run-dropdown",
                                options=dropdown_options,
                                value=dropdown_options[0]["value"] if dropdown_options else None,
                                placeholder="Select a run to explore",
                                clearable=False,
                            ),
                        ),
                        dbc.Col(
                            dbc.Button("Download", id="download-summary-btn", color="success", size="sm"),
                            width="auto",
                        ),
                        dbc.Col(
                            dbc.Button("Rebuild", id="regen-run-btn", color="secondary", size="sm"),
                            width="auto",
                        ),
                        dbc.Col(
                            dbc.Button("Delete", id="delete-run-btn", color="danger", outline=True, size="sm"),
                            width="auto",
                        ),
                    ],
                    align="center",
                    className="g-2",
                ),
            ],
            className="py-2",
        ),
        className="shadow-sm mb-0",
    )


# Base conditional styles for the member table (no row selected).
# Re-used by callbacks to reset or update the highlight.
_TABLE_STYLE_BASE: List[Dict[str, Any]] = [
    {"if": {"row_index": "odd"}, "backgroundColor": "#fafafa"},
    # Suppress the default active-cell blue border so only the row highlight is visible.
    {"if": {"state": "active"}, "backgroundColor": "rgba(0,0,0,0)", "border": "1px solid transparent"},
]


def _table_style_with_row(row_index: int) -> List[Dict[str, Any]]:
    return _TABLE_STYLE_BASE + [
        {"if": {"row_index": row_index}, "backgroundColor": "rgba(13,110,253,0.10)", "borderTop": "1px solid rgba(13,110,253,0.25)", "borderBottom": "1px solid rgba(13,110,253,0.25)"},
    ]


def _member_table_card() -> dbc.Card:
    return dbc.Card(
        dbc.CardBody(
            [
                html.H5("Members", className="mb-3"),
                dash_table.DataTable(
                    id="member-table",
                    columns=[
                        {"name": "",        "id": "inspect"},
                        {"name": "Surname", "id": "surname"},
                        {"name": "Name",    "id": "name"},
                        {"name": "SSD",     "id": "ssd"},
                    ],
                    data=[],
                    style_as_list_view=True,
                    style_table={"overflowX": "auto"},
                    style_header={
                        "backgroundColor": "#f8f9fa",
                        "fontWeight": "600",
                        "fontSize": 13,
                        "color": "#495057",
                        "borderBottom": "2px solid #dee2e6",
                        "borderTop": "none",
                        "padding": "8px 10px",
                    },
                    style_cell={
                        "textAlign": "left",
                        "padding": "8px 10px",
                        "fontSize": 13,
                        "color": "#212529",
                        "borderBottom": "1px solid #f0f0f0",
                        "fontFamily": "inherit",
                        "cursor": "default",
                    },
                    style_cell_conditional=[
                        {"if": {"column_id": "inspect"}, "width": "36px", "textAlign": "center", "cursor": "pointer", "color": "#6c757d"},
                        {"if": {"column_id": "ssd"},     "maxWidth": "160px", "overflow": "hidden", "textOverflow": "ellipsis", "color": "#6c757d", "fontSize": 10},
                    ],
                    style_data_conditional=_TABLE_STYLE_BASE,
                    sort_action="native",
                    sort_mode="single",
                    page_action="none",
                    row_selectable=False,
                    cell_selectable=True,
                    tooltip_data=[],
                    tooltip_duration=None,
                ),
            ]
        ),
        className="shadow-sm h-100",
    )


def _member_detail_card() -> dbc.Card:
    return dbc.Card(
        dbc.CardBody(
            [
                html.H5("Member details", className="mb-3"),
                dcc.Loading(
                    html.Div("Select a member using the magnifier icon.", id="member-detail"),
                    id="member-detail-loading",
                    type="circle",
                    color="#0d6efd",
                    delay_show=0,
                ),
            ],
            style={"overflowY": "auto"},
        ),
        className="shadow-sm h-100",
    )


def _build_exploring_tab() -> dbc.Container:
    return dbc.Container(
        [
            dbc.Row(
                dbc.Col(_select_data_bar(_run_dropdown_options()), md=12),
                className="g-0 pt-3 pb-2",
            ),
            dbc.Row(
                [
                    dbc.Col(_member_table_card(),  md=4),
                    dbc.Col(_member_detail_card(), md=8),
                ],
                className="g-3 align-items-stretch",
            ),
        ],
        fluid=True,
        className="px-3",
    )





header = html.Div(
    dbc.Row(
        [
            dbc.Col(html.Img(src="/assets/logo.png", style={"height": "64px"}), width="auto"),
            dbc.Col(
                [
                    html.H1("DEEP", className="mb-1"),
                    html.Div("DITEN Evaluation and Evidence Platform", className="text-muted", style={"fontSize": "1.125rem"}),
                ],
                width="auto",
            ),
        ],
        align="center",
        className="g-3",
    ),
    className="mb-4",
)

app.layout = dbc.Container(
    [
        header,
        dbc.Card(
            dbc.CardBody(
                dcc.Tabs(
                    id="main-tabs",
                    value="tab-import",
                    children=[
                        dcc.Tab(label="Importing", value="tab-import", children=_build_import_tab()),
                        dcc.Tab(label="Exploring", value="tab-exploring", children=_build_exploring_tab()),
                    ],
                )
            ),
            className="shadow-sm",
        ),
        dcc.Store(id="run-store", data=RUN_STORE_INITIAL),
        dcc.Interval(id="import-poll-interval", interval=2_000, disabled=True),
    ],
    fluid=True,
    className="py-4",
)


def _format_import_status(state: Dict[str, Any]) -> str:
    status = state.get("status")
    if status == "running":
        return "⏳ Import in progress..."
    if status == "completed":
        return "✅ Import completed."
    if status == "failed":
        return f"⚠️ Import failed: {state.get('error')}"
    if status == "cancelled":
        return "⏹️ Import cancelled."
    return "Ready."


def _format_run_meta(run_data: Dict[str, Any]) -> html.Div:
    metadata = run_data.get("metadata") or {}
    if not run_data.get("run_dir"):
        return html.Div("No run selected.", className="text-muted")
    items = []
    if metadata.get("year_windows"):
        items.append(html.Li(f"Year windows: {metadata['year_windows']}"))
    sources = [
        name
        for name, enabled in {
            "Scopus": metadata.get("fetch_scopus"),
            "UNIGE": metadata.get("fetch_unige"),
            "IRIS": metadata.get("fetch_iris"),
        }.items()
        if enabled
    ]
    if sources:
        items.append(html.Li("Sources: " + ", ".join(sources)))
    if metadata.get("created_at"):
        items.append(html.Li(f"Created at: {metadata['created_at']}"))
    if metadata.get("source_count") is not None:
        items.append(html.Li(f"Members: {metadata['source_count']}"))
    return html.Ul(items) if items else html.Div("No metadata available.", className="text-muted")


@app.callback(
    Output("input-file-dropdown", "options"),
    Output("input-file-dropdown", "value"),
    Output("input-preview-table", "columns"),
    Output("input-preview-table", "data"),
    Output("delete-input-btn", "disabled"),
    Output("input-preview-message", "children"),
    Output("upload-input-file", "contents"),
    Input("input-file-dropdown", "value"),
    Input("upload-input-file", "contents"),
    Input("delete-input-btn", "n_clicks"),
    State("upload-input-file", "filename"),
)
def manage_input_files(
    selected_value: Optional[str],
    upload_contents: Optional[str],
    delete_clicks: Optional[int],
    upload_filename: Optional[str],
):
    triggered = dash.ctx.triggered_id
    new_value = selected_value
    message_override: Optional[str] = None
    reset_upload = dash.no_update

    if triggered == "upload-input-file" and upload_contents and upload_filename:
        try:
            saved = _save_uploaded_input_file(upload_filename, upload_contents)
            new_value = str(saved.resolve())
            message_override = f"Uploaded {saved.name}"
        except ValueError as exc:
            message_override = f"⚠️ {exc}"
        except Exception as exc:  # pragma: no cover
            message_override = f"⚠️ Upload failed: {exc}"
        reset_upload = None
    elif triggered == "delete-input-btn" and delete_clicks and new_value:
        _delete_input_file(new_value)
        message_override = "Selected workbook deleted."
        new_value = None

    options = _input_file_options()
    option_values = {option["value"] for option in options}
    if not option_values:
        new_value = None
    elif new_value not in option_values:
        new_value = options[0]["value"]

    columns, data, preview_message = _build_input_preview(new_value)
    message = message_override or preview_message
    delete_disabled = new_value is None

    return options, new_value, columns, data, delete_disabled, message, reset_upload


@app.callback(
    Output("import-status-text", "children"),
    Output("import-log", "children"),
    Output("run-store", "data"),
    Output("import-poll-interval", "disabled"),
    Output("start-import", "disabled"),
    Output("fetch-options", "disabled"),
    Output("stop-import", "disabled"),
    Output("run-dropdown", "options"),
    Output("run-dropdown", "value"),
    Output("run-action-message", "children"),
    Input("start-import", "n_clicks"),
    Input("stop-import", "n_clicks"),
    Input("import-poll-interval", "n_intervals"),
    Input("run-dropdown", "value"),
    Input("delete-run-btn", "n_clicks"),
    Input("regen-run-btn", "n_clicks"),
    State("input-file-dropdown", "value"),
    State("year-windows", "value"),
    State("fetch-options", "value"),
    State("run-store", "data"),
    prevent_initial_call=True,
)
def handle_run_actions(
    start_clicks: int,
    stop_clicks: int,
    _poll_count: int,
    selected_run_dir: Optional[str],
    delete_clicks: Optional[int],
    regen_clicks: Optional[int],
    selected_input_file: Optional[str],
    year_windows_value: str,
    fetch_options: List[str],
    run_store: Dict[str, Any],
):
    triggered = dash.ctx.triggered_id
    current_store = dict(run_store or {})
    dropdown_options = no_update
    dropdown_value = no_update
    action_message = no_update

    if triggered == "start-import":
        try:
            windows = _parse_year_windows(year_windows_value)
        except ValueError as exc:
            has_file_now = _input_file_exists(selected_input_file)
            return (
                f"⚠️ {exc}",
                no_update,
                current_store,
                True,
                not has_file_now,
                False,
                True,
                dropdown_options,
                dropdown_value,
                DEFAULT_RUN_MESSAGE if action_message is no_update else action_message,
            )
        if not selected_input_file:
            return (
                "⚠️ Select an input workbook before importing.",
                no_update,
                current_store,
                True,
                True,
                False,
                True,
                dropdown_options,
                dropdown_value,
                DEFAULT_RUN_MESSAGE if action_message is no_update else action_message,
            )

        fetch_scopus = "scopus" in (fetch_options or [])
        fetch_unige = "unige" in (fetch_options or [])
        fetch_iris = "iris" in (fetch_options or [])
        try:
            IMPORT_MANAGER.start(
                input_workbook=selected_input_file,
                year_windows=windows,
                fetch_scopus=fetch_scopus,
                fetch_unige=fetch_unige,
                fetch_iris=fetch_iris,
            )
        except RuntimeError as exc:
            state = IMPORT_MANAGER.get_state()
            is_running = state.get("status") == "running"
            has_file_now = _input_file_exists(selected_input_file)
            return (
                f"⚠️ {exc}",
                "\n".join(state.get("logs", [])),
                current_store,
                state.get("status") != "running",
                is_running or not has_file_now,
                is_running,
                not is_running,
                dropdown_options,
                dropdown_value,
                DEFAULT_RUN_MESSAGE if action_message is no_update else action_message,
            )

    if triggered == "run-dropdown":
        dropdown_options = _run_dropdown_options()
        values = {option["value"] for option in dropdown_options}
        dropdown_value = selected_run_dir if selected_run_dir in values else dropdown_options[0]["value"] if dropdown_options else None
        current_store = _load_run_store_for_value(dropdown_value)
        action_message = (
            f"📂 Viewing run {Path(dropdown_value).name}" if dropdown_value else DEFAULT_RUN_MESSAGE
        )
    if triggered == "stop-import":
        IMPORT_MANAGER.stop()
    elif triggered == "delete-run-btn":
        if selected_run_dir and _delete_run_directory(selected_run_dir):
            action_message = f"🗑️ Deleted run {Path(selected_run_dir).name}."
        else:
            action_message = "⚠️ Unable to delete run."
        dropdown_options, dropdown_value = _sync_run_dropdown(None)
        current_store = _load_run_store_for_value(dropdown_value)
    elif triggered == "regen-run-btn":
        action_message = _regenerate_run_outputs(selected_run_dir)
        dropdown_options, dropdown_value = _sync_run_dropdown(selected_run_dir)
        current_store = _load_run_store_for_value(dropdown_value)

    state = IMPORT_MANAGER.get_state()
    interval_disabled = state.get("status") != "running"
    run_data = current_store
    result = state.get("result")
    if result and state.get("status") in {"completed", "cancelled"}:
        run_data = result
        if triggered == "start-import":
            dropdown_options, dropdown_value = _sync_run_dropdown(run_data.get("run_dir"))
            action_message = "✅ Import completed and outputs generated."

    is_running = state.get("status") == "running"
    has_file = _input_file_exists(selected_input_file)
    start_disabled = is_running or not has_file

    final_message = (
        action_message
        if action_message is not no_update
        else (DEFAULT_RUN_MESSAGE if dropdown_value in (None, no_update) else no_update)
    )

    return (
        _format_import_status(state),
        "\n".join(state.get("logs") or []),
        run_data,
        interval_disabled,
        start_disabled,
        is_running,
        not is_running,
        dropdown_options,
        dropdown_value,
        final_message,
    )


@app.callback(
    Output("current-run-label", "children"),
    Output("member-table", "data"),
    Output("member-table", "style_data_conditional"),
    Input("run-store", "data"),
)
def update_run_view(run_store: Dict[str, Any]):
    run_data = run_store or {}
    payloads = run_data.get("payloads") or []
    rows: List[Dict[str, Any]] = []
    for payload in payloads:
        rows.append(
            {
                "inspect": "🔍",
                "surname": payload.get("surname", ""),
                "name": payload.get("name", ""),
                "ssd": payload.get("ssd", ""),
                "role": payload.get("grade") or payload.get("role", ""),
            }
        )
    metadata = run_data.get("metadata") or {}
    input_file = metadata.get("input_file", "")
    count = metadata.get("source_count", len(payloads))
    label = f"{input_file}  –  {count} membri" if input_file else (f"{count} membri" if payloads else "Nessun dato")
    return label, rows, _TABLE_STYLE_BASE


@app.callback(
    Output("download-summary-btn", "disabled"),
    Input("run-store", "data"),
)
def handle_download_button_state(run_store: Dict[str, Any]) -> bool:
    metadata = (run_store or {}).get("metadata") or {}
    summary_path = metadata.get("summary_path")
    if not summary_path:
        return True
    return not Path(summary_path).exists()


@app.callback(
    Output("download-summary", "data"),
    Input("download-summary-btn", "n_clicks"),
    State("run-store", "data"),
    prevent_initial_call=True,
)
def trigger_summary_download(n_clicks: int, run_store: Dict[str, Any]):
    if not n_clicks:
        return dash.no_update
    metadata = (run_store or {}).get("metadata") or {}
    summary_path = metadata.get("summary_path")
    if not summary_path:
        return dash.no_update
    path = Path(summary_path)
    if not path.exists():
        return dash.no_update
    return dcc.send_file(path, filename=path.name)


@app.callback(
    Output("member-detail", "children"),
    Output("member-table", "style_data_conditional", allow_duplicate=True),
    Input("member-table", "active_cell"),
    State("run-store", "data"),
    prevent_initial_call=True,
)
def show_member_detail(active_cell: Optional[Dict[str, Any]], run_store: Dict[str, Any]):
    if not active_cell or active_cell.get("column_id") != "inspect":
        return dash.no_update, dash.no_update
    payloads = (run_store or {}).get("payloads") or []
    row_index = active_cell.get("row")
    if row_index is None or row_index < 0 or row_index >= len(payloads):
        return dash.no_update, dash.no_update
    return _member_detail_component(payloads[row_index]), _table_style_with_row(row_index)


def main() -> None:  # pragma: no cover - manual start
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8050")), debug=False)


if __name__ == "__main__":  # pragma: no cover
    main()
