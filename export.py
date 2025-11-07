from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Sequence


class Exporter:
    def export(self, payloads: Sequence[Dict[str, Any]], run_dir: Path) -> Path:
        return self._export_markdown(payloads, run_dir)

    def _export_markdown(self, payloads: Sequence[Dict[str, Any]], run_dir: Path) -> Path:
        md_dir = run_dir / "markdown"
        md_dir.mkdir(parents=True, exist_ok=True)
        for index, payload in enumerate(payloads):
            filename = f"{self._slugify(payload.get('surname', ''))}_{self._slugify(payload.get('name', ''))}_{payload.get('scopus_id', '') or index}.md"
            md_path = md_dir / filename
            lines = self._build_markdown_lines(payload)
            self._write_markdown(md_path, lines)
        return md_dir

    def _build_markdown_lines(self, payload: Dict[str, Any]) -> List[str]:
        lines: List[str] = []
        full_name = f"{payload.get('surname', '')} {payload.get('name', '')}".strip()
        title = full_name or "Member Profile"
        lines.append(f"# {title}")
        lines.append("")

        overview_pairs = [
            ("Unit", payload.get("unit")),
            ("Role", payload.get("role")),
            ("Grade", payload.get("grade")),
            ("SSD", payload.get("ssd")),
            ("Scopus ID", payload.get("scopus_id")),
            ("UNIGE ID", payload.get("unige_id")),
            ("Retrieved at", payload.get("retrieved_at")),
        ]
        overview_lines = [f"- **{label}:** {value}" for label, value in overview_pairs if value]
        if overview_lines:
            lines.append("## Overview")
            lines.extend(overview_lines)
            lines.append("")

        contact_info = [
            ("Email", payload.get("email")),
            ("Phone", payload.get("phone")),
            ("Website", payload.get("website")),
            ("Page", payload.get("page")),
        ]
        contact_lines = [f"- **{label}:** {value}" for label, value in contact_info if value]
        if contact_lines:
            lines.append("## Contact")
            lines.extend(contact_lines)
            lines.append("")

        location_entries = payload.get("location") or []
        if location_entries:
            lines.append("## Locations")
            for entry in location_entries:
                lines.append(f"- {self._format_location(entry)}")
            lines.append("")

        career_entries = payload.get("career") or []
        if career_entries:
            lines.append("## Career")
            for entry in career_entries:
                lines.append(f"- {self._format_career(entry)}")
            lines.append("")

        responsibilities = payload.get("responsibilities") or []
        if responsibilities:
            lines.append("## Responsibilities")
            for entry in responsibilities:
                lines.append(f"- {self._format_responsibility(entry)}")
            lines.append("")

        teaching = payload.get("teaching") or {}
        if teaching:
            lines.append("## Teaching")
            for year in sorted(teaching.keys(), reverse=True):
                lessons = teaching[year]
                lines.append(f"### {year}")
                for lesson in lessons:
                    lines.append(f"- {self._format_teaching(lesson)}")
                lines.append("")

        metrics = payload.get("scopus_metrics") or []
        if metrics:
            lines.append("## Scopus Metrics")
            for metric in metrics:
                lines.append(f"- {self._format_metric(metric)}")
            lines.append("")

        products = payload.get("scopus_products") or []
        if products:
            lines.append("## Scopus Products")
            for idx, product in enumerate(products, start=1):
                lines.append(f"{idx}. {self._format_product(product)}")
            lines.append("")

        return lines

    def _format_location(self, entry: Any) -> str:
        if not isinstance(entry, dict):
            return self._to_text(entry)
        components = [
            entry.get("building"),
            entry.get("floor"),
            entry.get("room"),
        ]
        extras = [
            f"{key}: {value}"
            for key, value in entry.items()
            if key not in {"building", "floor", "room"} and value
        ]
        result = ", ".join(part for part in components if part)
        if extras:
            extras_str = "; ".join(extras)
            result = f"{result} ({extras_str})" if result else extras_str
        return result or "-"

    def _format_career(self, entry: Any) -> str:
        if not isinstance(entry, dict):
            return self._to_text(entry)
        parts = [
            entry.get("role"),
            self._format_range(entry.get("from"), entry.get("to")),
        ]
        extras = [
            f"{key}: {value}"
            for key, value in entry.items()
            if key not in {"role", "from", "to"} and value
        ]
        result = ", ".join(part for part in parts if part)
        if extras:
            extras_str = "; ".join(extras)
            result = f"{result} ({extras_str})" if result else extras_str
        return result or "-"

    def _format_responsibility(self, entry: Any) -> str:
        if not isinstance(entry, dict):
            return self._to_text(entry)
        parts = [
            entry.get("title"),
            entry.get("unit"),
            self._format_range(entry.get("from"), entry.get("to")),
        ]
        extras = [
            f"{key}: {value}"
            for key, value in entry.items()
            if key not in {"title", "unit", "from", "to"} and value
        ]
        result = ", ".join(part for part in parts if part)
        if extras:
            extras_str = "; ".join(extras)
            result = f"{result} ({extras_str})" if result else extras_str
        return result or "-"

    def _format_teaching(self, entry: Any) -> str:
        if not isinstance(entry, dict):
            return self._to_text(entry)
        parts = [
            entry.get("course"),
            entry.get("degree"),
        ]
        extras = [
            f"{key}: {value}"
            for key, value in entry.items()
            if key not in {"course", "degree"} and value
        ]
        result = ", ".join(part for part in parts if part)
        if extras:
            extras_str = "; ".join(extras)
            result = f"{result} ({extras_str})" if result else extras_str
        return result or "-"

    def _format_metric(self, metric: Any) -> str:
        if not isinstance(metric, dict):
            return self._to_text(metric)
        parts: List[str] = []
        period = metric.get("period")
        if period:
            parts.append(str(period))
        for label, key in [
            ("Docs", "total_products"),
            ("Journals", "journals"),
            ("Conferences", "conferences"),
            ("Citations", "citations"),
            ("H-index", "hindex"),
        ]:
            value = metric.get(key)
            if value not in (None, ""):
                parts.append(f"{label}: {value}")
        return "; ".join(parts) or "-"

    def _format_product(self, product: Any) -> str:
        if not isinstance(product, dict):
            return self._to_text(product)
        parts = [
            f"**Title:** {product.get('title')}" if product.get("title") else None,
            f"**Venue:** {product.get('venue')}" if product.get("venue") else None,
            f"**Year:** {product.get('year')}" if product.get("year") else None,
            f"**Type:** {product.get('type')}" if product.get("type") else None,
            f"**Citations:** {product.get('citations')}" if product.get("citations") is not None else None,
        ]
        extras = [
            f"{key}: {value}"
            for key, value in product.items()
            if key not in {"title", "venue", "year", "type", "citations"} and value
        ]
        result = "; ".join(part for part in parts if part)
        if extras:
            extras_str = "; ".join(extras)
            result = f"{result}; {extras_str}" if result else extras_str
        return result or "-"

    @staticmethod
    def _format_range(start: Any, end: Any) -> str:
        if not start and not end:
            return ""
        if start and end:
            return f"{start} → {end}"
        return str(start or end)

    @staticmethod
    def _write_markdown(path: Path, lines: Sequence[str]) -> None:
        content = "\n".join(lines).rstrip() + "\n"
        path.write_text(content, encoding="utf-8")

    @staticmethod
    def _slugify(value: Any, default: str = "unknown") -> str:
        if not value:
            return default
        cleaned = re.sub(r"\s+", "_", str(value).strip().lower())
        cleaned = re.sub(r"[^a-z0-9_]", "", cleaned)
        return cleaned or default

    @staticmethod
    def _to_text(text: Any) -> str:
        if text is None:
            return ""
        return str(text)
