from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Sequence


class DataPreparation:
    def prepare(
        self,
        payloads: Sequence[Dict[str, Any]],
        run_dir: Path,
        input_csv: str,
    ) -> Path:
        output_dir = run_dir / "elaborations"
        output_dir.mkdir(parents=True, exist_ok=True)
        summary_name = f"{Path(input_csv).stem}_results.csv"
        summary_path = output_dir / summary_name

        records = [self._build_summary_row(payload) for payload in payloads]
        if records:
            header = list(records[0].keys())
            with summary_path.open("w", encoding="utf-8", newline="") as handle:
                for row in [header, *([list(record.values()) for record in records])]:
                    handle.write(";".join(str(field) for field in row) + "\n")
        else:
            summary_path.write_text("", encoding="utf-8")

        return summary_path

    def _build_summary_row(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        metrics = payload.get("scopus_metrics") or []
        absolute = next(
            (metric for metric in metrics if (metric.get("period") or "").lower() == "absolute"),
            {},
        )

        row = {
            "LastName": payload.get("surname", ""),
            "FirstName": payload.get("name", ""),
            "Unit": payload.get("unit", ""),
            "Role": payload.get("role", ""),
            "SSD": payload.get("ssd", ""),
            "ScopusID": payload.get("scopus_id", ""),
            "UnigeID": payload.get("unige_id", ""),
            "Total_Docs": absolute.get("total_products", ""),
            "Total_Citations": absolute.get("citations", ""),
            "H_index": absolute.get("hindex", ""),
        }

        for metric in metrics:
            period = metric.get("period", "")
            suffix = self._extract_suffix(period)
            if not suffix:
                continue
            row[f"Docs_{suffix}"] = metric.get("total_products", "")
            row[f"Citations_{suffix}"] = metric.get("citations", "")
            row[f"Journals_{suffix}"] = metric.get("journals", "")
            row[f"Conferences_{suffix}"] = metric.get("conferences", "")
            row[f"H_index_{suffix}"] = metric.get("hindex", "")

        return row

    @staticmethod
    def _extract_suffix(period: str) -> str:
        digits = ""
        for char in period:
            if char.isdigit():
                digits += char
            elif digits:
                break
        return f"{digits}y" if digits else ""
