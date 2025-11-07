from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Dict, List, Optional

from member import Member

try:  # pragma: no cover - optional dependency
    from openpyxl import load_workbook
except Exception:  # pragma: no cover
    load_workbook = None


class Aggregate:
    """Load members from a CSV or XLSX file."""

    def __init__(self, input_path: str, *, delimiter: str = ";") -> None:
        self.input_path = Path(input_path)
        self.delimiter = delimiter

    def load_members(self) -> List[Member]:
        if not self.input_path.exists():
            raise FileNotFoundError(f"Input file not found: {self.input_path}")

        rows = self._read_rows()
        members: List[Member] = []
        for row in rows:
            normalized = self._normalize_row(row)

            surname = normalized.get("surname")
            name = normalized.get("name")
            scopus_id = normalized.get("scopusid")
            if not (surname and name and scopus_id):
                continue

            unige_id = normalized.get("unigeid") or None
            unit = normalized.get("unit") or None

            members.append(
                Member(
                    surname=surname,
                    name=name,
                    scopus_id=scopus_id,
                    unit=unit,
                    unige_id=unige_id,
                )
            )
        return members

    def _read_rows(self) -> List[Dict[Optional[str], Optional[str]]]:
        suffix = self.input_path.suffix.lower()
        if suffix in {".xlsx", ".xlsm"}:
            if load_workbook is None:
                raise RuntimeError(
                    "openpyxl is required to read XLSX files. Install it via 'pip install openpyxl'."
                )
            return self._read_rows_from_xlsx()
        return self._read_rows_from_csv()

    def _read_rows_from_csv(self) -> List[Dict[Optional[str], Optional[str]]]:
        rows: List[Dict[Optional[str], Optional[str]]] = []
        with self.input_path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle, delimiter=self.delimiter)
            rows.extend(reader)
        return rows

    def _read_rows_from_xlsx(self) -> List[Dict[Optional[str], Optional[str]]]:
        workbook = load_workbook(filename=self.input_path, read_only=True, data_only=True)
        sheet = workbook.active

        rows: List[Dict[Optional[str], Optional[str]]] = []
        iterator = sheet.iter_rows(values_only=True)

        header: List[str] = []
        for row in iterator:
            header = [self._cell_to_text(cell) for cell in row]
            if any(header):
                break
        if not header:
            workbook.close()
            return rows

        for row in iterator:
            values = [self._cell_to_text(cell) for cell in row]
            if not any(values):
                continue
            record: Dict[Optional[str], Optional[str]] = {}
            for idx, key in enumerate(header):
                if not key:
                    continue
                record[key] = values[idx] if idx < len(values) else ""
            rows.append(record)

        workbook.close()
        return rows

    @staticmethod
    def _cell_to_text(value: Optional[object]) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value.strip()
        return str(value).strip()

    @staticmethod
    def _normalize_row(row: Dict[Optional[str], Optional[str]]) -> Dict[str, str]:
        cleaned: Dict[str, str] = {}
        for key, value in row.items():
            if key is None:
                continue
            normalized_key = re.sub(r"[^a-z0-9]", "", key.strip().lower())
            if not normalized_key:
                continue
            cleaned[normalized_key] = (value or "").strip()
        return cleaned
