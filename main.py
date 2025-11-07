from __future__ import annotations

import json
import os
import queue
import re
import subprocess
import sys
import threading
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText
from typing import Any, Dict, Iterable, List

from dotenv import load_dotenv

from data_preparation import DataPreparation
from export import Exporter
from importer import Importer

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
        input_csv=os.getenv("INPUT_CSV", "./input/TEST.csv"),
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

        self.run_dir_var = tk.StringVar(value=self._default_run_dir())

        self.current_payloads: List[dict] | None = None
        self.current_run_dir: Path | None = None
        self.data_preparer = DataPreparation()
        self.exporter = Exporter()
        # Background workers push log messages through this queue to avoid UI freezes.
        self.import_log_queue: queue.Queue[str] = queue.Queue()
        self._import_log_scheduled = False
        self._import_running = False
        self._suspend_run_dir_trace = False
        self.member_payload_lookup: Dict[str, Dict[str, Any]] = {}
        self.magnifier_icon = self._build_magnifier_icon()

        self._build_ui()
        self.run_dir_var.trace_add("write", self._on_run_dir_var_changed)

    def _build_ui(self) -> None:
        notebook = ttk.Notebook(self.root)
        notebook.pack(fill="both", expand=True, padx=10, pady=5)

        import_tab = ttk.Frame(notebook)
        export_tab = ttk.Frame(notebook)
        notebook.add(import_tab, text="Importing")
        notebook.add(export_tab, text="Exploring")

        self._build_import_tab(import_tab)
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

    def _build_export_tab(self, parent: tk.Widget) -> None:
        """Compose the widgets used when exploring previously imported runs."""

        run_frame = tk.LabelFrame(parent, text="Working Folder", padx=10, pady=10)
        run_frame.pack(fill="x", padx=5, pady=5)
        tk.Entry(run_frame, textvariable=self.run_dir_var, width=50).grid(row=0, column=0, sticky="we", padx=5)
        tk.Button(run_frame, text="Select", command=self._browse_run_dir).grid(row=0, column=1, padx=5)
        tk.Button(run_frame, text="Open", command=self._open_run_dir).grid(row=0, column=2, padx=5)
        run_frame.columnconfigure(0, weight=1)

        export_actions = tk.Frame(parent)
        export_actions.pack(fill="x", padx=5, pady=5)
        tk.Button(export_actions, text="Start Export", command=self.start_export, width=15).pack(side="left", padx=5)

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

    def _default_run_dir(self) -> str:
        """Return the most recent run directory or an empty string if none exist."""

        base = self.data_dir
        if not base.is_dir():
            return ""

        pattern = re.compile(r"^(\d{4}_\d{2}_\d{2})_(\d+)$")
        candidates: List[tuple[str, int, Path]] = []
        for child in base.iterdir():
            if child.is_dir():
                match = pattern.match(child.name)
                if match:
                    date_token = match.group(1)
                    index = int(match.group(2))
                    candidates.append((date_token, index, child))

        if not candidates:
            return ""

        candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return str(candidates[0][2])

    def _browse_csv(self) -> None:
        """Prompt the user for a CSV file."""

        file_path = filedialog.askopenfilename(title="Select input CSV", filetypes=[("CSV files", "*.csv"), ("All files", "*.*")])
        if file_path:
            self.input_var.set(file_path)

    def _browse_run_dir(self) -> None:
        """Prompt the user for a working folder."""

        directory = filedialog.askdirectory(title="Select working folder", initialdir=str(self.data_dir))
        if directory:
            self.run_dir_var.set(directory)

    def _open_run_dir(self) -> None:
        """Open the currently selected working folder using the platform file explorer."""

        directory = self.run_dir_var.get()
        if not directory:
            messagebox.showwarning("Working folder", "Select a valid working folder first.")
            return

        path = Path(directory)
        if not path.is_dir():
            messagebox.showwarning("Working folder", "Select a valid working folder first.")
            return

        try:
            if sys.platform.startswith("win"):
                os.startfile(str(path))  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.run(["open", str(path)], check=False)
            else:
                subprocess.run(["xdg-open", str(path)], check=False)
        except Exception as exc:  # pragma: no cover - GUI feedback only
            messagebox.showerror("Working folder", f"Unable to open folder: {exc}")

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
            run_dir, payloads = importer.run()
        except Exception as exc:
            message = f"⚠️ Import failed: {exc}"
            self._enqueue_import_log(message)
            self.root.after(0, lambda: self._on_import_failure("Import failed", str(exc)))
            return

        try:
            summary_path = self.data_preparer.prepare(payloads, run_dir, input_csv)
        except Exception as exc:
            message = f"⚠️ Data preparation failed: {exc}"
            self._enqueue_import_log(message)
            self.root.after(0, lambda: self._on_import_failure("Data preparation failed", str(exc)))
            return

        self.root.after(
            0,
            lambda: self._on_import_success(run_dir, payloads, summary_path),
        )

    def _on_import_failure(self, title: str, message: str) -> None:
        """Restore UI state after a background failure."""

        messagebox.showerror(title, message)
        self._finalize_import()

    def _on_import_success(
        self,
        run_dir: Path,
        payloads: List[Dict[str, Any]],
        summary_path: Path,
    ) -> None:
        """Update UI state and log messages after a successful import cycle."""

        self.current_run_dir = run_dir
        self.current_payloads = payloads
        self._suspend_run_dir_trace = True
        self.run_dir_var.set(str(run_dir))
        self._suspend_run_dir_trace = False
        self._update_member_table(payloads)
        self._enqueue_import_log(f"Summary CSV: {summary_path}")
        self._enqueue_import_log("✅ Import completed")
        self._finalize_import()

    def _finalize_import(self) -> None:
        """Re-enable the import controls once the background work stops."""

        self._import_running = False
        self.import_button.config(state="normal")

    def _on_run_dir_var_changed(self, *_: Any) -> None:
        """Reload member data when the user selects a different working folder."""

        if self._suspend_run_dir_trace or self._import_running:
            return
        directory = self.run_dir_var.get().strip()
        if not directory:
            self._clear_member_table()
            return
        path = Path(directory)
        if not path.is_dir():
            self._clear_member_table()
            return
        try:
            payloads = self._load_payloads_from_json(path)
        except Exception:
            self._clear_member_table()
            return
        self.current_run_dir = path
        self.current_payloads = payloads
        self._update_member_table(payloads)

    def start_export(self) -> None:
        run_dir_str = self.run_dir_var.get().strip()
        if not run_dir_str:
            messagebox.showwarning("Missing run directory", "Select a run directory first.")
            return

        run_dir = Path(run_dir_str)
        if not run_dir.is_dir():
            messagebox.showerror("Run directory not found", f"{run_dir} does not exist.")
            return

        if self.current_run_dir and run_dir.resolve() == self.current_run_dir.resolve() and self.current_payloads is not None:
            payloads = self.current_payloads
        else:
            try:
                payloads = self._load_payloads_from_json(run_dir)
            except Exception as exc:
                messagebox.showerror("Export failed", str(exc))
                self._append_export_log(f"⚠️ Export failed: {exc}")
                return
            self.current_run_dir = run_dir
            self.current_payloads = payloads

        self._update_member_table(payloads)
        try:
            markdown_dir = self.exporter.export(payloads, run_dir)
        except Exception as exc:
            messagebox.showerror("Export failed", str(exc))
            self._append_export_log(f"⚠️ Export failed: {exc}")
            return

        self._append_export_log(f"✅ Export completed. Markdown directory: {markdown_dir}")

    def _load_payloads_from_json(self, run_dir: Path) -> List[Dict[str, Any]]:
        payloads: List[Dict[str, Any]] = []
        for path in sorted(run_dir.glob("*.json")):
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
