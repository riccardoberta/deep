# DEEP – DITEN Evaluation and Evidence Platform

DEEP (DITEN Evaluation and Evidence Platform) is a webapp utility used by the DITEN department to gather, normalise, and explore evidence about faculty members. It connects to Scopus, UNIGE and IRIS web services, enriches the department roster with bibliometric metrics plus institutional data, and produces ready-to-share XLSX/Markdown/PDF summaries for evaluation exercises.

## Key features
- Imports the department roster from an Excel workbook placed in `INPUT_FOLDER`. The workbook may have an explicit header row (`Surname`, `Name`, `Unit`, `Role`, `SSD`, `scopus_id`, `unige_id`) or rely on positional column order.
- Pulls Scopus metrics (H-index, publications, citations per rolling window) via `pybliometrics`.
- Pulls UNIGE people data (roles, locations, teaching, responsibilities) via the official REST API; when UNIGE data is unavailable the role and SSD declared in the input file are used as fallback.
- Computes bibliometric threshold scores (0.0 / 0.4 / 0.8 / 1.2) for each member against the D.M. 589/2018 reference table stored in `soglie/soglie_dm589_2018.xlsx`. Scores cover three indicators (articles, citations, h-index) and three levels (II fascia, I fascia, Commissario), with value/threshold ratios stored alongside each score.
- Normalises academic grades: `Ordinario` → `Professore Ordinario`, `Associato` → `Professore Associato`.
- Stores every run under timestamped folders in `data/`, including per-member JSON payloads with a `scores` block.
- Automatically produces XLSX summaries (including score columns and ratios), collaboration graphs, and Markdown dossiers after each import.
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
1. Pick the roster workbook from the dropdown (all `.xlsx`/`.xlsm` files under `INPUT_FOLDER` are listed). You can upload new files or delete the selected one directly from the UI; the preview table shows the file content so you can verify it before importing.
2. Adjust the rolling year windows and enable/disable Scopus, UNIGE, or IRIS fetches.
3. Click **Start Import** to create a new run under `data/<timestamp>/`. Live logs stream in the Import Log panel while the background worker fetches data; once the import completes the app automatically generates the XLSX summary, collaboration graph, and Markdown exports.
4. Click **Stop Import** to cancel an in-progress run. While importing, the start button and fetch toggles are disabled to prevent accidental changes.

### Exploring tab
1. Use the **Select data** bar at the top to pick any existing run from the dropdown. From the same bar you can **Download** the results XLSX, **Rebuild** the derived outputs, or **Delete** the run.
2. The **Members** table on the left lists every member in the run (Surname, Name, SSD). Click the 🔍 icon on any row to load the full member profile on the right; the selected row stays highlighted.
3. The **Member details** panel shows:
   - **Bibliometric thresholds (D.M. 589/2018)** – three colour-coded cards (Articoli, Citazioni, H-index) each displaying the score badge (0.0 / 0.4 / 0.8 / 1.2) and the value / threshold = ratio formula for II fascia, I fascia, and Commissario levels.
   - **Raw data** – a collapsible JSON tree of the full member payload with syntax-coloured keys and values.

## Repository layout
```
dash_app.py          Dash web dashboard
importer.py          Orchestrates per-member data fetching and payload assembly
aggregate.py         Loads and normalises the input roster workbook
member.py            Member dataclass (surname, name, scopus_id, grade, ssd, …)
thresholds.py        D.M. 589/2018 threshold loading, SSD mapping, and score computation
data_preparation.py  Builds the XLSX results summary (metrics + scores + ratios)
export.py            Renders Markdown dossiers
collaborations.py    Builds the co-authorship graph
soglie/              D.M. 589/2018 threshold reference workbook
input/               Roster workbooks (not committed)
data/                Run output folders (not committed); each run contains:
  source/            Per-member JSON payloads
  elaborations/      *_results.xlsx, collaboration graph files
  markdown/          Member dossiers
```

## License
This project is released under the terms of the MIT License (see `LICENSE`).
