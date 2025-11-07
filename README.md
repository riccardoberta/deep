# DEEP – DITEN Evaluation and Evidence Platform

DEEP is a desktop utility used by the DITEN department to gather, normalise, and explore evidence about faculty members. It connects to Scopus and UNIGE web services, enriches the department roster with bibliometric metrics plus institutional data, and produces ready-to-share CSV/Markdown summaries for evaluation exercises.

## Key features
- Imports the department roster from a CSV (samples available under `input/`).
- Pulls Scopus metrics (H-index, publications, citations per rolling window) via `pybliometrics`.
- Pulls UNIGE people data (roles, locations, teaching, responsibilities) via the official REST API.
- Stores every run under timestamped folders in `data/`, including per-member JSON payloads.
- Keeps raw Scopus/UNIGE payloads in `data/<run>/source/` and lets you generate CSVs/graphs later from the Elaborating tab.
- Generates two exploration artifacts: a tabular CSV (for spreadsheets) and Markdown dossiers.
- Ships with a Tkinter GUI that keeps network work on background threads to avoid UI freezes.
- Can build a co-authorship graph (GraphML + JSON) on demand so you can explore collaborations per time window.

## Requirements
- Python 3.10+ with Tkinter support (installed by default on most macOS/Linux distributions).
- `pip install` access to the following packages: `pybliometrics`, `python-dotenv`, `requests`, `networkx`, `matplotlib`, `openpyxl`.
- Valid Scopus API credentials and UNIGE web service credentials.

## Installation
1. Clone and enter the repository:
   ```bash
   git clone https://github.com/riccardoberta/deep.git
   cd deep
   ```
2. (Recommended) Create and activate a virtual environment:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate  # Windows: .venv\Scripts\activate
   ```
3. Install the runtime dependencies:
   ```bash
   pip install pybliometrics python-dotenv requests networkx matplotlib openpyxl
   ```
   Tkinter is part of the standard library; on Linux you may need to install `python3-tk` via your package manager.

## Configuration
Create a `.env` file in the project root (never commit it) and provide the required secrets plus any optional overrides. The application automatically loads it before launching the GUI.

| Variable          | Required | Purpose                                                                 | Default            |
|-------------------|----------|-------------------------------------------------------------------------|--------------------|
| `SCOPUS_API_KEY`  | Yes      | Personal Scopus API key used by `pybliometrics`.                        | –                  |
| `SCOPUS_EMAIL`    | Yes      | Email registered with the Scopus API.                                   | –                  |
| `UNIGE_USERNAME`  | Yes      | Username for the UNIGE REST services.                                   | –                  |
| `UNIGE_PASSWORD`  | Yes      | Password for the UNIGE REST services.                                   | –                  |
| `INPUT_CSV`       | No       | Default roster XLSX path used to pre-fill the GUI.                      | `./input/DITEN.xlsx` |
| `YEAR_WINDOWS`    | No       | Comma-separated rolling windows (years) for Scopus stats.               | `15,10,5`          |
| `SLEEP_SECONDS`   | No       | Delay between Scopus requests to stay within rate limits.               | `3.0`              |
| `FETCH_SCOPUS`    | No       | Set to `0`/`false` to skip Scopus enrichment (useful offline).          | `true`             |
| `FETCH_UNIGE`     | No       | Set to `0`/`false` to skip UNIGE enrichment.                            | `true`             |
| `DATA_DIR`        | No       | Root directory where each execution writes its timestamped run folder.  | `data`             |

> ℹ️ The Scopus client stores an auth config under `~/.pybliometrics/config.ini` if it does not already exist. You only need to supply the API key and email via the environment – the code handles the rest.

## Running the application
Launch the GUI with:

```bash
python main.py
```

### Importing tab
1. Choose the roster XLSX (surname, name, ScopusID, UNIGEID, unit…) or keep the default `input/DITEN.xlsx`.
2. Adjust the rolling year windows and toggle on/off the Scopus/UNIGE fetchers as needed.
3. Click **Start Import**. A new folder such as `data/2025_11_06_1/` is created. Each member’s enriched payload is saved under `source/<surname>_<name>.json`, and the live log is streamed in the lower panel.

### Elaborating tab
1. Hit **Prepare Results CSV** to aggregate the latest run into `elaborations/<input>_results.csv`.
2. Hit **Build Collaboration Graph** to generate `elaborations/collaborations.json` (used by the UI) and `elaborations/collaborations.graphml` (for external tools).
3. Use the elaboration log to keep track of what was generated and where the files live.

### Exploring tab
1. The app always targets the most recent run (see the “Current Run” label). Use **Reload Latest Run** if you import from another session.
2. Press **Start Export** to generate Markdown dossiers (`markdown/<surname>_<name>_<scopus>.md`) and their PDF counterparts (`pdf/<surname>_<name>_<scopus>.pdf`).
3. The member tree on the right loads everyone in the chosen run; double-click to open their JSON payload in your default editor (or browse via the magnifier icon).
4. Use **View Collaborations** (after building the graph) to open the interactive co-authorship network. Pick any rolling window (overall, 15 years, 10 years, …) to change node sizes (H-index) and edge weights (co-authorship counts).

## Repository layout
- `main.py` – Tkinter UI and workflow orchestration.
- `importer.py` / `aggregate.py` / `member.py` – parsing the roster and fetching Scopus & UNIGE data.
- `data_preparation.py` – builds the CSV summary.
- `export.py` – renders Markdown dossiers.
- `assets/` – application icon.
- `input/` – sample rosters to get you started.
- `data/` – ignored by git; contains your local runs (back up separately if needed). Each run now contains:
  - `source/` – raw JSON payloads straight from Scopus/UNIGE.
  - `elaborations/` – derived artefacts (`*_results.csv`, `collaborations.*`, etc.).
  - `markdown/` + `pdf/` – profile exports produced from the Exploring tab.

## License
This project is released under the terms of the MIT License (see `LICENSE`).
