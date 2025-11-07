# DEEP – DITEN Evaluation and Evidence Platform

DEEP is a desktop utility used by the DITEN department to gather, normalise, and explore evidence about faculty members. It connects to Scopus and UNIGE web services, enriches the department roster with bibliometric metrics plus institutional data, and produces ready-to-share CSV/Markdown summaries for evaluation exercises.

## Key features
- Imports the department roster from a CSV (samples available under `input/`).
- Pulls Scopus metrics (H-index, publications, citations per rolling window) via `pybliometrics`.
- Pulls UNIGE people data (roles, locations, teaching, responsibilities) via the official REST API.
- Stores every run under timestamped folders in `data/`, including per-member JSON payloads.
- Generates two exploration artifacts: a tabular CSV (for spreadsheets) and Markdown dossiers.
- Ships with a Tkinter GUI that keeps network work on background threads to avoid UI freezes.

## Requirements
- Python 3.10+ with Tkinter support (installed by default on most macOS/Linux distributions).
- `pip install` access to the following packages: `pybliometrics`, `python-dotenv`, `requests`.
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
   pip install pybliometrics python-dotenv requests
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
| `INPUT_CSV`       | No       | Default roster path used to pre-fill the GUI.                           | `./input/TEST.csv` |
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
1. Choose the roster CSV (surname, name, ScopusID, UNIGEID, unit…) or keep the default sample.
2. Adjust the rolling year windows and toggle on/off the Scopus/UNIGE fetchers as needed.
3. Click **Start Import**. A new folder such as `data/2025_11_06_1/` is created. Each member’s enriched payload is saved under `raw/<surname>_<name>.json`, and the live log is streamed in the lower panel.

### Exploring tab
1. Point the “Working Folder” field to one of the run folders in `data/`.
2. Press **Start Export** to generate:
   - `output/<input>_results.csv` – spreadsheet-friendly summary (one row per member).
   - `markdown/<surname>_<name>_<scopus>.md` – narrative profile with contact info, teaching, metrics, and product list.
3. The member tree on the right loads everyone in the chosen run; double-click to open their JSON payload in your default editor (or browse via the magnifier icon).

## Repository layout
- `main.py` – Tkinter UI and workflow orchestration.
- `importer.py` / `aggregate.py` / `member.py` – parsing the roster and fetching Scopus & UNIGE data.
- `data_preparation.py` – builds the CSV summary.
- `export.py` – renders Markdown dossiers.
- `assets/` – application icon.
- `input/` – sample rosters to get you started.
- `data/` – ignored by git; contains your local runs (back up separately if needed).

## License
This project is released under the terms of the MIT License (see `LICENSE`).
