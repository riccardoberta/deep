# DEEP – DITEN Evaluation and Evidence Platform

DEEP is a web application that helps the DITEN department collect, organise, and analyse bibliometric evidence about its faculty members. It connects to Scopus and the UNIGE REST services to enrich a department roster with publication metrics, institutional roles, and teaching data, then scores each member against the D.M. 589/2018 thresholds and makes everything accessible through an interactive dashboard.

## Features

**Data collection**
- Reads the department roster from an Excel workbook (`.xlsx` / `.xlsm`) placed in `INPUT_FOLDER`. The workbook can have an explicit header row (`Surname`, `Name`, `Unit`, `Role`, `SSD`, `scopus_id`, `unige_id`) or rely on column order.
- Fetches Scopus metrics (H-index, publications, citations) over configurable rolling windows (default 5, 10, 15 years).
- Fetches UNIGE data (academic role, SSD, location, teaching, responsibilities) via the official REST API; falls back to the values declared in the input file when UNIGE is unavailable.
- Computes D.M. 589/2018 threshold scores (0.0 / 0.4 / 0.8 / 1.2) for three indicators (articles, citations, H-index) and three levels (Associato, Ordinario, Commissario/Evaluator), storing both scores and value/threshold ratios.
- Saves each import as a self-contained run folder named `YYYY_MM_DD_<filename>` under `DATA_DIR`; a second import on the same day with the same file overwrites the previous one. Each run includes per-member JSON payloads, an XLSX summary, a co-authorship graph, and a full import log.

**Dashboard tabs**

| Tab | Purpose |
|-----|---------|
| **Collect** | Upload and select roster workbooks, configure fetch options, start/stop imports, monitor live logs. |
| **Members** | Browse members for any run; click a row to see full bibliometric details, threshold scores, and the raw JSON payload. |
| **Query** | Ask natural-language questions about the data; history is persisted per run and survives restarts; individual entries can be deleted. |
| **Network** | Interactive co-authorship graph (Cytoscape.js); filter by SSD, set a minimum co-authorship threshold, click an edge to see the shared paper list. |
| **Overview** | Department-level KPIs, average metrics by SSD (sortable by score), and a member comparison table with Excel export. |

**Exports**
- PDF dossier per member (threshold score table, teaching, Scopus publications with inline bold titles and IEEE-style metadata).
- XLSX summary of all members with scores and ratios.
- XLSX export of the SSD breakdown and member comparison tables.

## Requirements

- Python 3.10+
- A `requirements.txt` is provided; install everything with:
  ```bash
  pip install -r requirements.txt
  ```
- Valid Scopus API credentials and UNIGE web service credentials.
- `dash-cytoscape` is optional; the Network tab shows a placeholder message if it is not installed.

## Installation

```bash
git clone https://github.com/riccardoberta/deep.git
cd deep
python3 -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Configuration

Create a `.env` file in the project root (never commit it). The application loads it automatically at startup.

| Variable          | Required | Description                                                              | Default   |
|-------------------|----------|--------------------------------------------------------------------------|-----------|
| `SCOPUS_API_KEY`  | Yes      | Scopus API key used by `pybliometrics`.                                  | –         |
| `SCOPUS_EMAIL`    | Yes      | Email registered with the Scopus API.                                    | –         |
| `UNIGE_USERNAME`  | Yes      | Username for the UNIGE REST services.                                    | –         |
| `UNIGE_PASSWORD`  | Yes      | Password for the UNIGE REST services.                                    | –         |
| `APP_USERNAME`    | Yes      | Username to log in to the dashboard.                                     | –         |
| `APP_PASSWORD`    | Yes      | Password to log in to the dashboard.                                     | –         |
| `APP_SECRET_KEY`  | Yes      | Secret key for Flask session signing (any long random string).           | –         |
| `INPUT_FOLDER`    | No       | Directory scanned for roster workbooks.                                  | `./input` |
| `DATA_DIR`        | No       | Root directory for run output folders.                                   | `data`    |
| `YEAR_WINDOWS`    | No       | Comma-separated rolling windows (years) for Scopus stats.                | `15,10,5` |
| `SLEEP_SECONDS`   | No       | Delay between Scopus requests to respect rate limits.                    | `3.0`     |
| `FETCH_SCOPUS`    | No       | Set to `false` to skip Scopus enrichment (useful for offline testing).   | `true`    |
| `FETCH_UNIGE`     | No       | Set to `false` to skip UNIGE enrichment.                                 | `true`    |

> The Scopus client writes its auth config to `~/.pybliometrics/config.ini` on first use; only the API key and email need to be provided via the environment.

## Running the application

```bash
python dash_app.py
```

Open `http://127.0.0.1:8050/` in a browser. You will be prompted to log in with the credentials set in `APP_USERNAME` and `APP_PASSWORD`.

## Repository layout

```
dash_app.py          Dash web dashboard and all UI callbacks
importer.py          Orchestrates per-member data fetching and payload assembly
aggregate.py         Loads and normalises the input roster workbook
member.py            Member dataclass
thresholds.py        D.M. 589/2018 threshold loading, SSD mapping, and score computation
analyser.py          LLM-based query engine over run data
data_preparation.py  Builds the XLSX results summary
export.py            Generates per-member PDF dossiers
collaborations.py    Builds the co-authorship graph (GraphML + JSON)
requirements.txt     Python dependencies
soglie/              D.M. 589/2018 threshold reference workbook
input/               Roster workbooks (not committed)
data/                Run output folders (not committed)
  YYYY_MM_DD_<file>/
    source/          Per-member JSON payloads
    import.log       Full import log
    elaborations/    XLSX summary, collaboration graph files
    markdown/        Member dossiers
assets/              Static files served by Dash (logo, CSS)
```

## License

This project is released under the MIT License (see `LICENSE`).
