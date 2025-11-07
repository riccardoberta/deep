from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Dict, List, Optional

from member import Member


class Aggregate:
    """Load members from a CSV file."""

    def __init__(self, csv_path: str, *, delimiter: str = ";") -> None:
        self.csv_path = Path(csv_path)
        self.delimiter = delimiter

    def load_members(self) -> List[Member]:
        if not self.csv_path.exists():
            raise FileNotFoundError(f"Input CSV not found: {self.csv_path}")

        members: List[Member] = []
        with self.csv_path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle, delimiter=self.delimiter)
            for row in reader:
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
