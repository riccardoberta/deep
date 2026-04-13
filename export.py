from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from fpdf import FPDF


# ---------------------------------------------------------------------------
# On-demand per-member PDF
# ---------------------------------------------------------------------------

def generate_member_pdf(payload: Dict[str, Any]) -> bytes:
    """Return A4 PDF bytes for a single member profile."""

    # ── Palette ──────────────────────────────────────────────────────────────
    C_BLUE   = (13,  110, 253)
    C_DARK   = (33,   37,  41)
    C_GRAY   = (108, 117, 125)
    C_LIGHT  = (248, 249, 250)
    C_WHITE  = (255, 255, 255)
    C_GREEN  = (25,  135,  84)
    C_ORANGE = (253, 126,  20)
    C_RED    = (220,  53,  69)

    class _PDF(FPDF):
        def footer(self) -> None:
            self.set_y(-12)
            self.set_font("Helvetica", "", 7)
            self.set_text_color(*C_GRAY)
            name = _safe(f"{payload.get('surname','')} {payload.get('name','')}".strip())
            self.cell(0, 5, f"{name}  -  page {self.page_no()}", align="C")

    pdf = _PDF(format="A4")
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.add_page()

    W  = 180   # usable width
    LM = 15    # left margin

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _safe(value: Any, max_len: int = 200) -> str:
        if value is None:
            return ""
        text = str(value)
        # encode to latin-1 replacing unmappable chars
        return text[:max_len].encode("latin-1", "replace").decode("latin-1")

    def _section(title: str) -> None:
        pdf.ln(3)
        pdf.set_font("Helvetica", "B", 11)
        pdf.set_text_color(*C_BLUE)
        pdf.set_x(LM)
        pdf.cell(W, 7, _safe(title), ln=True)
        pdf.set_draw_color(*C_BLUE)
        pdf.set_line_width(0.5)
        pdf.line(LM, pdf.get_y(), LM + W, pdf.get_y())
        pdf.set_draw_color(200, 200, 200)
        pdf.set_line_width(0.2)
        pdf.set_text_color(*C_DARK)
        pdf.ln(3)

    def _kv(label: str, value: Any, w_label: float = 38) -> None:
        if value is None or value == "":
            return
        pdf.set_x(LM)
        pdf.set_font("Helvetica", "B", 9)
        pdf.set_text_color(*C_GRAY)
        pdf.cell(w_label, 5, _safe(label) + ":", ln=False)
        pdf.set_font("Helvetica", "", 9)
        pdf.set_text_color(*C_DARK)
        pdf.multi_cell(W - w_label, 5, _safe(value))

    def _bullet(text: Any, indent: int = 4) -> None:
        pdf.set_font("Helvetica", "", 9)
        pdf.set_text_color(*C_DARK)
        pdf.set_x(LM + indent)
        pdf.cell(4, 5, "-", ln=False)
        pdf.multi_cell(W - indent - 4, 5, _safe(text))

    def _score_color(score: Optional[float]):
        if score is None: return C_GRAY
        if score >= 1.2:  return C_GREEN
        if score >= 0.8:  return C_BLUE
        if score >= 0.4:  return C_ORANGE
        return C_RED

    # ── Header bar ───────────────────────────────────────────────────────────
    pdf.set_fill_color(*C_BLUE)
    pdf.rect(0, 0, 210, 44, "F")

    full_name = _safe(f"{payload.get('surname','')} {payload.get('name','')}".strip() or "Member Profile")
    pdf.set_text_color(*C_WHITE)
    pdf.set_font("Helvetica", "B", 20)
    pdf.set_xy(LM, 8)
    pdf.cell(W, 10, full_name, ln=True)

    grade = payload.get("grade") or payload.get("role", "")
    ssd   = payload.get("ssd", "")
    sub   = " · ".join(p for p in [grade, ssd] if p)
    if sub:
        pdf.set_font("Helvetica", "", 11)
        pdf.set_text_color(200, 220, 255)
        pdf.set_xy(LM, 21)
        pdf.cell(W, 6, _safe(sub), ln=True)

    unit = payload.get("unit", "")
    if unit:
        pdf.set_font("Helvetica", "", 9)
        pdf.set_text_color(160, 185, 240)
        pdf.set_xy(LM, 30)
        pdf.cell(W, 6, _safe(unit), ln=True)

    pdf.set_text_color(*C_DARK)
    pdf.set_y(50)

    # ── Overview ─────────────────────────────────────────────────────────────
    _section("Overview")
    _kv("Unit",       unit)
    _kv("Scopus ID",  payload.get("scopus_id"))
    _kv("UNIGE ID",   payload.get("unige_id"))
    ret = (payload.get("retrieved_at") or "")[:10]
    if ret:
        _kv("Retrieved", ret)

    # ── Contact ──────────────────────────────────────────────────────────────
    contact = [(lbl, payload.get(key)) for lbl, key in [
        ("Email", "email"), ("Phone", "phone"),
        ("Website", "website"), ("Page", "page"),
    ] if payload.get(key)]
    location = payload.get("location") or []
    if contact or location:
        _section("Contact")
        for label, value in contact:
            _kv(label, value)
        for loc in location:
            if isinstance(loc, dict):
                parts = [
                    loc.get("building"),
                    f"Floor {loc.get('floor')}" if loc.get("floor") else None,
                    loc.get("room"),
                ]
                loc_str = ", ".join(p for p in parts if p)
                if loc_str:
                    _kv("Office", loc_str)

    # ── Career ───────────────────────────────────────────────────────────────
    career = payload.get("career") or []
    if career:
        _section("Career")
        for entry in career:
            if not isinstance(entry, dict):
                continue
            role  = entry.get("role") or ""
            from_ = str(entry.get("from") or "")[:10]
            to_   = str(entry.get("to")   or "")[:10] if entry.get("to") else "present"
            period = f"({from_} -> {to_})" if from_ else ""
            _bullet(f"{role}  {period}".strip())

    # ── Bibliometric Metrics ─────────────────────────────────────────────────
    metrics = payload.get("scopus_metrics") or []
    if metrics:
        _section("Bibliometric Metrics")
        col_w = [68, 28, 30, 26, 28]
        headers = ["Period", "Products", "Citations", "H-index", "Journals"]

        pdf.set_fill_color(*C_LIGHT)
        pdf.set_font("Helvetica", "B", 8)
        pdf.set_text_color(*C_GRAY)
        pdf.set_x(LM)
        for w, h in zip(col_w, headers):
            pdf.cell(w, 6, h, border="B", align="C", fill=True)
        pdf.ln()

        pdf.set_font("Helvetica", "", 8)
        for i, m in enumerate(metrics):
            if not isinstance(m, dict):
                continue
            pdf.set_fill_color(248, 249, 250) if i % 2 == 0 else pdf.set_fill_color(255, 255, 255)
            period = _safe(m.get("period") or "", 50)
            pdf.set_text_color(*C_DARK)
            pdf.set_x(LM)
            pdf.cell(col_w[0], 5, period, fill=True)
            for key, w in [
                ("total_products", col_w[1]),
                ("citations",      col_w[2]),
                ("hindex",         col_w[3]),
                ("journals",       col_w[4]),
            ]:
                val = m.get(key)
                txt = str(int(val)) if val is not None else "-"
                pdf.cell(w, 5, txt, align="C", fill=True)
            pdf.ln()
        pdf.ln(2)

    # ── Threshold Scores ─────────────────────────────────────────────────────
    scores_data = payload.get("scores") or {}
    if scores_data:
        _section("Threshold Scores  (D.M. 589/2018)")

        def _ratio_str(level: Dict) -> str:
            v = level.get("value")
            t = level.get("threshold")
            r = level.get("ratio")
            if v is None or t is None:
                return "N/D"
            ratio_s = f"{r:.2f}" if r is not None else "-"
            return f"{v} / {t} = {ratio_s}"

        for indicator, label in [
            ("articles",  "Articles"),
            ("citations", "Citations"),
            ("hindex",    "H-index"),
        ]:
            block = scores_data.get(indicator) or {}
            score = block.get("score")

            pdf.set_x(LM)
            pdf.set_font("Helvetica", "B", 9)
            pdf.set_text_color(*C_DARK)
            pdf.cell(32, 6, label, ln=False)

            score_txt = f"{score:.1f}" if score is not None else "N/D"
            pdf.set_font("Helvetica", "B", 9)
            pdf.set_text_color(*_score_color(score))
            pdf.cell(14, 6, score_txt, align="C", ln=False)

            pdf.set_font("Helvetica", "", 8)
            pdf.set_text_color(*C_GRAY)
            for level_key, level_name in [
                ("ii_fascia",    "Assoc. Prof."),
                ("i_fascia",     "Full Prof."),
                ("commissario",  "Commissioner"),
            ]:
                level = block.get(level_key) or {}
                ratio = _ratio_str(level)
                pdf.cell(26, 6, f"{level_name}:", ln=False)
                pdf.set_text_color(*C_DARK)
                pdf.cell(36, 6, ratio, ln=False)
                pdf.set_text_color(*C_GRAY)
            pdf.ln()

    # ── Responsibilities ──────────────────────────────────────────────────────
    responsibilities = payload.get("responsibilities") or []
    if responsibilities:
        _section("Responsibilities")
        for resp in responsibilities:
            if not isinstance(resp, dict):
                _bullet(str(resp))
                continue
            title  = resp.get("title") or resp.get("role") or ""
            unit_r = resp.get("unit") or ""
            from_  = str(resp.get("from") or "")[:10]
            to_    = str(resp.get("to")   or "")[:10] if resp.get("to") else "present"
            period = f"({from_} -> {to_})" if from_ else ""
            text_parts = [p for p in [title, unit_r, period] if p]
            _bullet("  ".join(text_parts))

    # ── Teaching ─────────────────────────────────────────────────────────────
    teaching = payload.get("teaching") or {}
    if teaching:
        _section("Teaching")
        for year in sorted(teaching.keys(), reverse=True):
            courses = teaching[year]
            n = len(courses)
            pdf.set_font("Helvetica", "B", 9)
            pdf.set_text_color(*C_BLUE)
            pdf.set_x(LM)
            pdf.cell(W, 5, f"{year}  ({n} course{'s' if n != 1 else ''})", ln=True)
            pdf.set_text_color(*C_DARK)
            for course in courses:
                if isinstance(course, dict):
                    name   = course.get("course") or course.get("name") or ""
                    degree = course.get("degree") or ""
                    text   = name + (f"  –  {degree}" if degree else "")
                else:
                    text = str(course)
                _bullet(text, indent=6)

    # ── Scopus publications ───────────────────────────────────────────────────
    scopus_products = payload.get("scopus_products") or []
    if scopus_products:
        _section(f"Publications – Scopus  ({len(scopus_products)})")
        for i, prod in enumerate(scopus_products, 1):
            if not isinstance(prod, dict):
                continue
            title  = _safe(prod.get("title") or "Untitled", 180)
            year   = prod.get("year") or ""
            venue  = _safe(prod.get("venue") or "", 80)
            cit    = prod.get("citations")
            type_  = _safe(prod.get("type") or prod.get("sub_type") or "", 30)

            pdf.set_x(LM)
            pdf.set_font("Helvetica", "B", 8)
            pdf.set_text_color(*C_DARK)
            pdf.multi_cell(W, 5, f"{i}.  {title}")

            meta: List[str] = []
            if year:    meta.append(str(year))
            if venue:   meta.append(venue)
            if cit is not None: meta.append(f"Cited: {int(cit)}")
            if type_:   meta.append(type_)
            if meta:
                pdf.set_x(LM + 6)
                pdf.set_font("Helvetica", "", 7)
                pdf.set_text_color(*C_GRAY)
                pdf.multi_cell(W - 6, 4, " · ".join(meta))
            pdf.ln(1)

    # ── IRIS publications ─────────────────────────────────────────────────────
    iris_products = payload.get("iris_products") or []
    if iris_products:
        if pdf.get_y() > 220:
            pdf.add_page()
        _section(f"Publications – IRIS  ({len(iris_products)})")
        for i, prod in enumerate(iris_products, 1):
            if not isinstance(prod, dict):
                continue
            title = _safe(prod.get("title") or prod.get("name") or "Untitled", 180)
            year  = prod.get("year") or ""
            type_ = _safe(prod.get("type") or "", 40)

            pdf.set_x(LM)
            pdf.set_font("Helvetica", "B", 8)
            pdf.set_text_color(*C_DARK)
            pdf.multi_cell(W, 5, f"{i}.  {title}")

            meta = [p for p in [str(year) if year else None, type_ or None] if p]
            if meta:
                pdf.set_x(LM + 6)
                pdf.set_font("Helvetica", "", 7)
                pdf.set_text_color(*C_GRAY)
                pdf.multi_cell(W - 6, 4, " · ".join(meta))
            pdf.ln(1)

    return bytes(pdf.output())


# ---------------------------------------------------------------------------
# Markdown export (kept for optional use)
# ---------------------------------------------------------------------------

class Exporter:
    def export(self, payloads: Sequence[Dict[str, Any]], run_dir: Path) -> Path:
        return self._export_markdown(payloads, run_dir)

    def _export_markdown(self, payloads: Sequence[Dict[str, Any]], run_dir: Path) -> Path:
        md_dir = run_dir / "markdown"
        md_dir.mkdir(parents=True, exist_ok=True)
        for index, payload in enumerate(payloads):
            filename = (
                f"{self._slugify(payload.get('surname', ''))}_"
                f"{self._slugify(payload.get('name', ''))}_"
                f"{payload.get('scopus_id', '') or index}.md"
            )
            md_path = md_dir / filename
            lines = self._build_markdown_lines(payload)
            self._write_markdown(md_path, lines)
        return md_dir

    def _build_markdown_lines(self, payload: Dict[str, Any]) -> List[str]:
        lines: List[str] = []
        full_name = f"{payload.get('surname', '')} {payload.get('name', '')}".strip()
        lines.append(f"# {full_name or 'Member Profile'}")
        lines.append("")

        overview_pairs = [
            ("Unit",         payload.get("unit")),
            ("Role",         payload.get("role")),
            ("Grade",        payload.get("grade")),
            ("SSD",          payload.get("ssd")),
            ("Scopus ID",    payload.get("scopus_id")),
            ("UNIGE ID",     payload.get("unige_id")),
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
            ("Email",   payload.get("email")),
            ("Phone",   payload.get("phone")),
            ("Website", payload.get("website")),
            ("Page",    payload.get("page")),
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
                for line in self._format_product_block(product):
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
        result = ", ".join(part for part in components if part)
        return result or "-"

    def _format_career(self, entry: Any) -> str:
        if not isinstance(entry, dict):
            return self._to_text(entry)
        parts = [
            self._format_value(entry.get("role")),
            self._format_range(entry.get("from"), entry.get("to")),
        ]
        return ", ".join(part for part in parts if part) or "-"

    def _format_teaching(self, entry: Any) -> str:
        if not isinstance(entry, dict):
            return self._to_text(entry)
        parts = [
            self._format_value(entry.get("course")),
            self._format_value(entry.get("degree")),
        ]
        return ", ".join(part for part in parts if part) or "-"

    def _format_metric(self, metric: Any) -> str:
        if not isinstance(metric, dict):
            return self._to_text(metric)
        parts: List[str] = []
        period = metric.get("period")
        if period:
            parts.append(str(period))
        for label, key in [
            ("Docs", "total_products"),
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
        fields = [
            ("Title",    self._format_value(product.get("title"))),
            ("Venue",    self._format_value(product.get("venue"))),
            ("Year",     self._format_value(product.get("year"))),
            ("Type",     self._format_value(product.get("type"))),
            ("Citations",self._format_value(product.get("citations"))),
        ]
        return [f"- **{l}:** {v}" for l, v in fields if v] or ["- -"]

    @staticmethod
    def _format_range(start: Any, end: Any) -> str:
        def _clean(value: Any) -> str:
            if not value:
                return ""
            text = str(value).strip()
            match = re.search(r"\d{4}-\d{2}-\d{2}", text)
            if match:
                return match.group(0)
            return text

        start_c = _clean(start)
        end_c   = _clean(end)
        if start_c and end_c:
            return f"{start_c} → {end_c}"
        return start_c or end_c

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
        letters = [c for c in text if c.isalpha()]
        if letters and all(c.isupper() for c in letters) and any(c.isspace() for c in text):
            text = text.lower()
            text = re.sub(r"\b([a-z])", lambda m: m.group(1).upper(), text)
        return text
