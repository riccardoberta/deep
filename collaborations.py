from __future__ import annotations

import itertools
import json
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

try:  # pragma: no cover - optional dependency
    import networkx as nx
except Exception:  # pragma: no cover
    nx = None


class CollaborationBuilder:
    def __init__(
        self,
        year_windows: Iterable[int],
        *,
        logger: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.year_windows = sorted(
            {int(window) for window in year_windows if int(window) > 0},
            reverse=True,
        )
        self.logger = logger

    def build(
        self,
        payloads: Sequence[Dict[str, Any]],
        run_dir: Path,
        *,
        output_dir: Optional[Path] = None,
    ) -> Dict[str, Optional[Path]]:
        payload_list = list(payloads)
        if not payload_list:
            raise ValueError("No payloads available to build collaborations.")

        base_dir = output_dir or (run_dir / "elaborations")
        base_dir.mkdir(parents=True, exist_ok=True)

        window_keys = [{"key": "overall", "label": "Overall", "years": None}]
        window_key_map: Dict[Optional[int], str] = {None: "overall"}
        for window in self.year_windows:
            key = f"w{window}"
            window_key_map[window] = key
            window_keys.append({"key": key, "label": f"Last {window} years", "years": window})

        nodes = self._build_nodes(payload_list, window_key_map)
        edges = self._build_edges(payload_list, self.year_windows, window_key_map)
        data = {
            "generated_at": datetime.utcnow().isoformat(),
            "windows": window_keys,
            "nodes": list(nodes.values()),
            "edges": edges,
        }

        json_path = base_dir / "collaborations.json"
        json_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        self._log(f"Collaboration JSON saved to {json_path}")

        graph_path: Optional[Path] = None
        if nx is not None:
            graph_path = base_dir / "collaborations.graphml"
            self._write_graphml(graph_path, data)
            self._log(f"GraphML saved to {graph_path}")
        else:  # pragma: no cover - optional dependency
            self._log("networkx not installed: skipping GraphML export.")

        return {"json": json_path, "graphml": graph_path}

    def _build_nodes(
        self,
        payloads: List[Dict[str, Any]],
        window_key_map: Dict[Optional[int], str],
    ) -> Dict[str, Dict[str, Any]]:
        nodes: Dict[str, Dict[str, Any]] = {}
        for index, payload in enumerate(payloads):
            member_id = self._member_identifier(payload, index)
            name = payload.get("name", "")
            surname = payload.get("surname", "")
            label = " ".join(part for part in [name, surname] if part).strip() or member_id
            metrics = payload.get("scopus_metrics") or []

            hindex_map: Dict[str, int] = {}
            for window, key in window_key_map.items():
                hindex_map[key] = self._extract_hindex(metrics, window)

            nodes[member_id] = {
                "id": member_id,
                "label": label,
                "name": name,
                "surname": surname,
                "unit": payload.get("unit"),
                "scopus_id": payload.get("scopus_id"),
                "unige_id": payload.get("unige_id"),
                "h_index": hindex_map,
            }
        return nodes

    def _build_edges(
        self,
        payloads: List[Dict[str, Any]],
        windows: List[int],
        window_key_map: Dict[Optional[int], str],
    ) -> List[Dict[str, Any]]:
        current_year = datetime.utcnow().year
        product_map: Dict[str, Dict[str, Any]] = {}

        for index, payload in enumerate(payloads):
            member_id = self._member_identifier(payload, index)
            for product in payload.get("scopus_products") or []:
                product_id = self._product_identifier(product)
                if not product_id:
                    continue
                year = self._safe_int(product.get("year"))
                entry = product_map.setdefault(product_id, {})
                existing_year = entry.get("year")
                entry["year"] = year if year is not None else existing_year
                member_years = entry.setdefault("members", {})
                member_years[member_id] = entry["year"]

        pairs: Dict[Tuple[str, str], Dict[str, Any]] = {}
        for data in product_map.values():
            members = data.get("members") or {}
            if len(members) < 2:
                continue
            year = self._safe_int(data.get("year"))
            for left, right in itertools.combinations(sorted(members.keys()), 2):
                stats = pairs.setdefault(
                    (left, right),
                    {"weight_overall": 0, "window_counts": defaultdict(int)},
                )
                stats["weight_overall"] += 1
                if year is not None:
                    for window in windows:
                        if year >= current_year - window:
                            stats["window_counts"][window] += 1

        edges: List[Dict[str, Any]] = []
        for (left, right), stats in pairs.items():
            weights = {
                window_key_map[None]: stats["weight_overall"],
            }
            for window in windows:
                key = window_key_map[window]
                weights[key] = stats["window_counts"].get(window, 0)
            edges.append(
                {
                    "source": left,
                    "target": right,
                    "weight": weights,
                }
            )
        return edges

    def _write_graphml(self, path: Path, data: Dict[str, Any]) -> None:
        if nx is None:  # pragma: no cover
            return

        graph = nx.Graph()
        for node in data.get("nodes", []):
            attributes = {
                "label": node.get("label", ""),
                "name": node.get("name", ""),
                "surname": node.get("surname", ""),
                "unit": node.get("unit", ""),
                "scopus_id": node.get("scopus_id", ""),
                "unige_id": node.get("unige_id", ""),
            }
            for key, value in node.get("h_index", {}).items():
                attributes[f"h_index_{key}"] = value
            graph.add_node(node["id"], **attributes)

        for edge in data.get("edges", []):
            attributes = {}
            for key, value in edge.get("weight", {}).items():
                attributes[f"weight_{key}"] = value
            attributes.setdefault("weight", edge.get("weight", {}).get("overall", 0))
            graph.add_edge(edge["source"], edge["target"], **attributes)

        graph.graph["windows"] = json.dumps(data.get("windows", []))
        nx.write_graphml(graph, path)

    def _extract_hindex(self, metrics: List[Dict[str, Any]], window: Optional[int]) -> int:
        if not metrics:
            return 0
        if window is None:
            for metric in metrics:
                period = (metric.get("period") or "").strip().lower()
                if period == "absolute":
                    return self._safe_int(metric.get("hindex")) or 0
            return 0

        prefix = f"{window:02d}"
        for metric in metrics:
            period = (metric.get("period") or "").strip().lower()
            if period.startswith(prefix):
                return self._safe_int(metric.get("hindex")) or 0
        return 0

    def _member_identifier(self, payload: Dict[str, Any], index: int) -> str:
        for key in ("scopus_id", "unige_id"):
            value = payload.get(key)
            if value:
                return str(value)
        surname = self._slugify(str(payload.get("surname", "unknown")))
        name = self._slugify(str(payload.get("name", index)))
        return f"{surname}_{name}_{index}"

    @staticmethod
    def _product_identifier(product: Dict[str, Any]) -> Optional[str]:
        if not isinstance(product, dict):
            return None
        for key in ("scopus_id", "eid", "identifier", "id"):
            value = product.get(key)
            if value:
                return str(value)
        title = product.get("title")
        if title:
            return re.sub(r"\s+", "_", title.strip())
        return None

    @staticmethod
    def _safe_int(value: Any) -> Optional[int]:
        if value in (None, ""):
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _slugify(value: str, default: str = "unknown") -> str:
        cleaned = re.sub(r"\s+", "_", value.strip().lower())
        cleaned = re.sub(r"[^a-z0-9_]", "", cleaned)
        return cleaned or default

    def _log(self, message: str) -> None:
        if self.logger:
            self.logger(message)
