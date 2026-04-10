"""
Bibliometric threshold scoring based on D.M. 589/2018.

Scoring per indicator (articoli, citazioni, h-index):
  0.0  → below II fascia threshold
  0.4  → meets II fascia threshold
  0.8  → meets I fascia threshold
  1.2  → meets Commissari threshold
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from openpyxl import load_workbook

# ---------------------------------------------------------------------------
# Mapping: new SSD code → SC key used in the DM 589/2018 threshold table.
# Where the table has an SSD-specific row (e.g. "09/A1-ING-IND/01") that key
# takes priority; otherwise the SC-level key (e.g. "09/A1") is used.
# ---------------------------------------------------------------------------
_NEW_SSD_TO_THRESHOLD_KEY: Dict[str, str] = {
    # Mathematics / Informatics
    "MAT/01": "01/A1",
    "MAT/02": "01/A2",
    "MAT/03": "01/A2",
    "MAT/04": "01/A1-MAT/04",
    "MAT/05": "01/A3",
    "MAT/06": "01/A3-MAT/06",
    "MAT/07": "01/A4",
    "MAT/08": "01/A5",
    "MAT/09": "01/A6",
    "INFO-01/A": "01/B1",   # Informatica  (old INF/01)
    # Physics
    "FIS/01": "02/A1",
    "FIS/02": "02/A2",
    "FIS/03": "02/B1",
    "FIS/04": "02/B2",
    "FIS/05": "02/B1",
    "FIS/06": "02/C1-FIS/06",
    "FIS/07": "02/C1",
    "FIS/08": "02/D1-FIS/08",
    # Engineering – area 09
    "IIND-01/A": "09/A1-ING-IND/01",   # Architettura navale
    "IIND-01/B": "09/A1-ING-IND/02",   # Costruzioni navali
    "IIND-01/C": "09/A1-ING-IND/03",   # Meccanica del volo
    "IIND-02/A": "09/A2",              # Meccanica applicata
    "IIND-03/A": "09/A3",              # Progettazione industriale
    "IIND-03/B": "09/A3-ING-IND/15",  # Disegno e metodi dell'ingegneria industriale
    "IIND-04/A": "09/B1",              # Tecnologie e sistemi di lavorazione
    "IIND-05/A": "09/B2",              # Impianti industriali meccanici
    "IIND-06/A": "09/B3",              # Ingegneria economico-gestionale
    "IIND-07/A": "09/C1",              # Macchine e sistemi per l'energia
    "IIND-07/B": "09/C2",              # Fisica tecnica
    "IIND-08/A": "09/E1",              # Elettrotecnica (old ING-IND/31)
    "IIND-08/B": "09/E2",              # Sistemi elettrici per l'energia (old ING-IND/33)
    "IIET-01/A": "09/E1",              # Elettrotecnica (old ING-IND/31)
    "IIET-01/B": "09/E2",              # Ingegneria dell'energia elettrica (old ING-IND/32-33)
    "IINF-01/A": "09/E3",              # Elettronica (old ING-INF/01)
    "IINF-02/A": "09/F1",              # Campi elettromagnetici (old ING-INF/02)
    "IINF-03/A": "09/F2",              # Telecomunicazioni (old ING-INF/03)
    "IINF-04/A": "09/G1",              # Automatica (old ING-INF/04)
    "IINF-05/A": "09/H1",              # Sistemi di elaborazione (old ING-INF/05)
    "IINF-06/A": "09/G2",              # Bioingegneria (old ING-INF/06)
    # Civil engineering – area 08
    "ICAR-01": "08/A1",
    "ICAR-02": "08/A1",
    "ICAR-03": "08/A2",
    "ICAR-04": "08/A3",
    "ICAR-07": "08/B1",
    "ICAR-08": "08/B2",
    "ICAR-09": "08/B3",
    # Medicine – area 06
    "MEDS-16/A": "06/F1",              # Malattie odontostomatologiche (old MED/28)
}

# ---------------------------------------------------------------------------
# Threshold record indices (columns 2-10 of the XLSX, 0-indexed within row):
#   0: II fascia  – Art. 5a
#   1: II fascia  – Cit. 10a
#   2: II fascia  – H-idx 10a
#   3: I fascia   – Art. 10a
#   4: I fascia   – Cit. 15a
#   5: I fascia   – H-idx 15a
#   6: Commissari – Art. 10a
#   7: Commissari – Cit. 15a
#   8: Commissari – H-idx 15a
# ---------------------------------------------------------------------------
ThresholdRow = Tuple[
    Optional[int], Optional[int], Optional[int],  # II fascia
    Optional[int], Optional[int], Optional[int],  # I fascia
    Optional[int], Optional[int], Optional[int],  # Commissari
]

_DEFAULT_XLSX = Path(__file__).parent / "soglie" / "soglie_dm589_2018.xlsx"


def load_thresholds(xlsx_path: Path | str = _DEFAULT_XLSX) -> Dict[str, ThresholdRow]:
    """Return a dict mapping SC/SSD code → 9-tuple of threshold values."""
    wb = load_workbook(filename=str(xlsx_path), read_only=True, data_only=True)
    sheet = wb.active
    rows = list(sheet.iter_rows(values_only=True))
    wb.close()

    result: Dict[str, ThresholdRow] = {}
    for row in rows[2:]:  # first two rows are header
        if not row[0]:
            continue
        code = str(row[0]).strip()
        values: ThresholdRow = tuple(  # type: ignore[assignment]
            int(v) if isinstance(v, (int, float)) and v is not None else None
            for v in row[2:11]
        )
        result[code] = values
    return result


def _extract_new_ssd_code(ssd_str: str) -> Optional[str]:
    """Extract the bare SSD code from payload strings.

    Handles formats like:
      'IINF-01/A (Elettronica)'
      'IINF-01/A – Elettronica'
      'INFO-01/A'
    """
    if not ssd_str:
        return None
    m = re.match(r"([A-Z][A-Z0-9\-]+/[A-Z0-9]+)", ssd_str.strip())
    return m.group(1) if m else None


def _find_threshold(
    thresholds: Dict[str, ThresholdRow], ssd_str: str
) -> Optional[ThresholdRow]:
    """Return the threshold row for *ssd_str*, or None if not found."""
    code = _extract_new_ssd_code(ssd_str)
    if not code:
        return None
    # Direct match (e.g. old-style code already in the table)
    if code in thresholds:
        return thresholds[code]
    # Map new SSD code → table key
    key = _NEW_SSD_TO_THRESHOLD_KEY.get(code)
    if key and key in thresholds:
        return thresholds[key]
    return None


def _get_metric(metrics: List[Dict[str, Any]], period_prefix: str, field: str) -> Optional[int]:
    for m in metrics:
        if str(m.get("period", "")).startswith(period_prefix):
            v = m.get(field)
            return int(v) if v is not None else None
    return None


def _level_entry(
    value: Optional[int], threshold: Optional[int], years: int
) -> Dict[str, Any]:
    """Build one threshold-level entry: years, member value, threshold, ratio."""
    if value is None or not threshold:
        return {"years": years, "value": value, "threshold": threshold, "ratio": None}
    return {
        "years": years,
        "value": value,
        "threshold": threshold,
        "ratio": round(value / threshold, 2),
    }


def _indicator_block(
    val_ii: Optional[int], thresh_ii: Optional[int], years_ii: int,
    val_i:  Optional[int], thresh_i:  Optional[int], years_i:  int,
    val_c:  Optional[int], thresh_c:  Optional[int], years_c:  int,
) -> Dict[str, Any]:
    """Build a single-indicator score block with score and per-level detail."""
    if val_ii is None or thresh_ii is None:
        score = None
    else:
        score = 0.0
        if val_ii >= thresh_ii:
            score = 0.4
        if val_i is not None and thresh_i is not None and val_i >= thresh_i:
            score = 0.8
        if val_c is not None and thresh_c is not None and val_c >= thresh_c:
            score = 1.2

    return {
        "score":       score,
        "ii_fascia":   _level_entry(val_ii, thresh_ii, years_ii),
        "i_fascia":    _level_entry(val_i,  thresh_i,  years_i),
        "commissario": _level_entry(val_c,  thresh_c,  years_c),
    }


def compute_scores(
    ssd_str: Optional[str],
    metrics: List[Dict[str, Any]],
    thresholds: Dict[str, ThresholdRow],
) -> Dict[str, Any]:
    """Return a *scores* dict nested under three indicator keys.

    Each indicator block has the structure::

        {
            "score": 1.2,
            "ii_fascia":   {"years": 5,  "value": 54, "threshold": 9,  "ratio": 6.0},
            "i_fascia":    {"years": 10, "value": 75, "threshold": 18, "ratio": 4.17},
            "commissario": {"years": 10, "value": 75, "threshold": 28, "ratio": 2.68},
        }

    ``score`` is None when data are unavailable or the sector is not bibliometric.
    """
    def _null_block() -> Dict[str, Any]:
        empty = {"years": None, "value": None, "threshold": None, "ratio": None}
        return {"score": None, "ii_fascia": dict(empty), "i_fascia": dict(empty), "commissario": dict(empty)}

    null: Dict[str, Any] = {
        "articles":  _null_block(),
        "citations": _null_block(),
        "hindex":    _null_block(),
    }

    if not ssd_str or not metrics:
        return null

    thresh = _find_threshold(thresholds, ssd_str)
    if thresh is None:
        return null

    (art5a, cit10a, h10a, art10a_i, cit15a_i, h15a_i, art10a_c, cit15a_c, h15a_c) = thresh

    art5  = _get_metric(metrics, "05 years", "total_products")
    art10 = _get_metric(metrics, "10 years", "total_products")
    cit10 = _get_metric(metrics, "10 years", "citations")
    cit15 = _get_metric(metrics, "15 years", "citations")
    h10   = _get_metric(metrics, "10 years", "hindex")
    h15   = _get_metric(metrics, "15 years", "hindex")

    return {
        # Articles: II fascia 5yr, I fascia and Commissari 10yr.
        "articles": _indicator_block(
            art5,  art5a,   5,
            art10, art10a_i, 10,
            art10, art10a_c, 10,
        ),
        # Citations: II fascia 10yr, I fascia and Commissari 15yr.
        "citations": _indicator_block(
            cit10, cit10a,   10,
            cit15, cit15a_i, 15,
            cit15, cit15a_c, 15,
        ),
        # H-index: same windows as citations.
        "hindex": _indicator_block(
            h10, h10a,   10,
            h15, h15a_i, 15,
            h15, h15a_c, 15,
        ),
    }
