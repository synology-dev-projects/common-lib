# `common-lib`

The shared foundational Python library for the **Quant System** ecosystem. It provides unified database drivers, broker connectors, financial data extraction engines, alerting utilities, and thread-safe charting engines shared across all pipelines, bots, microservices, and the Progressive Web App (PWA).

---

## 🏛️ Architecture & Modules

```
common-lib/
├── common_lib/
│   ├── config/
│   │   └── config.py               # Shared YAML & .env config loader
│   ├── connectors/
│   │   ├── ibkr.py                 # Interactive Brokers API connector (ib_insync)
│   │   ├── nfty.py                 # Self-hosted ntfy push notification publisher
│   │   ├── oracle.py               # Enterprise Oracle DB connector (oracledb) with upsert/merge
│   │   └── tradingedge/
│   │       ├── dexgex.py           # Real-time GEX & DEX extraction, analytics & OO Figure chart rendering
│   │       ├── optionflow.py       # Unusual options flow extraction & tabular cleaning
│   │       └── quant_levels.py     # Proprietary quant levels & key support/resistance extraction
│   └── utility/
│       └── utils.py                # Core logging, string, date, and math helpers
├── tests/                          # Full pytest test suite with pre-commit integration
└── README.md
```

---

## 🎯 Design Goals

1. **Zero Redundancy**: Centralize database schemas, credential resolution, and institutional data connectors in one place.
2. **Lock-Free Thread Safety**:
   - Chart generation in `dexgex.py` uses Object-Oriented `matplotlib.figure.Figure` and `FigureCanvasAgg` to guarantee 100% thread safety across multi-threaded microservices without global locks.
3. **Robust Data Ingestion**:
   - High-performance batch upserts (`MERGE INTO`), automatic table creation, schema inspection, and metadata generation for Oracle Database.
4. **Resilient Network I/O**:
   - Built-in retry logic, session pooling, and error handling for external feeds (Interactive Brokers, TradingEdge ASP.NET session management).
5. **Continuous Quality Gate**:
   - Strict pre-commit hook runs the comprehensive unit test suite (`pytest tests/ -v`) before any commit can be pushed to version control.

---

## 📦 Key Connectors & Components

### 1. `connectors.tradingedge.dexgex`
- **Data Ingestion**: Scrapes and parses options chain data into Pandas DataFrames.
- **Exposure Math**: Computes Delta Exposure (DEX) and Gamma Exposure (GEX), identifying zero-gamma flip points, Call Walls, Put Walls, and front-week concentration.
- **Thread-Safe Charting**: `generate_gexdex_chart()` renders dual-sided horizontal bar charts (calls left in green, puts right in red) in WebP or PNG format.

### 2. `connectors.oracle`
- `write_to_oracle_upsert()`: Efficient batch ingestion into Oracle using temporary staging tables and SQL `MERGE INTO`.
- `generate_metadata_catalog()`: Automatically inspects database tables, column types, and constraints to generate metadata YAML files for configuration.

### 3. `connectors.ibkr`
- Interfaces with Interactive Brokers Gateway via `ib_insync` to fetch historical bars and market data with contract definition caching.

### 4. `connectors.nfty`
- Publishes real-time alerts to self-hosted `ntfy` server topics with priority, tags, and action buttons.

---

## 🚀 Development & Testing

Run all unit tests locally:
```powershell
py -3.13 -m pytest tests/ -v
```

> **Note**: `common-lib` is mounted as a shared volume in Synology Docker containers (`/app/common_lib`). Any core change must be tested thoroughly before pushing to master.