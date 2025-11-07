from __future__ import annotations

import os
import time
from configparser import ConfigParser
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from dotenv import load_dotenv
from pybliometrics import init
from pybliometrics.scopus import AuthorRetrieval, ScopusSearch, SerialTitleISSN
from pybliometrics.utils import create_config

YEAR_WINDOWS_DEFAULT = (15, 10, 5)


@dataclass
class ScopusSummary:
    total_docs: int
    total_citations: int
    h_index: int
    per_window: Dict[int, Dict[str, int]]


class ScopusClient:
    """Fetch Scopus metrics and publication data for members."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        email: Optional[str] = None,
        year_windows: Optional[Iterable[int]] = None,
        *,
        sleep_seconds: float = 3.0,
        current_year: Optional[int] = None,
    ) -> None:
        load_dotenv()
        resolved_api_key = api_key or os.getenv("SCOPUS_API_KEY")
        resolved_email = email or os.getenv("SCOPUS_EMAIL")
        if not resolved_api_key or not resolved_email:
            raise ValueError("SCOPUS_API_KEY and SCOPUS_EMAIL must be provided")

        self.api_key = resolved_api_key
        self.email = resolved_email
        self.year_windows = list(year_windows or YEAR_WINDOWS_DEFAULT)
        self.sleep_seconds = sleep_seconds
        self.current_year = current_year or datetime.now().year

        self.config_path = Path("~/.pybliometrics/config.ini").expanduser()
        self._ensure_config()
        init(config_path=self.config_path, keys=[self.api_key])

        self._source_cache: Dict[str, Dict[str, object]] = {}

    def fetch_profile(self, scopus_id: str) -> Dict[str, object]:
        scopus_id = str(scopus_id).strip()
        if not scopus_id:
            raise ValueError("Scopus ID must not be empty.")

        author = AuthorRetrieval(scopus_id)
        search = ScopusSearch(f"AU-ID({scopus_id})")
        docs = search.results or []

        total_docs = len(docs)
        total_citations = author.citation_count
        h_index = author.h_index

        stats = {
            window: {
                "docs": 0,
                "citations": 0,
                "journals": 0,
                "conferences": 0,
                "citation_counts": [],
            }
            for window in self.year_windows
        }

        papers: List[Dict[str, object]] = []
        for pub in docs:
            try:
                cover_date = getattr(pub, "coverDate", None)
                year = None
                if cover_date and len(cover_date) >= 4:
                    year = int(cover_date[:4])
                cited = int(pub.citedby_count) if pub.citedby_count else 0
                publication_type = getattr(pub, "aggregationType", None) or "Unknown"
                publication_type_lower = publication_type.lower()

                if year is not None:
                    for window in self.year_windows:
                        if year >= self.current_year - window:
                            window_stats = stats[window]
                            window_stats["docs"] += 1
                            window_stats["citations"] += cited
                            window_stats["citation_counts"].append(cited)
                            if publication_type_lower == "journal":
                                window_stats["journals"] += 1
                            elif "conference" in publication_type_lower:
                                window_stats["conferences"] += 1

                metadata = self._fetch_serial_metadata(
                    getattr(pub, "issn", None),
                    getattr(pub, "eIssn", None),
                    getattr(pub, "source_id", None),
                )

                author_names = getattr(pub, "author_names", None)
                if isinstance(author_names, str):
                    formatted_authors = "; ".join(name.strip().replace(",", "") for name in author_names.split(";"))
                else:
                    formatted_authors = author_names

                papers.append(
                    {
                        "scopus_id": getattr(pub, "identifier", None) or getattr(pub, "eid", None),
                        "title": getattr(pub, "title", None),
                        "doi": getattr(pub, "doi", None),
                        "venue": getattr(pub, "publicationName", None),
                        "type": publication_type,
                        "sub_type": getattr(pub, "subtypeDescription", None),
                        "year": year,
                        "volume": getattr(pub, "volume", None),
                        "issue_id": getattr(pub, "issueIdentifier", None),
                        "pages": getattr(pub, "pageRange", None),
                        "issn": getattr(pub, "issn", None),
                        "eIssn": getattr(pub, "eIssn", None),
                        "source_id": getattr(pub, "source_id", None),
                        "authors": formatted_authors,
                        "author_ids": getattr(pub, "author_ids", None),
                        "authorAffiliationIds": getattr(pub, "author_afids", None),
                        "corresponding": getattr(pub, "creator", None),
                        "keywords": getattr(pub, "authkeywords", None),
                        "abstract": getattr(pub, "description", None),
                        "citations": cited,
                        "quartile": self._filter_quartiles(metadata.get("quartileHistory"), year),
                    }
                )
            except Exception:
                continue

        window_h_indexes = {
            window: self._compute_h_index(stats[window]["citation_counts"])
            for window in self.year_windows
        }

        metrics = [
            {
                "period": "absolute",
                "hindex": h_index,
                "total_products": total_docs,
                "journals": sum(
                    1 for p in papers if (p.get("type") or "").lower() == "journal"
                ),
                "conferences": sum(
                    1 for p in papers if "conference" in (p.get("type") or "").lower()
                ),
                "citations": total_citations,
                "start": 1900,
                "end": self.current_year,
            }
        ]

        for window in self.year_windows:
            metrics.append(
                {
                    "period": f"{window:02d} years ({self.current_year - window}-{self.current_year})",
                    "hindex": window_h_indexes[window],
                    "total_products": stats[window]["docs"],
                    "journals": stats[window]["journals"],
                    "conferences": stats[window]["conferences"],
                    "citations": stats[window]["citations"],
                    "start": self.current_year - window,
                    "end": self.current_year,
                }
            )

        summary = ScopusSummary(
            total_docs=total_docs,
            total_citations=total_citations,
            h_index=h_index,
            per_window={
                window: {
                    "docs": stats[window]["docs"],
                    "citations": stats[window]["citations"],
                    "journals": stats[window]["journals"],
                    "conferences": stats[window]["conferences"],
                    "h_index": window_h_indexes[window],
                }
                for window in self.year_windows
            },
        )

        time.sleep(self.sleep_seconds)

        return {
            "retrieved_at": datetime.utcnow().isoformat(),
            "scopus_metrics": metrics,
            "scopus_products": papers,
        }

    @staticmethod
    def _compute_h_index(citation_counts: Iterable[int]) -> int:
        counts = sorted((c or 0 for c in citation_counts), reverse=True)
        h_value = 0
        for position, count in enumerate(counts, start=1):
            if count >= position:
                h_value = position
            else:
                break
        return h_value

    def _filter_quartiles(
        self,
        quartile_history: Optional[List[Dict[str, object]]],
        publication_year: Optional[int],
    ) -> Optional[List[Dict[str, object]]]:
        if not quartile_history:
            return None

        target_years = {self.current_year}
        if publication_year is not None:
            target_years.add(publication_year)

        filtered = [entry for entry in quartile_history if entry.get("year") in target_years]
        return filtered or None

    def _fetch_serial_metadata(
        self,
        issn: Optional[str],
        eissn: Optional[str],
        source_id: Optional[str],
    ) -> Dict[str, object]:
        identifiers = [
            identifier
            for identifier in (issn, eissn, source_id)
            if identifier and isinstance(identifier, str)
        ]
        for identifier in identifiers:
            if identifier in self._source_cache:
                return self._source_cache[identifier]

        primary_identifier = next(
            (identifier for identifier in (issn, eissn) if identifier), None
        )
        if not primary_identifier:
            return {}

        try:
            serial = SerialTitleISSN(primary_identifier, view="CITESCORE")
        except Exception:
            for identifier in identifiers:
                self._source_cache[identifier] = {}
            return {}

        subjects: List[str] = []
        code_to_area: Dict[int, str] = {}
        for area in getattr(serial, "subject_area", []) or []:
            name = getattr(area, "area", None) or getattr(area, "abbreviation", None)
            code = getattr(area, "code", None)
            if isinstance(code, str):
                try:
                    code = int(code)
                except ValueError:
                    code = None
            if name and name not in subjects:
                subjects.append(name)
            if code is not None and name:
                code_to_area[int(code)] = name

        quartile_history: List[Dict[str, object]] = []
        cite_scores = getattr(serial, "citescoreyearinfolist", None) or []
        for entry in cite_scores:
            year = getattr(entry, "year", None)
            if isinstance(year, str):
                try:
                    year = int(year)
                except ValueError:
                    year = None

            subjects_for_year: List[Dict[str, object]] = []
            for subject in getattr(entry, "rank", []) or []:
                subject_code = getattr(subject, "subjectcode", None)
                if isinstance(subject_code, str):
                    try:
                        subject_code = int(subject_code)
                    except ValueError:
                        subject_code = None
                percentile = getattr(subject, "percentile", None)
                if isinstance(percentile, str):
                    try:
                        percentile = float(percentile)
                    except ValueError:
                        percentile = None
                rank_position = getattr(subject, "rank", None)
                if isinstance(rank_position, str):
                    try:
                        rank_position = int(rank_position)
                    except ValueError:
                        rank_position = None
                subjects_for_year.append(
                    {
                        "subject": code_to_area.get(subject_code),
                        "percentile": percentile,
                        "rank": rank_position,
                        "quartile": self._quartile_from_percentile(percentile),
                    }
                )
            if subjects_for_year:
                quartile_history.append({"year": year, "subjects": subjects_for_year})

        metadata = {
            "quartileHistory": quartile_history or None,
        }
        for identifier in identifiers or [primary_identifier]:
            self._source_cache[identifier] = metadata
        return metadata

    @staticmethod
    def _quartile_from_percentile(percentile: Optional[float]) -> Optional[str]:
        if percentile is None:
            return None
        try:
            value = float(percentile)
        except (TypeError, ValueError):
            return None
        if value >= 75:
            return "Q1"
        if value >= 50:
            return "Q2"
        if value >= 25:
            return "Q3"
        if value >= 0:
            return "Q4"
        return None

    def _ensure_config(self) -> None:
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        config = ConfigParser()
        config.optionxform = str
        needs_rebuild = True

        if self.config_path.exists():
            config.read(self.config_path)
            required_sections = {"Directories", "Authentication", "Requests"}
            needs_rebuild = not required_sections.issubset(config.sections())

        if needs_rebuild:
            create_config(config_dir=self.config_path, keys=[self.api_key])
            config.read(self.config_path)

        if not config.has_section("Authentication"):
            config.add_section("Authentication")
        config.set("Authentication", "APIKey", self.api_key)
        config.set("Authentication", "view", "STANDARD")
        config.set("Authentication", "Email", self.email)

        with self.config_path.open("w") as cfg:
            config.write(cfg)
