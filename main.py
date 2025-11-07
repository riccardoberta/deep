from __future__ import annotations

import json
import os
import queue
import re
import threading
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText
from typing import Any, Dict, Iterable, List, Optional

from dotenv import load_dotenv

from collaborations import CollaborationBuilder
from data_preparation import DataPreparation
from export import Exporter
from importer import Importer

try:  # pragma: no cover - optional visualisation deps
    import networkx as nx
except Exception:  # pragma: no cover
    nx = None

try:  # pragma: no cover - optional visualisation deps
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
    from matplotlib.figure import Figure
except Exception:  # pragma: no cover
    FigureCanvasTkAgg = None
    Figure = None

load_dotenv()


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y"}


@dataclass(frozen=True)
class AppSettings:
    """Snapshot of the user-configurable defaults loaded from the environment."""

    input_csv: str
    year_windows: str
    sleep_seconds: float
    fetch_scopus: bool
    fetch_unige: bool
    data_dir: Path


def _load_settings() -> AppSettings:
    """Read defaults from .env/environment variables and normalise types."""

    sleep_value = os.getenv("SLEEP_SECONDS", "3.0")
    try:
        sleep_seconds = float(sleep_value)
    except (TypeError, ValueError):
        sleep_seconds = 3.0

    data_dir = Path(os.getenv("DATA_DIR", "data")).expanduser()

    return AppSettings(
        input_csv=os.getenv("INPUT_CSV", "./input/DITEN.xlsx"),
        year_windows=os.getenv("YEAR_WINDOWS", "15,10,5"),
        sleep_seconds=sleep_seconds,
        fetch_scopus=_env_bool("FETCH_SCOPUS", True),
        fetch_unige=_env_bool("FETCH_UNIGE", True),
        data_dir=data_dir,
    )


class Application:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("DEEP: DITEN Evaluation and Evidence Platform")
        self.root.geometry("720x520")

        self.settings = _load_settings()

        self.input_var = tk.StringVar(value=self.settings.input_csv)
        self.year_windows_var = tk.StringVar(value=self.settings.year_windows)
        self.fetch_scopus_var = tk.BooleanVar(value=self.settings.fetch_scopus)
        self.fetch_unige_var = tk.BooleanVar(value=self.settings.fetch_unige)

        self.sleep_seconds = self.settings.sleep_seconds
        self.data_dir = self.settings.data_dir

        self.icon_image = self._build_icon()
        if self.icon_image is not None:
            try:
                self.root.iconphoto(True, self.icon_image)
            except tk.TclError:
                self.icon_image = None

        self.current_payloads: List[dict] | None = None
        self.current_run_dir: Path | None = self._latest_run_dir_path()
        self.current_metadata: Dict[str, Any] | None = None
        self.data_preparer = DataPreparation()
        self.exporter = Exporter()
        self.run_label_var = tk.StringVar(value=self._format_run_label(self.current_run_dir))
        # Background workers push log messages through this queue to avoid UI freezes.
        self.import_log_queue: queue.Queue[str] = queue.Queue()
        self._import_log_scheduled = False
        self._import_running = False
        self.member_payload_lookup: Dict[str, Dict[str, Any]] = {}
        self.magnifier_icon = self._build_magnifier_icon()
        self._collaboration_cache: Dict[str, Dict[str, Any]] = {}
        self._collaboration_positions: Dict[str, Dict[str, tuple[float, float]]] = {}

        self._build_ui()
        self._refresh_latest_run()

    def _build_ui(self) -> None:
        notebook = ttk.Notebook(self.root)
        notebook.pack(fill="both", expand=True, padx=10, pady=5)

        import_tab = ttk.Frame(notebook)
        elaborating_tab = ttk.Frame(notebook)
        export_tab = ttk.Frame(notebook)
        notebook.add(import_tab, text="Importing")
        notebook.add(elaborating_tab, text="Elaborating")
        notebook.add(export_tab, text="Exploring")

        self._build_import_tab(import_tab)
        self._build_elaborating_tab(elaborating_tab)
        self._build_export_tab(export_tab)

    def _build_import_tab(self, parent: tk.Widget) -> None:
        """Compose the widgets that drive the import workflow."""

        settings_frame = tk.LabelFrame(parent, text="Settings", padx=10, pady=10)
        settings_frame.pack(fill="x", padx=5, pady=5)

        tk.Label(settings_frame, text="Input file").grid(row=0, column=0, sticky="w")
        tk.Entry(settings_frame, textvariable=self.input_var, width=50).grid(row=0, column=1, sticky="we", padx=5)
        tk.Button(settings_frame, text="Browse", command=self._browse_csv).grid(row=0, column=2, padx=5)

        tk.Label(settings_frame, text="Year Windows (comma separated)").grid(row=1, column=0, sticky="w")
        tk.Entry(settings_frame, textvariable=self.year_windows_var, width=50).grid(row=1, column=1, sticky="we", padx=5)

        tk.Checkbutton(settings_frame, text="Fetch Scopus", variable=self.fetch_scopus_var).grid(row=2, column=0, sticky="w", pady=5)
        tk.Checkbutton(settings_frame, text="Fetch UNIGE", variable=self.fetch_unige_var).grid(row=2, column=1, sticky="w", pady=5)
        settings_frame.columnconfigure(1, weight=1)

        import_actions = tk.Frame(parent)
        import_actions.pack(fill="x", padx=5, pady=5)
        self.import_button = tk.Button(import_actions, text="Start Import", command=self.start_import, width=15)
        self.import_button.pack(side="left", padx=5)

        import_log_frame = tk.LabelFrame(parent, text="Import Log", padx=5, pady=5)
        import_log_frame.pack(fill="both", expand=True, padx=5, pady=5)
        self.import_log_text = ScrolledText(import_log_frame, wrap="word", height=12)
        self.import_log_text.pack(fill="both", expand=True)

    def _build_elaborating_tab(self, parent: tk.Widget) -> None:
        """Compose the controls used to build derived artefacts from the latest run."""

        actions_frame = tk.LabelFrame(parent, text="Elaboration Tasks", padx=10, pady=10)
        actions_frame.pack(fill="x", padx=5, pady=5)

        tk.Button(
            actions_frame,
            text="Prepare Results CSV",
            command=self.prepare_results_csv,
            width=22,
        ).pack(side="left", padx=5)

        tk.Button(
            actions_frame,
            text="Build Collaboration Graph",
            command=self.build_collaboration_graph,
            width=26,
        ).pack(side="left", padx=5)

        log_frame = tk.LabelFrame(parent, text="Elaboration Log", padx=5, pady=5)
        log_frame.pack(fill="both", expand=True, padx=5, pady=5)
        self.elaboration_log_text = ScrolledText(log_frame, wrap="word", height=12)
        self.elaboration_log_text.pack(fill="both", expand=True)

    def _build_export_tab(self, parent: tk.Widget) -> None:
        """Compose the widgets used when exploring previously imported runs."""

        run_frame = tk.LabelFrame(parent, text="Current Run", padx=10, pady=10)
        run_frame.pack(fill="x", padx=5, pady=5)
        tk.Label(run_frame, textvariable=self.run_label_var, anchor="w").grid(row=0, column=0, sticky="we", padx=5)
        tk.Button(run_frame, text="Reload Latest Run", command=self._refresh_latest_run, width=20).grid(row=0, column=1, padx=5)
        run_frame.columnconfigure(0, weight=1)

        export_actions = tk.Frame(parent)
        export_actions.pack(fill="x", padx=5, pady=5)
        tk.Button(export_actions, text="Start Export", command=self.start_export, width=15).pack(side="left", padx=5)
        tk.Button(
            export_actions,
            text="View Collaborations",
            command=self._open_collaborations_window,
            width=18,
        ).pack(side="left", padx=5)

        members_frame = tk.LabelFrame(parent, text="Members", padx=5, pady=5)
        members_frame.pack(fill="both", expand=True, padx=5, pady=5)
        columns = ("name", "surname", "unit", "ssd", "role")
        self.member_tree = ttk.Treeview(
            members_frame,
            columns=columns,
            show="tree headings",
            selectmode="browse",
        )
        self.member_tree.heading("#0", text="")
        self.member_tree.column("#0", width=32, stretch=False)
        self.member_tree.heading("name", text="Name")
        self.member_tree.heading("surname", text="Surname")
        self.member_tree.heading("unit", text="Unit")
        self.member_tree.heading("ssd", text="SSD")
        self.member_tree.heading("role", text="Role")
        self.member_tree.column("name", width=120, stretch=True)
        self.member_tree.column("surname", width=140, stretch=True)
        self.member_tree.column("unit", width=80, stretch=True)
        self.member_tree.column("ssd", width=120, stretch=True)
        self.member_tree.column("role", width=180, stretch=True)

        tree_scroll = ttk.Scrollbar(members_frame, orient="vertical", command=self.member_tree.yview)
        self.member_tree.configure(yscrollcommand=tree_scroll.set)
        self.member_tree.grid(row=0, column=0, sticky="nsew")
        tree_scroll.grid(row=0, column=1, sticky="ns")
        members_frame.columnconfigure(0, weight=1)
        members_frame.rowconfigure(0, weight=1)
        self.member_tree.bind("<Button-1>", self._on_member_tree_click)
        self.member_tree.bind("<Double-1>", self._on_member_tree_activate)
        self.member_tree.bind("<Return>", self._on_member_tree_activate)

        export_log_frame = tk.LabelFrame(parent, text="Exploration Log", padx=5, pady=5)
        export_log_frame.pack(fill="both", expand=True, padx=5, pady=5)
        self.export_log_text = ScrolledText(export_log_frame, wrap="word", height=10)
        self.export_log_text.pack(fill="both", expand=True)

    def _browse_csv(self) -> None:
        """Prompt the user for a CSV file."""

        file_path = filedialog.askopenfilename(title="Select input CSV", filetypes=[("CSV files", "*.csv"), ("All files", "*.*")])
        if file_path:
            self.input_var.set(file_path)

    def start_import(self) -> None:
        if self._import_running:
            return

        try:
            year_windows = self._parse_year_windows(self.year_windows_var.get())
        except ValueError as exc:
            messagebox.showerror("Invalid input", str(exc))
            return

        input_csv = self.input_var.get()
        fetch_scopus = self.fetch_scopus_var.get()
        fetch_unige = self.fetch_unige_var.get()

        self._import_running = True
        self.import_button.config(state="disabled")
        self._enqueue_import_log("🚀 Starting import...")

        worker = threading.Thread(
            target=self._run_import,
            args=(input_csv, list(year_windows), fetch_scopus, fetch_unige),
            daemon=True,
        )
        worker.start()

    def _run_import(
        self,
        input_csv: str,
        year_windows: Iterable[int],
        fetch_scopus: bool,
        fetch_unige: bool,
    ) -> None:
        """Worker thread that fetches external data and writes per-member JSON."""

        importer = Importer(
            input_csv=input_csv,
            year_windows=year_windows,
            sleep_seconds=self.sleep_seconds,
            fetch_scopus=fetch_scopus,
            fetch_unige=fetch_unige,
            data_dir=self.data_dir,
            logger=self._enqueue_import_log,
        )

        try:
            run_dir, payloads, metadata = importer.run()
        except Exception as exc:
            message = f"⚠️ Import failed: {exc}"
            self._enqueue_import_log(message)
            self.root.after(0, lambda: self._on_import_failure("Import failed", str(exc)))
            return

        self.root.after(
            0,
            lambda: self._on_import_success(run_dir, payloads, metadata),
        )

    def _on_import_failure(self, title: str, message: str) -> None:
        """Restore UI state after a background failure."""

        messagebox.showerror(title, message)
        self._finalize_import()

    def _on_import_success(
        self,
        run_dir: Path,
        payloads: List[Dict[str, Any]],
        metadata: Dict[str, Any],
    ) -> None:
        """Update UI state and log messages after a successful import cycle."""

        self.current_run_dir = run_dir
        self.current_payloads = payloads
        self.current_metadata = metadata
        self.run_label_var.set(self._format_run_label(run_dir))
        self._update_member_table(payloads)
        self._enqueue_import_log(f"Raw payloads stored under: {run_dir / 'source'}")
        self._enqueue_import_log("Use the Elaborating tab to prepare CSVs or graphs.")
        self._enqueue_import_log("✅ Import completed")
        self._finalize_import()

    def _finalize_import(self) -> None:
        """Re-enable the import controls once the background work stops."""

        self._import_running = False
        self.import_button.config(state="normal")

    def start_export(self) -> None:
        run_dir = self._require_current_run_dir()
        if run_dir is None:
            return

        if not self._ensure_run_payloads():
            return

        try:
            markdown_dir = self.exporter.export(self.current_payloads, run_dir)
        except Exception as exc:
            messagebox.showerror("Export failed", str(exc))
            self._append_export_log(f"⚠️ Export failed: {exc}")
            return

        self._append_export_log(f"✅ Export completed. Markdown directory: {markdown_dir}")

    def _load_payloads_from_json(self, run_dir: Path) -> List[Dict[str, Any]]:
        payloads: List[Dict[str, Any]] = []
        source_dir = run_dir / "source"
        if not source_dir.is_dir():
            raise RuntimeError(f"No source directory found under {run_dir}.")

        for path in sorted(source_dir.glob("*.json")):
            try:
                payloads.append(json.loads(path.read_text(encoding="utf-8")))
            except Exception as exc:
                raise RuntimeError(f"Failed to read {path.name}: {exc}") from exc
        if not payloads:
            raise RuntimeError("No JSON files found in the selected run directory.")
        return payloads

    @staticmethod
    def _parse_year_windows(value: str) -> Iterable[int]:
        parts = [part.strip() for part in value.split(",") if part.strip()]
        if not parts:
            raise ValueError("Year windows cannot be empty.")
        try:
            return [int(part) for part in parts]
        except ValueError as exc:
            raise ValueError("Year windows must be integers separated by commas.") from exc

    def _enqueue_import_log(self, message: str) -> None:
        """Schedule a message to appear in the import log without blocking the UI."""

        self.import_log_queue.put(message)
        self._schedule_import_log_flush()

    def _schedule_import_log_flush(self) -> None:
        """Ensure a flush is queued exactly once per Tk iteration."""

        if not self._import_log_scheduled:
            self._import_log_scheduled = True
            self.root.after(0, self._flush_import_log_queue)

    def _flush_import_log_queue(self) -> None:
        """Drain pending log messages and append them to the widget."""

        self._import_log_scheduled = False
        while True:
            try:
                message = self.import_log_queue.get_nowait()
            except queue.Empty:
                break
            self._append_import_log(message)
        if not self.import_log_queue.empty():
            self._schedule_import_log_flush()

    def _append_import_log(self, message: str) -> None:
        """Append a single message to the import log widget."""

        self.import_log_text.insert("end", f"{message}\n")
        self.import_log_text.see("end")
        self.root.update_idletasks()

    def _append_export_log(self, message: str) -> None:
        """Append a single message to the exploration log widget."""

        self.export_log_text.insert("end", f"{message}\n")
        self.export_log_text.see("end")
        self.root.update_idletasks()

    def _append_elaboration_log(self, message: str) -> None:
        """Append a single message to the elaboration log widget."""

        self.elaboration_log_text.insert("end", f"{message}\n")
        self.elaboration_log_text.see("end")
        self.root.update_idletasks()

    def prepare_results_csv(self) -> None:
        """Generate the aggregated CSV for the latest run."""

        run_dir = self._require_current_run_dir()
        if run_dir is None:
            return
        if not self._ensure_run_payloads():
            return

        metadata = self._ensure_metadata()
        input_csv = metadata.get("input_csv") or self.input_var.get()
        if not input_csv:
            messagebox.showwarning("Missing input CSV", "Unable to determine the source CSV for this run.")
            return

        try:
            summary_path = self.data_preparer.prepare(self.current_payloads, run_dir, input_csv)
        except Exception as exc:
            messagebox.showerror("CSV preparation failed", str(exc))
            self._append_elaboration_log(f"⚠️ CSV preparation failed: {exc}")
            return

        self._append_elaboration_log(f"✅ Results CSV saved to {summary_path}")

    def build_collaboration_graph(self) -> None:
        """Generate collaboration JSON/GraphML files for the latest run."""

        run_dir = self._require_current_run_dir()
        if run_dir is None:
            return
        if not self._ensure_run_payloads():
            return

        metadata = self._ensure_metadata()
        windows = metadata.get("year_windows")
        if not windows:
            try:
                windows = list(self._parse_year_windows(self.year_windows_var.get()))
            except ValueError:
                windows = []

        builder = CollaborationBuilder(windows or [], logger=self._append_elaboration_log)
        try:
            result = builder.build(self.current_payloads, run_dir)
        except Exception as exc:
            messagebox.showerror("Collaboration build failed", str(exc))
            self._append_elaboration_log(f"⚠️ Collaboration build failed: {exc}")
            return

        cache_key = self._collaboration_cache_key(run_dir)
        self._collaboration_cache.pop(cache_key, None)
        self._collaboration_positions.pop(cache_key, None)

        json_path = result.get("json")
        graph_path = result.get("graphml")
        if json_path:
            self._append_elaboration_log(f"✅ Collaboration JSON saved to {json_path}")
        if graph_path:
            self._append_elaboration_log(f"✅ GraphML saved to {graph_path}")
        else:
            self._append_elaboration_log("ℹ️ Install networkx to export GraphML files.")

    def _open_collaborations_window(self) -> None:
        """Open a toplevel window with an interactive collaboration graph."""

        if nx is None or FigureCanvasTkAgg is None or Figure is None:
            messagebox.showinfo(
                "Dependency missing",
                "Install networkx and matplotlib to view the collaboration graph.",
            )
            return

        run_dir = self._require_current_run_dir()
        if run_dir is None:
            return

        json_path = run_dir / "elaborations" / "collaborations.json"
        if not json_path.exists():
            messagebox.showinfo(
                "Collaborations unavailable",
                "No collaboration data found for this run. Build the collaboration graph first.",
            )
            return

        try:
            data = self._load_collaboration_data(run_dir)
        except Exception as exc:
            messagebox.showerror("Unable to load collaborations", str(exc))
            return

        window_options = data.get("windows") or [{"key": "overall", "label": "Overall"}]
        label_to_key = {option["label"]: option["key"] for option in window_options}
        labels = list(label_to_key.keys())
        selected_label = tk.StringVar(value=labels[0])

        dialog = tk.Toplevel(self.root)
        dialog.title("Collaboration Graph")
        dialog.geometry("920x680")
        dialog.transient(self.root)

        control_frame = tk.Frame(dialog)
        control_frame.pack(fill="x", padx=10, pady=5)
        ttk.Label(control_frame, text="Time window:").pack(side="left")
        window_selector = ttk.Combobox(
            control_frame,
            state="readonly",
            textvariable=selected_label,
            values=labels,
            width=30,
        )
        window_selector.pack(side="left", padx=6)

        figure = Figure(figsize=(7.5, 5.6), dpi=100)
        axis = figure.add_subplot(111)
        canvas = FigureCanvasTkAgg(figure, master=dialog)
        canvas.get_tk_widget().pack(fill="both", expand=True, padx=10, pady=5)

        def refresh_graph(*_: Any) -> None:
            label = selected_label.get()
            window_key = label_to_key.get(label, window_options[0]["key"])
            self._draw_collaboration_graph(axis, canvas, data, window_key, label, run_dir)

        window_selector.bind("<<ComboboxSelected>>", refresh_graph)
        refresh_graph()

    def _draw_collaboration_graph(
        self,
        axis: Any,
        canvas: FigureCanvasTkAgg,
        data: Dict[str, Any],
        window_key: str,
        window_label: str,
        run_dir: Path,
    ) -> None:
        axis.clear()
        nodes = data.get("nodes") or []
        edges = data.get("edges") or []

        graph = nx.Graph()
        for node in nodes:
            h_index_map = node.get("h_index", {})
            value = self._safe_float(h_index_map.get(window_key))
            if not value:
                value = self._safe_float(h_index_map.get("overall"))
            graph.add_node(
                node["id"],
                label=node.get("label") or node["id"],
                h_index=value,
            )

        for edge in edges:
            weight_map = edge.get("weight", {})
            weight = self._safe_float(weight_map.get(window_key))
            if weight <= 0:
                continue
            graph.add_edge(edge["source"], edge["target"], weight=weight)

        if graph.number_of_nodes() == 0:
            axis.text(0.5, 0.5, "No members available", ha="center", va="center")
            axis.axis("off")
            canvas.draw_idle()
            return

        cache_key = self._collaboration_cache_key(run_dir)
        positions = self._collaboration_positions.get(cache_key)
        if positions is None or not positions:
            positions = self._build_collaboration_positions(run_dir, data)

        node_sizes = [max(200.0, attr.get("h_index", 0) * 60.0) for _, attr in graph.nodes(data=True)]
        edge_widths = [max(0.5, attr.get("weight", 1) * 0.6) for _, _, attr in graph.edges(data=True)]

        nx.draw_networkx_edges(graph, positions, ax=axis, width=edge_widths, alpha=0.55, edge_color="#9AA7BF")
        nx.draw_networkx_nodes(
            graph,
            positions,
            ax=axis,
            node_size=node_sizes,
            node_color="#4F81BD",
            alpha=0.9,
            linewidths=0.6,
            edgecolors="#1F3D60",
        )
        nx.draw_networkx_labels(graph, positions, ax=axis, font_size=8)

        axis.set_title(f"Collaborations – {window_label}")
        axis.axis("off")
        canvas.draw_idle()

    def _build_collaboration_positions(self, run_dir: Path, data: Dict[str, Any]) -> Dict[str, tuple[float, float]]:
        cache_key = self._collaboration_cache_key(run_dir)
        positions = self._collaboration_positions.get(cache_key)
        if positions is not None:
            return positions

        base_graph = nx.Graph()
        for node in data.get("nodes") or []:
            base_graph.add_node(node["id"])
        for edge in data.get("edges") or []:
            weight_map = edge.get("weight", {})
            weight = sum(self._safe_float(value) for value in weight_map.values())
            if weight <= 0:
                continue
            base_graph.add_edge(edge["source"], edge["target"], weight=weight)

        if base_graph.number_of_nodes() == 0:
            positions = {}
        else:
            positions = nx.spring_layout(base_graph, weight="weight", seed=42)
        self._collaboration_positions[cache_key] = positions
        return positions

    def _load_collaboration_data(self, run_dir: Path) -> Dict[str, Any]:
        cache_key = self._collaboration_cache_key(run_dir)
        cached = self._collaboration_cache.get(cache_key)
        if cached is not None:
            return cached
        path = run_dir / "elaborations" / "collaborations.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        if not data.get("windows"):
            data["windows"] = [{"key": "overall", "label": "Overall"}]
        self._collaboration_cache[cache_key] = data
        return data

    def _collaboration_cache_key(self, run_dir: Path) -> str:
        return str(run_dir.resolve())

    @staticmethod
    def _safe_float(value: Any) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    def _latest_run_dir_path(self) -> Optional[Path]:
        base = self.data_dir
        if not base.is_dir():
            return None
        pattern = re.compile(r"^(\d{4}_\d{2}_\d{2})_(\d+)$")
        candidates: List[tuple[str, int, Path]] = []
        for child in base.iterdir():
            if child.is_dir():
                match = pattern.match(child.name)
                if match:
                    candidates.append((match.group(1), int(match.group(2)), child))
        if not candidates:
            return None
        candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return candidates[0][2]

    def _format_run_label(self, run_dir: Optional[Path]) -> str:
        return f"Latest run: {run_dir}" if run_dir else "Latest run: none"

    def _refresh_latest_run(self) -> None:
        latest = self._latest_run_dir_path()
        if latest is None:
            self.current_run_dir = None
            self.current_payloads = None
            self.current_metadata = None
            self.run_label_var.set(self._format_run_label(None))
            self._clear_member_table()
            return

        if self.current_run_dir and self.current_run_dir.resolve() == latest.resolve():
            self.run_label_var.set(self._format_run_label(latest))
            if self.current_payloads is None:
                self._reload_current_run_payloads()
            return

        self.current_run_dir = latest
        self.current_payloads = None
        self.current_metadata = None
        self.run_label_var.set(self._format_run_label(latest))
        self._reload_current_run_payloads()

    def _reload_current_run_payloads(self) -> None:
        run_dir = self.current_run_dir
        if run_dir is None:
            self._clear_member_table()
            self.run_label_var.set(self._format_run_label(None))
            return
        try:
            payloads = self._load_payloads_from_json(run_dir)
        except Exception as exc:
            self._append_export_log(f"⚠️ Unable to load run: {exc}")
            messagebox.showerror("Run load failed", str(exc))
            self.current_payloads = None
            self.current_metadata = None
            self._clear_member_table()
            return
        self.current_payloads = payloads
        self.current_metadata = self._load_run_metadata(run_dir)
        self._update_member_table(payloads)
        self.run_label_var.set(self._format_run_label(run_dir))

    def _load_run_metadata(self, run_dir: Path) -> Dict[str, Any]:
        path = run_dir / "metadata.json"
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _require_current_run_dir(self) -> Optional[Path]:
        run_dir = self.current_run_dir
        if run_dir is None:
            messagebox.showwarning("No run available", "Run an import before continuing.")
            return None
        return run_dir

    def _ensure_run_payloads(self) -> bool:
        run_dir = self.current_run_dir
        if run_dir is None:
            messagebox.showwarning("No run available", "Run an import before continuing.")
            return False
        if self.current_payloads is not None:
            return True
        try:
            self.current_payloads = self._load_payloads_from_json(run_dir)
        except Exception as exc:
            messagebox.showerror("Unable to load run", str(exc))
            return False
        if self.current_metadata is None:
            self.current_metadata = self._load_run_metadata(run_dir)
        self._update_member_table(self.current_payloads)
        return True

    def _ensure_metadata(self) -> Dict[str, Any]:
        run_dir = self.current_run_dir
        if run_dir is None:
            return {}
        if self.current_metadata is None:
            self.current_metadata = self._load_run_metadata(run_dir)
        return self.current_metadata or {}

    def _update_member_table(self, payloads: Iterable[Dict[str, Any]]) -> None:
        """Populate the member treeview with the supplied payloads sorted by surname."""

        if not hasattr(self, "member_tree"):
            return
        self._clear_member_table()
        sorted_payloads = sorted(
            payloads,
            key=lambda item: (
                str(item.get("surname", "")).lower(),
                str(item.get("name", "")).lower(),
            ),
        )
        for index, payload in enumerate(sorted_payloads):
            item_id = str(payload.get("scopus_id") or payload.get("unige_id") or f"member_{index}")
            while item_id in self.member_payload_lookup:
                item_id = f"{item_id}_{index}"
            self.member_payload_lookup[item_id] = payload
            values = (
                payload.get("name", ""),
                payload.get("surname", ""),
                payload.get("unit", ""),
                payload.get("ssd", ""),
                payload.get("role", ""),
            )
            self.member_tree.insert(
                "",
                "end",
                iid=item_id,
                text="",
                image=self.magnifier_icon if self.magnifier_icon else "",
                values=values,
            )

    def _clear_member_table(self) -> None:
        """Remove every row from the member tree."""

        if not hasattr(self, "member_tree"):
            return
        for item in self.member_tree.get_children():
            self.member_tree.delete(item)
        self.member_payload_lookup.clear()

    def _on_member_tree_click(self, event: tk.Event) -> str | None:
        """Intercept clicks on the icon column so we can show the detail window."""

        row_id = self.member_tree.identify_row(event.y)
        column = self.member_tree.identify_column(event.x)
        if column == "#0" and row_id:
            self._open_member_details(row_id)
            return "break"
        return None

    def _on_member_tree_activate(self, event: tk.Event) -> None:
        """Handle double-click/Return key by displaying the selected member."""

        selection = self.member_tree.selection()
        if selection:
            self._open_member_details(selection[0])

    def _open_member_details(self, item_id: str) -> None:
        """Locate the payload for a tree item and open it in a detail window."""

        payload = self.member_payload_lookup.get(item_id)
        if not payload:
            return
        self._show_member_details(payload)

    def _show_member_details(self, payload: Dict[str, Any]) -> None:
        """Render the full JSON payload in a simple read-only window."""

        window = tk.Toplevel(self.root)
        window.title(f"{payload.get('surname', '')} {payload.get('name', '')}".strip() or "Member details")
        window.geometry("520x640")
        text = ScrolledText(window, wrap="word")
        text.pack(fill="both", expand=True, padx=10, pady=10)
        text.insert("1.0", json.dumps(payload, indent=2, ensure_ascii=False))
        text.configure(state="disabled")
        text.see("1.0")

    def _build_icon(self) -> tk.PhotoImage | None:
        """Load the application logo from disk, falling back to a generated square."""

        logo_path = Path(__file__).resolve().parent / "assets" / "logo.png"
        if logo_path.exists():
            try:
                return tk.PhotoImage(file=str(logo_path))
            except tk.TclError:
                pass  # fall back to generated icon

        try:
            icon = tk.PhotoImage(width=16, height=16)
        except tk.TclError:
            return None
        icon.put("#0d47a1", to=(0, 0, 16, 16))
        icon.put("#ffffff", to=(4, 4, 12, 12))
        icon.put("#0d47a1", to=(6, 6, 10, 10))
        return icon

    def _build_magnifier_icon(self) -> tk.PhotoImage | None:
        """Draw a minimalist magnifying-glass icon for the member tree."""

        try:
            image = tk.PhotoImage(width=16, height=16)
        except tk.TclError:
            return None
        image.put("#ffffff", to=(0, 0, 16, 16))
        # Lens
        for x in range(4, 12):
            for y in range(4, 12):
                if (x - 7) ** 2 + (y - 7) ** 2 <= 9:
                    image.put("#0d47a1", (x, y))
        # Handle
        handle_coords = [(10, 10), (11, 11), (12, 12), (13, 13)]
        for coord in handle_coords:
            image.put("#0d47a1", coord)
        return image

def main() -> None:
    root = tk.Tk()
    app = Application(root)
    root.mainloop()

if __name__ == "__main__":
    main()
