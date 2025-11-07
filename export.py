from __future__ import annotations

import re
import textwrap
from pathlib import Path
from typing import Any, Dict, List, Sequence


class Exporter:
    def export(self, payloads: Sequence[Dict[str, Any]], run_dir: Path) -> Path:
        return self._export_markdown(payloads, run_dir)

    def _export_markdown(self, payloads: Sequence[Dict[str, Any]], run_dir: Path) -> Path:
        md_dir = run_dir / "markdown"
        md_dir.mkdir(parents=True, exist_ok=True)
        pdf_dir = run_dir / "pdf"
        pdf_dir.mkdir(parents=True, exist_ok=True)
        for index, payload in enumerate(payloads):
            filename = f"{self._slugify(payload.get('surname', ''))}_{self._slugify(payload.get('name', ''))}_{payload.get('scopus_id', '') or index}.md"
            md_path = md_dir / filename
            lines = self._build_markdown_lines(payload)
            self._write_markdown(md_path, lines)
            pdf_path = pdf_dir / f"{md_path.stem}.pdf"
            self._write_pdf(pdf_path, lines)
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
        overview_lines = [
            f"- **{label}:** {self._format_value(value)}"
            for label, value in overview_pairs
            if value not in (None, "")
        ]
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
        contact_lines = [
            f"- **{label}:** {self._format_value(value)}"
            for label, value in contact_info
            if value not in (None, "")
        ]
        location_entries = payload.get("location") or []
        if location_entries:
            location_text = "; ".join(
                filter(None, (self._format_location(entry) for entry in location_entries))
            )
            if location_text:
                contact_lines.append(f"- **Locations:** {location_text}")
        if contact_lines:
            lines.append("## Contact")
            lines.extend(contact_lines)
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
            for idx, entry in enumerate(responsibilities):
                lines.extend(self._format_responsibility_block(entry))
                if idx < len(responsibilities) - 1:
                    lines.append("")
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
                lines.append(f"{idx}.")
                product_lines = self._format_product_block(product)
                for line in product_lines:
                    lines.append(f"   {line}")
            lines.append("")

        return lines

    def _format_location(self, entry: Any) -> str:
        if not isinstance(entry, dict):
            return self._to_text(entry)
        components = [
            self._format_value(entry.get("building")),
            self._format_value(entry.get("floor")),
            self._format_value(entry.get("room")),
        ]
        extras = [
            f"{key}: {self._format_value(value)}"
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
            self._format_value(entry.get("role")),
            self._format_range(entry.get("from"), entry.get("to")),
        ]
        extras = [
            f"{key}: {self._format_value(value)}"
            for key, value in entry.items()
            if key not in {"role", "from", "to"} and value
        ]
        result = ", ".join(part for part in parts if part)
        if extras:
            extras_str = "; ".join(extras)
            result = f"{result} ({extras_str})" if result else extras_str
        return result or "-"

    def _format_responsibility_block(self, entry: Any) -> List[str]:
        if not isinstance(entry, dict):
            return [f"- {self._to_text(entry)}"]

        fields = [
            ("Title", self._format_value(entry.get("title"))),
            ("Unit", self._format_value(entry.get("unit"))),
            ("Role", self._format_value(entry.get("role"))),
            ("Period", self._format_range(entry.get("from"), entry.get("to"))),
        ]
        lines: List[str] = []
        for label, value in fields:
            if value not in ("", None):
                prefix = "- " if not lines else "  - "
                lines.append(f"{prefix}**{label}:** {value}")

        extras = [
            (key, self._format_value(value))
            for key, value in entry.items()
            if key not in {"title", "unit", "role", "from", "to"} and value
        ]
        for key, value in extras:
            prefix = "- " if not lines else "  - "
            label = re.sub(r"_+", " ", key).title()
            lines.append(f"{prefix}**{label}:** {value}")

        if not lines:
            lines.append("- Responsibility")
        return lines

    def _format_teaching(self, entry: Any) -> str:
        if not isinstance(entry, dict):
            return self._to_text(entry)
        parts = [
            self._format_value(entry.get("course")),
            self._format_value(entry.get("degree")),
        ]
        extras = [
            f"{key}: {self._format_value(value)}"
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

    def _format_product_block(self, product: Any) -> List[str]:
        if not isinstance(product, dict):
            return [f"- {self._to_text(product)}"]

        ordered_fields = [
            ("Title", self._format_value(product.get("title"))),
            ("Venue", self._format_value(product.get("venue"))),
            ("Year", self._format_value(product.get("year"))),
            ("Type", self._format_value(product.get("type"))),
            ("Subtype", self._format_value(product.get("sub_type"))),
            ("Citations", self._format_value(product.get("citations"))),
            ("DOI", self._format_value(product.get("doi"))),
            ("Scopus ID", self._format_value(product.get("scopus_id"))),
            ("ISSN", self._format_value(product.get("issn"))),
            ("eISSN", self._format_value(product.get("eIssn"))),
            ("Volume", self._format_value(product.get("volume"))),
            ("Issue", self._format_value(product.get("issue_id"))),
            ("Pages", self._format_value(product.get("pages"))),
            ("Authors", self._format_value(product.get("authors"))),
            ("Keywords", self._format_value(product.get("keywords"))),
            ("Quartile", product.get("quartile")),
        ]

        lines: List[str] = []
        for label, value in ordered_fields:
            if value in (None, ""):
                continue
            if label == "Quartile":
                lines.extend(self._format_quartile_block(value))
            else:
                lines.append(f"- **{label}:** {value}")

        extras = [
            (key, value)
            for key, value in product.items()
            if key
            not in {
                "title",
                "venue",
                "year",
                "type",
                "sub_type",
                "citations",
                "doi",
                "scopus_id",
                "issn",
                "eIssn",
                "volume",
                "issue_id",
                "pages",
                "authors",
                "keywords",
                "quartile",
                "abstract",
            }
            and value not in (None, "")
        ]
        for key, value in extras:
            label = re.sub(r"_+", " ", key).title()
            lines.append(f"- **{label}:** {self._format_value(value)}")

        return lines or ["- -"]

    def _write_pdf(self, path: Path, lines: Sequence[str]) -> None:
        prepared = self._prepare_pdf_lines(lines)
        wrapped = self._wrap_pdf_lines(prepared)
        if not wrapped:
            wrapped = [""]

        page_width = 595  # A4 width in points
        page_height = 842  # A4 height in points
        margin = 40
        line_height = 14
        max_lines = max(1, int((page_height - 2 * margin) / line_height))
        pages: List[List[str]] = [
            wrapped[index : index + max_lines] for index in range(0, len(wrapped), max_lines)
        ]
        if not pages:
            pages = [[]]

        builder = _SimplePDFBuilder()
        catalog_id = builder.reserve()
        pages_id = builder.reserve()
        font_id = builder.add_object("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

        page_ids: List[int] = []
        for page_lines in pages:
            stream = self._build_pdf_stream(page_lines, margin, page_height, line_height)
            content_id = builder.add_stream(stream)
            page_obj = (
                f"<< /Type /Page /Parent {pages_id} 0 R /MediaBox [0 0 {page_width} {page_height}] "
                f"/Contents {content_id} 0 R "
                f"/Resources << /Font << /F1 {font_id} 0 R >> >> >>"
            )
            page_id = builder.add_object(page_obj)
            page_ids.append(page_id)

        kids = " ".join(f"{pid} 0 R" for pid in page_ids)
        pages_obj = f"<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>"
        builder.set_object(pages_id, pages_obj)
        builder.set_object(catalog_id, f"<< /Type /Catalog /Pages {pages_id} 0 R >>")
        builder.write(path, catalog_id)

    def _build_pdf_stream(
        self,
        lines: Sequence[str],
        margin: int,
        page_height: int,
        line_height: int,
    ) -> str:
        parts = [
            "BT",
            "/F1 11 Tf",
            f"{line_height} TL",
            f"{margin} {page_height - margin} Td",
        ]
        for line in lines:
            parts.append(f"({self._pdf_escape(line)}) Tj")
            parts.append("T*")
        parts.append("ET")
        return "\n".join(parts)

    def _prepare_pdf_lines(self, lines: Sequence[str]) -> List[str]:
        prepared: List[str] = []
        for line in lines:
            prepared.append(self._markdown_to_pdf_line(line or ""))
        return prepared

    def _wrap_pdf_lines(self, lines: Sequence[str], width: int = 90) -> List[str]:
        wrapped: List[str] = []
        for line in lines:
            wrapped.extend(self._wrap_line_for_pdf(line, width))
        return wrapped

    def _wrap_line_for_pdf(self, line: str, width: int) -> List[str]:
        if not line:
            return [""]
        indent_len = len(line) - len(line.lstrip(" "))
        indent = " " * indent_len
        text = line[indent_len:]
        effective_width = max(20, width - indent_len)
        wrapper = textwrap.TextWrapper(
            width=effective_width,
            replace_whitespace=False,
            drop_whitespace=False,
        )
        chunks = wrapper.wrap(text)
        if not chunks:
            return [indent]
        return [indent + chunk for chunk in chunks]

    @staticmethod
    def _pdf_escape(text: str) -> str:
        escaped = (
            text.replace("\\", "\\\\")
            .replace("(", "\\(")
            .replace(")", "\\)")
        )
        return escaped.encode("latin-1", "replace").decode("latin-1")

    def _markdown_to_pdf_line(self, line: str) -> str:
        text = self._format_value(line)
        if not text:
            return ""

        stripped = text.lstrip()
        indent = len(text) - len(stripped)
        prefix = " " * indent

        if stripped.startswith("#"):
            hash_count = len(stripped) - len(stripped.lstrip("#"))
            content = stripped[hash_count:].strip()
            content = content.upper() if hash_count == 1 else content
            return prefix + content

        bullet_prefix = "• "
        if stripped.startswith("- "):
            content = stripped[2:].strip()
            return prefix + bullet_prefix + self._strip_markdown_inline(content)

        ordered_match = re.match(r"(\d+)\.\s+(.*)", stripped)
        if ordered_match:
            number, rest = ordered_match.groups()
            cleaned = self._strip_markdown_inline(rest.strip())
            return prefix + f"{number}. {cleaned}"

        return prefix + self._strip_markdown_inline(stripped)

    @staticmethod
    def _strip_markdown_inline(text: str) -> str:
        cleaned = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
        cleaned = re.sub(r"\*(.+?)\*", r"\1", cleaned)
        cleaned = re.sub(r"`(.+?)`", r"\1", cleaned)
        cleaned = re.sub(r"__(.+?)__", r"\1", cleaned)
        cleaned = re.sub(r"_(.+?)_", r"\1", cleaned)
        cleaned = re.sub(r"\[(.+?)\]\((.+?)\)", r"\1 (\2)", cleaned)
        return cleaned

    def _format_quartile_block(self, data: Any) -> List[str]:
        if not data:
            return []
        if isinstance(data, str):
            return ["- **Quartile:**", f"  - {self._format_value(data)}"]

        lines: List[str] = ["- **Quartile:**"]
        items = data if isinstance(data, list) else [data]
        for item in items:
            if not isinstance(item, dict):
                lines.append(f"  - {self._format_value(item)}")
                continue

            subjects = item.get("subjects")
            subject_texts: List[str] = []
            if isinstance(subjects, list) and subjects:
                for subject in subjects:
                    if not isinstance(subject, dict):
                        subject_texts.append(self._format_value(subject))
                        continue
                    name = (
                        self._format_value(subject.get("subject"))
                        or self._format_value(subject.get("name"))
                        or "Subject"
                    )
                    details: List[str] = []
                    quartile = self._format_value(subject.get("quartile"))
                    if quartile:
                        details.append(quartile)
                    rank = subject.get("rank")
                    if rank not in (None, ""):
                        details.append(f"rank {rank}")
                    percentile = subject.get("percentile")
                    if percentile not in (None, ""):
                        details.append(f"percentile {percentile}")
                    detail_text = f" ({', '.join(details)})" if details else ""
                    subject_texts.append(f"{name}{detail_text}")
            else:
                subject_texts.append("(no subjects)")

            year = self._format_value(item.get("year"))
            if year:
                lines.append(f"  {year}:")
            for subject_text in subject_texts:
                lines.append(f"  - {subject_text}")

        return lines

    @staticmethod
    def _format_range(start: Any, end: Any) -> str:
        def _clean(value: Any) -> str:
            if not value:
                return ""
            text = str(value).strip()
            match = re.search(r"\d{4}-\d{2}-\d{2}", text)
            if match:
                return match.group(0)
            slash = re.search(r"\d{4}/\d{2}/\d{2}", text)
            if slash:
                return slash.group(0).replace("/", "-")
            parts = re.split(r"[T\s]", text, maxsplit=1)
            if parts:
                candidate = parts[0]
                if re.fullmatch(r"\d{4}-\d{2}-\d{2}", candidate):
                    return candidate
            return text

        start_clean = _clean(start)
        end_clean = _clean(end)
        if not start_clean and not end_clean:
            return ""
        if start_clean and end_clean:
            return f"{start_clean} → {end_clean}"
        return start_clean or end_clean

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
        return Exporter._format_value(text)

    @staticmethod
    def _format_value(value: Any) -> str:
        if value is None:
            return ""
        text = str(value)
        return Exporter._normalize_caps(text)

    @staticmethod
    def _normalize_caps(text: str) -> str:
        letters = [char for char in text if char.isalpha()]
        if not letters:
            return text
        has_whitespace = any(char.isspace() for char in text)
        if all(char.isupper() for char in letters) and has_whitespace:
            lowered = text.lower()
            return re.sub(
                r"\b([a-z])",
                lambda match: match.group(1).upper(),
                lowered,
            )
        return text


class _SimplePDFBuilder:
    def __init__(self) -> None:
        self._objects: List[bytes] = []

    def reserve(self) -> int:
        self._objects.append(b"")
        return len(self._objects)

    def add_object(self, content: str) -> int:
        self._objects.append(content.encode("utf-8"))
        return len(self._objects)

    def add_stream(self, stream: str) -> int:
        data = stream.encode("utf-8")
        obj = f"<< /Length {len(data)} >>\nstream\n{stream}\nendstream".encode("utf-8")
        self._objects.append(obj)
        return len(self._objects)

    def set_object(self, object_id: int, content: str) -> None:
        self._objects[object_id - 1] = content.encode("utf-8")

    def write(self, path: Path, root_id: int) -> None:
        with path.open("wb") as handle:
            handle.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
            offsets: List[int] = []
            for index, obj in enumerate(self._objects, start=1):
                offsets.append(handle.tell())
                handle.write(f"{index} 0 obj\n".encode("utf-8"))
                handle.write(obj)
                handle.write(b"\nendobj\n")
            xref_pos = handle.tell()
            handle.write(f"xref\n0 {len(self._objects) + 1}\n".encode("utf-8"))
            handle.write(b"0000000000 65535 f \n")
            for offset in offsets:
                handle.write(f"{offset:010d} 00000 n \n".encode("utf-8"))
            handle.write(b"trailer\n")
            handle.write(f"<< /Size {len(self._objects) + 1} /Root {root_id} 0 R >>\n".encode("utf-8"))
            handle.write(b"startxref\n")
            handle.write(f"{xref_pos}\n".encode("utf-8"))
            handle.write(b"%%EOF")
