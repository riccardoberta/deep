from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from aggregate import Aggregate
from member import Member
from scopus import ScopusClient
from unige import UnigeClient


class Importer:
    def __init__(
        self,
        input_csv: str,
        year_windows: Iterable[int],
        *,
        sleep_seconds: float,
        fetch_scopus: bool,
        fetch_unige: bool,
        data_dir: Path | str = "data",
        logger: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.input_csv = input_csv
        self.year_windows = year_windows
        self.sleep_seconds = sleep_seconds
        self.fetch_scopus = fetch_scopus
        self.fetch_unige = fetch_unige
        self.data_dir = Path(data_dir)
        self.logger = logger

    def run(self) -> Tuple[Path, List[Dict[str, Any]], Dict[str, Any]]:
        members = Aggregate(self.input_csv).load_members()
        run_dir = self._next_run_directory(self.data_dir)
        source_dir = run_dir / "source"
        source_dir.mkdir(parents=True, exist_ok=True)

        scopus_client: Optional[ScopusClient] = None
        if self.fetch_scopus:
            try:
                scopus_client = ScopusClient(
                    year_windows=self.year_windows,
                    sleep_seconds=self.sleep_seconds,
                )
            except Exception as exc:  # pragma: no cover
                self._log(f"⚠️ Unable to initialise Scopus client: {exc}")
                scopus_client = None

        unige_map: Dict[str, Dict[str, Any]] = {}
        if self.fetch_unige:
            try:
                with UnigeClient() as client:
                    unige_map = client.get_people_overview()
            except Exception as exc:  # pragma: no cover
                self._log(f"⚠️ UNIGE overview fetch failed: {exc}")

        payloads: List[Dict[str, Any]] = []
        for member in members:
            self._log(f"🔍 Processing {member.name} {member.surname}")

            scopus_payload: Dict[str, Any] = {}
            if scopus_client:
                try:
                    scopus_payload = scopus_client.fetch_profile(member.scopus_id)
                except Exception as exc:  # pragma: no cover
                    self._log(f"⚠️ Scopus fetch failed for {member.scopus_id}: {exc}")

            unige_raw = unige_map.get(str(member.unige_id)) if member.unige_id else None

            payload = self._build_payload(member, scopus_payload, unige_raw)
            json_path = self._member_json_path(source_dir, member)
            with json_path.open("w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, ensure_ascii=False)

            payloads.append(payload)

        metadata = {
            "input_csv": str(Path(self.input_csv).resolve()),
            "year_windows": [int(value) for value in self.year_windows],
            "fetch_scopus": bool(self.fetch_scopus),
            "fetch_unige": bool(self.fetch_unige),
            "created_at": datetime.utcnow().isoformat(),
            "source_count": len(payloads),
        }
        metadata_path = run_dir / "metadata.json"
        metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")

        return run_dir, payloads, metadata

    def _log(self, message: str) -> None:
        if self.logger:
            self.logger(message)
        else:
            print(message)

    def _build_payload(
        self,
        member: Member,
        scopus_payload: Dict[str, Any],
        unige_raw: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        processed_unige = self._process_unige(unige_raw)

        scopus_metrics = scopus_payload.get("scopus_metrics", []) if scopus_payload else []
        scopus_products = scopus_payload.get("scopus_products", []) if scopus_payload else []
        retrieved_at = scopus_payload.get("retrieved_at") if scopus_payload else None

        payload = {
            "surname": member.surname,
            "name": member.name,
            "unit": member.unit,
            "email": processed_unige.get("email"),
            "phone": processed_unige.get("phone"),
            "page": processed_unige.get("page"),
            "website": processed_unige.get("website"),
            "unige_id": member.unige_id,
            "scopus_id": member.scopus_id,
            "role": processed_unige.get("role"),
            "grade": processed_unige.get("grade"),
            "ssd": processed_unige.get("ssd"),
            "location": processed_unige.get("location"),
            "career": processed_unige.get("career"),
            "responsibilities": processed_unige.get("responsibilities"),
            "teaching": processed_unige.get("teaching"),
            "scopus_metrics": scopus_metrics,
            "scopus_products": scopus_products,
            "retrieved_at": retrieved_at,
        }

        return self._normalize_whitespace(payload)

    @staticmethod
    def _normalize_whitespace(node: Any) -> Any:
        if isinstance(node, dict):
            return {key: Importer._normalize_whitespace(value) for key, value in node.items()}
        if isinstance(node, list):
            return [Importer._normalize_whitespace(element) for element in node]
        if isinstance(node, str):
            return re.sub(r"\s+", " ", node).strip()
        return node

    def _process_unige(self, data: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "email": None,
            "phone": None,
            "page": None,
            "website": None,
            "role": None,
            "grade": None,
            "ssd": None,
            "location": [],
            "career": [],
            "responsibilities": [],
            "teaching": {},
        }

        if not isinstance(data, dict):
            return result

        raw = dict(data)

        result["email"] = raw.get("email")
        result["phone"] = raw.get("telefono")
        result["page"] = raw.get("link_rubrica")
        result["website"] = raw.get("sitopersonale")
        result["role"] = raw.get("ruolo")
        result["grade"] = raw.get("inquadramento")

        codice_ssd = raw.get("codice_ssd")
        ssd_descr = raw.get("ssd")
        if codice_ssd and ssd_descr:
            result["ssd"] = f"{codice_ssd} ({ssd_descr})"
        elif codice_ssd:
            result["ssd"] = codice_ssd
        elif ssd_descr:
            result["ssd"] = ssd_descr

        result["location"] = self._process_locations(raw.get("localizzazione"))
        result["teaching"] = self._process_teaching(raw.get("Docenze"))
        result["career"] = self._process_career(raw.get("Storico ruoli"))
        result["responsibilities"] = self._process_responsibilities(raw.get("Incarichi"))

        return result

    @staticmethod
    def _process_locations(locations: Optional[Any]) -> List[Any]:
        if not isinstance(locations, list):
            return []
        cleaned: List[Any] = []
        for entry in locations:
            if not isinstance(entry, dict):
                cleaned.append(entry)
                continue
            item = dict(entry)
            edificio = item.pop("edificio", None)
            codice_edificio = item.pop("codice_edificio", None)
            if edificio or codice_edificio:
                parts = [part for part in [edificio, f"({codice_edificio})" if codice_edificio else None] if part]
                item["building"] = " ".join(parts)
            if "piano" in item:
                item["floor"] = item.pop("piano")
            if "codice_locale" in item:
                item["room"] = item.pop("codice_locale")
            for redundant in ["matricola", "sigla_piano", "locale", "superficie", "numero_locale"]:
                item.pop(redundant, None)
            cleaned.append(item)
        return cleaned

    @staticmethod
    def _process_teaching(entries: Optional[Any]) -> Dict[str, List[Any]]:
        if not isinstance(entries, list):
            return {}
        grouped: Dict[str, List[Any]] = {}
        for lesson in entries:
            if not isinstance(lesson, dict):
                grouped.setdefault("unknown", []).append(lesson)
                continue
            item = dict(lesson)
            anac_value = str(item.pop("anac", "unknown"))
            name = item.pop("nome_ins", None)
            course_code = item.pop("codice_ins", None)
            degree_class = item.pop("classe", None)
            degree_name = item.pop("nome_cla", None)
            item.pop("id_docenza", None)
            item.pop("codcla", None)
            item.pop("matricola", None)

            if course_code and name:
                item["course"] = f"{name} ({course_code})"
            elif course_code:
                item["course"] = f"({course_code})"
            elif name:
                item["course"] = name

            if degree_class and degree_name:
                item["degree"] = f"{degree_class} - {degree_name}"
            elif degree_name:
                item["degree"] = degree_name
            elif degree_class:
                item["degree"] = degree_class

            grouped.setdefault(anac_value, []).append(item)
        return grouped

    @staticmethod
    def _process_career(entries: Optional[Any]) -> List[Any]:
        if not entries:
            return []
        if not isinstance(entries, list):
            entries = [entries]
        cleaned: List[Any] = []
        for item in entries:
            if isinstance(item, dict):
                entry = dict(item)
                role = entry.pop("ruolo", entry.pop("Ruolo", None))
                start = entry.pop("dt_ini", entry.pop("dtIni", None))
                end = entry.pop("dt_fin", entry.pop("dtFin", None))
                entry.pop("matricola", None)
                entry.pop("aff_org", None)
                if role is not None:
                    entry["role"] = role
                if start is not None:
                    entry["from"] = start
                if end is not None:
                    entry["to"] = end
                cleaned.append(entry)
            elif item is not None:
                cleaned.append(item)
        return cleaned

    @staticmethod
    def _process_responsibilities(entries: Optional[Any]) -> List[Any]:
        if not entries:
            return []
        if not isinstance(entries, list):
            entries = [entries]
        cleaned: List[Any] = []
        for item in entries:
            if isinstance(item, dict):
                entry = dict(item)
                title = entry.pop("decofunzione", None)
                start = entry.pop("inizioincarico", None)
                end = entry.pop("termineincarico", None)
                unit = entry.pop("decostruttura", None)
                entry.pop("matricola", None)
                entry.pop("codestruttura", None)
                entry.pop("codefunzione", None)
                if title is not None:
                    entry["title"] = title
                if start is not None:
                    entry["from"] = start
                if end is not None:
                    entry["to"] = end
                if unit is not None:
                    entry["unit"] = unit
                cleaned.append(entry)
            elif item is not None:
                cleaned.append(item)
        return cleaned

    @staticmethod
    def _slugify(value: str, default: str = "unknown") -> str:
        cleaned = re.sub(r"\s+", "_", value.strip().lower())
        cleaned = re.sub(r"[^a-z0-9_]", "", cleaned)
        return cleaned or default

    def _next_run_directory(self, base: Path) -> Path:
        base.mkdir(parents=True, exist_ok=True)
        date_prefix = datetime.now().strftime("%Y_%m_%d")
        pattern = re.compile(rf"{date_prefix}_(\d+)$")
        indices = [
            int(match.group(1))
            for path in base.iterdir()
            if path.is_dir() and (match := pattern.match(path.name))
        ]
        run_number = max(indices) + 1 if indices else 1
        run_dir = base / f"{date_prefix}_{run_number}"
        run_dir.mkdir(parents=True, exist_ok=False)
        return run_dir

    def _member_json_path(self, base_dir: Path, member: Member) -> Path:
        surname_slug = self._slugify(member.surname)
        name_slug = self._slugify(member.name)
        return base_dir / f"{surname_slug}_{name_slug}_{member.scopus_id}.json"
