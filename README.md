# DEEP – DITEN Evaluation and Evidence Platform

DEEP (DITEN Evaluation and Evidence Platform) is a webapp utility used by the DITEN department to gather, normalise, and explore evidence about faculty members. It connects to Scopus, UNIGE and IRIS web services, enriches the department roster with bibliometric metrics plus institutional data, and produces ready-to-share XLSX/Markdown/PDF summaries for evaluation exercises.

## Key features
- Imports the department roster from an Excel workbook (default `input/DITEN.xlsx`).
- Pulls Scopus metrics (H-index, publications, citations per rolling window) via `pybliometrics`.
- Pulls UNIGE people data (roles, locations, teaching, responsibilities) via the official REST API.
- Stores every run under timestamped folders in `data/`, including per-member JSON payloads.
- Keeps raw Scopus/UNIGE payloads in `data/<run>/source/` and automatically produces XLSX summaries, collaboration graphs, and dossier exports after each import.
- Generates two exploration artifacts: a tabular XLSX (for spreadsheets) and Markdown/PDF dossiers.
- Ships with a Dash web dashboard (styled via Dash Bootstrap Components) for browser-based workflows covering imports, elaborations, exploration, and live logs.
- Can build a co-authorship graph (GraphML + JSON) on demand so you can explore collaborations per time window.

## Requirements
- Python 3.10+.
- `pip install` access to the following packages: `pybliometrics`, `python-dotenv`, `requests`, `networkx`, `matplotlib`, `openpyxl`, `dash`, `dash-bootstrap-components`.
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
   pip install pybliometrics python-dotenv requests networkx matplotlib openpyxl dash dash-bootstrap-components
   ```

## Configuration
Create a `.env` file in the project root (never commit it) and provide the required secrets plus any optional overrides. The application automatically loads it before launching the GUI.

| Variable          | Required | Purpose                                                                 | Default              |
|-------------------|----------|-------------------------------------------------------------------------|----------------------|
| `SCOPUS_API_KEY`  | Yes      | Personal Scopus API key used by `pybliometrics`.                        | –                    |
| `SCOPUS_EMAIL`    | Yes      | Email registered with the Scopus API.                                   | –                    |
| `UNIGE_USERNAME`  | Yes      | Username for the UNIGE REST services.                                   | –                    |
| `UNIGE_PASSWORD`  | Yes      | Password for the UNIGE REST services.                                   | –                    |
| `INPUT_FOLDER`    | No       | Directory scanned for roster workbooks shown in the dashboard picker.   | `./input`            |
| `YEAR_WINDOWS`    | No       | Comma-separated rolling windows (years) for Scopus stats.               | `15,10,5`            |
| `SLEEP_SECONDS`   | No       | Delay between Scopus requests to stay within rate limits.               | `3.0`                |
| `FETCH_SCOPUS`    | No       | Set to `0`/`false` to skip Scopus enrichment (useful offline).          | `true`               |
| `FETCH_UNIGE`     | No       | Set to `0`/`false` to skip UNIGE enrichment.                            | `true`               |
| `FETCH_IRIS`      | No       | Set to `0`/`false` to skip IRIS products.                               | `true`               |
| `DATA_DIR`        | No       | Root directory where each execution writes its timestamped run folder.  | `data`               |

> ℹ️ The Scopus client stores an auth config under `~/.pybliometrics/config.ini` if it does not already exist. You only need to supply the API key and email via the environment – the code handles the rest.

## Running the application
Start the Dash server:

```bash
python dash_app.py
```

Open `http://127.0.0.1:8050/` to access the dashboard.

### Importing tab
1. Pick the roster workbook from the dropdown (all `.xlsx`/`.xlsm` files under `INPUT_FOLDER` are listed). You can upload new files or delete the selected one directly from the UI, and the preview table shows the first rows so you can verify the content.
2. Adjust the rolling year windows and enable/disable Scopus, UNIGE, or IRIS fetches.
3. Click **Start Import** to create a new run under `data/<timestamp>/`. Live logs stream in the textarea while background workers finish, and once the import completes the app automatically generates the XLSX summary, collaboration graph, and Markdown/PDF exports for you.
4. Use **Stop Import** if you need to cancel an in-progress run; while importing, the start button and fetch toggles stay disabled to avoid accidental changes.

### Exploring tab
1. Pick any existing run from the dropdown. You can reload the latest run, rebuild its outputs (XLSX/collaboration/exports), or delete outdated runs directly from the toolbar.
2. The table lists every member in the selected run; select a row to inspect the full JSON payload inline.
3. Once a collaboration graph exists, the **View Collaborations** button opens the interactive co-authorship network where node sizes reflect H-index and edges follow weighted co-authorship counts.
4. Use **Download Results XLSX** to grab the summary workbook produced for the selected run.

## Repository layout
- `dash_app.py` – Dash-based web dashboard exposing the same workflows through a browser.
- `importer.py` / `aggregate.py` / `member.py` – parsing the roster and fetching Scopus & UNIGE data.
- `data_preparation.py` – builds the XLSX summary.
- `export.py` – renders Markdown dossiers.
- `input/` – sample rosters to get you started.
- `data/` – ignored by git; contains your local runs (back up separately if needed). Each run now contains:
  - `source/` – raw JSON payloads straight from Scopus/UNIGE.
  - `elaborations/` – derived artefacts (`*_results.xlsx`, `collaborations.*`, etc.).
  - `markdown/` + `pdf/` – profile exports produced from the Exploring tab.

## License
This project is released under the terms of the MIT License (see `LICENSE`).
