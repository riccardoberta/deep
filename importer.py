from __future__ import annotations

import json
import re
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from aggregate import Aggregate
from member import Member
from scopus import ScopusClient
from thresholds import ThresholdRow, compute_scores, load_thresholds
from unige import UnigeClient


class Importer:
    def __init__(
        self,
        input_workbook: str,
        year_windows: Iterable[int],
        *,
        sleep_seconds: float,
        fetch_scopus: bool,
        fetch_unige: bool,
        fetch_iris: bool = False,
        data_dir: Path | str = "data",
        logger: Optional[Callable[[str], None]] = None,
        should_stop: Optional[Callable[[], bool]] = None,
    ) -> None:
        self.input_workbook = input_workbook
        self.year_windows = year_windows
        self.sleep_seconds = sleep_seconds
        self.fetch_scopus = fetch_scopus
        self.fetch_unige = fetch_unige
        self.fetch_iris = fetch_iris
        self.data_dir = Path(data_dir)
        self.logger = logger
        self.should_stop = should_stop

    def run(self) -> Tuple[Path, List[Dict[str, Any]], Dict[str, Any]]:
        members = Aggregate(self.input_workbook).load_members()
        thresholds = load_thresholds()
        run_dir = self._next_run_directory(self.data_dir, self.input_workbook)
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

        unige_client: Optional[UnigeClient] = None
        unige_map: Dict[str, Dict[str, Any]] = {}
        if self.fetch_unige or self.fetch_iris:
            try:
                unige_client = UnigeClient()
                if self.fetch_unige:
                    unige_map = unige_client.get_people_overview()
            except Exception as exc:  # pragma: no cover
                self._log(f"⚠️ UNIGE overview/IRIS init failed: {exc}")
                if unige_client:
                    unige_client.close()
                unige_client = None

        # Load any payloads already fetched in a previous run today
        existing: Dict[str, Dict[str, Any]] = {}
        for p in source_dir.glob("*.json"):
            try:
                existing[p.name] = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                pass

        payloads: List[Dict[str, Any]] = []
        total = len(members)
        aborted = False
        fetch_count = 0
        loop_start = time.monotonic()
        try:
            for index, member in enumerate(members, 1):
                if self.should_stop and self.should_stop():
                    self._log("⏹️ Import cancelled by user.")
                    aborted = True
                    break

                json_path = self._member_json_path(source_dir, member)
                prior = existing.get(json_path.name)
                if self._is_complete(prior, self.fetch_scopus, self.fetch_unige):
                    self._log(f"⏭️ Skipping {member.name} {member.surname} ({index}/{total}) — already imported")
                    payloads.append(prior)  # type: ignore[arg-type]
                    continue

                fetch_count += 1
                if fetch_count > 1:
                    elapsed = time.monotonic() - loop_start
                    avg = elapsed / (fetch_count - 1)
                    remaining = avg * (total - index + 1)
                    eta_str = f" · ETA {int(remaining // 60):02d}:{int(remaining % 60):02d}"
                else:
                    eta_str = ""
                self._log(f"🔍 Processing {member.name} {member.surname} ({index}/{total}{eta_str})")

                scopus_payload: Dict[str, Any] = {}
                scopus_ok = not self.fetch_scopus
                if scopus_client:
                    try:
                        scopus_payload = scopus_client.fetch_profile(member.scopus_id)
                        scopus_ok = True
                    except Exception as exc:  # pragma: no cover
                        self._log(f"⚠️ Scopus fetch failed for {member.scopus_id}: {exc}")

                canonical_unige_id = self._sanitize_unige_id(member.unige_id)
                unige_raw = self._lookup_unige_entry(unige_map, canonical_unige_id)
                unige_ok = not self.fetch_unige or unige_client is not None
                iris_products: List[Dict[str, Any]] = []
                if self.fetch_iris and unige_client and canonical_unige_id:
                    try:
                        iris_products = unige_client.get_member_iris_products(canonical_unige_id)
                    except Exception as exc:  # pragma: no cover
                        self._log(f"⚠️ IRIS fetch failed for {member.unige_id}: {exc}")

                payload = self._build_payload(
                    member,
                    canonical_unige_id,
                    scopus_payload,
                    unige_raw,
                    iris_products,
                    thresholds,
                    fetch_status={"scopus_ok": scopus_ok, "unige_ok": unige_ok, "scores_ok": None},
                )
                with json_path.open("w", encoding="utf-8") as handle:
                    json.dump(payload, handle, indent=2, ensure_ascii=False)

                payloads.append(payload)
        finally:
            if unige_client:
                unige_client.close()

        if aborted:
            self._cleanup_run_directory(run_dir)
            return run_dir, payloads, {}

        metadata = {
            "input_file": Path(self.input_workbook).name,
            "year_windows": [int(value) for value in self.year_windows],
            "fetch_scopus": bool(self.fetch_scopus),
            "fetch_unige": bool(self.fetch_unige),
            "fetch_iris": bool(self.fetch_iris),
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

    @staticmethod
    def _is_complete(
        payload: Optional[Dict[str, Any]],
        fetch_scopus: bool,
        fetch_unige: bool,
    ) -> bool:
        """Return True if a previously saved payload already has all requested data."""
        if payload is None:
            return False
        status = payload.get("_fetch_status", {})
        if fetch_scopus and not status.get("scopus_ok", False):
            return False
        if fetch_unige and not status.get("unige_ok", False):
            return False
        if not status.get("scores_ok", True):
            return False
        return True

    def _build_payload(
        self,
        member: Member,
        normalized_unige_id: Optional[str],
        scopus_payload: Dict[str, Any],
        unige_raw: Optional[Dict[str, Any]],
        iris_products: Optional[List[Dict[str, Any]]],
        thresholds: Optional[Dict[str, ThresholdRow]] = None,
        fetch_status: Optional[Dict[str, bool]] = None,
    ) -> Dict[str, Any]:
        processed_unige = self._process_unige(unige_raw)
        processed_iris = self._process_iris_products(iris_products)

        scopus_metrics = scopus_payload.get("scopus_metrics", []) if scopus_payload else []
        scopus_products = scopus_payload.get("scopus_products", []) if scopus_payload else []
        retrieved_at = scopus_payload.get("retrieved_at") if scopus_payload else None

        ssd_for_scoring = processed_unige.get("ssd") or member.ssd
        scores = compute_scores(ssd_for_scoring, scopus_metrics, thresholds or {})

        payload = {
            "surname": member.surname,
            "name": member.name,
            "unit": member.unit,
            "email": processed_unige.get("email"),
            "phone": processed_unige.get("phone"),
            "page": processed_unige.get("page"),
            "website": processed_unige.get("website"),
            "unige_id": normalized_unige_id or member.unige_id,
            "scopus_id": member.scopus_id,
            "role": processed_unige.get("role"),
            "grade": processed_unige.get("grade") or member.grade,
            "ssd": processed_unige.get("ssd") or member.ssd,
            "ssd_name": processed_unige.get("ssd_name"),
            "location": processed_unige.get("location"),
            "career": processed_unige.get("career"),
            "responsibilities": processed_unige.get("responsibilities"),
            "teaching": processed_unige.get("teaching"),
            "scores": scores,
            "scopus_metrics": scopus_metrics,
            "scopus_products": scopus_products,
            "iris_products": processed_iris,
            "retrieved_at": retrieved_at,
            "_fetch_status": {
                **(fetch_status or {"scopus_ok": True, "unige_ok": True}),
                "scores_ok": any(
                    scores.get(ind, {}).get("score") is not None
                    for ind in ("articles", "citations", "hindex")
                ),
            },
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
            "ssd_name": None,
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

        codice_ssd = (raw.get("codice_ssd") or "").strip() or None
        ssd_descr  = re.sub(r"^[\s\-(]+|[\s\-)]+$", "", raw.get("ssd") or "").strip() or None
        result["ssd"]      = codice_ssd
        result["ssd_name"] = ssd_descr

        result["location"] = self._process_locations(raw.get("localizzazione"))
        result["teaching"] = self._process_teaching(raw.get("Docenze"))
        result["career"] = self._process_career(raw.get("Storico ruoli"))
        result["responsibilities"] = self._process_responsibilities(raw.get("Incarichi"))

        return result

    def _process_iris_products(self, products: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
        if not isinstance(products, list):
            return []

        cleaned: List[Dict[str, Any]] = []

        def pop_path(record: Dict[str, Any], path: str) -> Any:
            if path in record:
                return record.pop(path)
            parts = path.split(".")
            current: Any = record
            parents: List[Tuple[Dict[str, Any], str]] = []
            for key in parts[:-1]:
                if not isinstance(current, dict):
                    return None
                parents.append((current, key))
                current = current.get(key)
                if current is None:
                    return None
            if not isinstance(current, dict):
                return None
            value = current.pop(parts[-1], None)
            # cleanup empty dicts
            while parents:
                parent, key = parents.pop()
                child = parent.get(key)
                if isinstance(child, dict) and not child:
                    parent.pop(key, None)
            return value

        for item in products:
            if not isinstance(item, dict):
                continue
            entry = {key: value for key, value in item.items()}

            mapping = {
                "search.legacyid_i": "legacy_id",
                "dateIssued.year": "year",
                "dc.type.miur": "miur_type",
                "dc.title": "title",
                "dc.identifier.scopus": "scopus_id",
                "dc.identifier.doi": "doi",
                "dc.identifier.isi": "isi_id",
            }

            for path, target in mapping.items():
                value = pop_path(entry, path)
                if value is not None:
                    entry[target] = value

            if "collection" in entry:
                entry["type"] = entry.pop("collection")

            pop_path(entry, "miur.stato")
            entry.pop("stato", None)
            entry.pop("person", None)
            entry.pop("serie", None)
            pop_path(entry, "dc.subject.keywords")
            entry.pop("handle", None)
            entry.pop("journal", None)

            citation_count = entry.pop("citationCount", None)
            if isinstance(citation_count, dict):
                if citation_count.get("isi") is not None:
                    entry["citations_isi"] = citation_count.get("isi")
                if citation_count.get("scopus") is not None:
                    entry["citations_scopus"] = citation_count.get("scopus")

            remove_keys = {
                "descriptionAbstractAll",
                "score",
                "citation",
                "dateIssued",
                "language",
                "fulltextPresence",
                "AllFulltextPresence",
                "lastModified",
                "dc.date.issued_dt",
            }
            for key in remove_keys:
                pop_path(entry, key)
                entry.pop(key, None)

            cleaned.append(entry)

        return cleaned

    @staticmethod
    def _sanitize_unige_id(identifier: Optional[str]) -> Optional[str]:
        if identifier is None:
            return None
        value = str(identifier).strip()
        if not value:
            return None
        if value.endswith(".0"):
            candidate = value[:-2]
            if candidate:
                return candidate
        return value

    @staticmethod
    def _lookup_unige_entry(unige_map: Dict[str, Dict[str, Any]], identifier: Optional[str]) -> Optional[Dict[str, Any]]:
        if not identifier:
            return None
        candidates = [identifier]
        stripped = identifier.lstrip("0")
        if stripped and stripped not in candidates:
            candidates.append(stripped)
        try:
            numeric = str(int(stripped or identifier))
            if numeric not in candidates:
                candidates.append(numeric)
        except ValueError:
            pass
        for key in candidates:
            if key and key in unige_map:
                return unige_map[key]
        return None
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

    def _next_run_directory(self, base: Path, input_workbook: str) -> Path:
        base.mkdir(parents=True, exist_ok=True)
        date_prefix = datetime.now().strftime("%Y_%m_%d")
        stem = Path(input_workbook).stem.strip()
        file_part = re.sub(r'[\s<>:"/\\|?*\x00-\x1f]+', "_", stem)
        file_part = file_part.strip("_") or "import"
        run_dir = base / f"{date_prefix}_{file_part}"
        run_dir.mkdir(parents=True, exist_ok=True)
        return run_dir

    def _member_json_path(self, base_dir: Path, member: Member) -> Path:
        surname_slug = self._slugify(member.surname)
        name_slug = self._slugify(member.name)
        return base_dir / f"{surname_slug}_{name_slug}_{member.scopus_id}.json"

    @staticmethod
    def _cleanup_run_directory(run_dir: Path) -> None:
        try:
            if run_dir.exists():
                shutil.rmtree(run_dir)
        except Exception:
            pass
