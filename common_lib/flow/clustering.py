"""
Options Flow Thematic Semantic Clustering & SEC 10-K Ingestion Engine.
Groups unusual options flow prints across tickers sharing underlying business model risks
using dense 768-dim embeddings of SEC 10-K Item 1 business descriptions.
"""

import math
import json
import logging
import urllib.request
import urllib.error
from datetime import date, datetime, timezone
from typing import List, Dict, Any, Optional, Tuple, Set, Union
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert

logger = logging.getLogger("quant.common_lib.flow.clustering")

# Canonical core watchlist business descriptions from official SEC 10-K Item 1 filings
CORE_10K_SUMMARIES = {
    "SOFI": {
        "company_name": "SoFi Technologies, Inc.",
        "cik": "0001818874",
        "sector": "Financial Technology & Digital Banking",
        "business_summary": (
            "SoFi is a member-centric digital financial services platform operating three complementary businesses: "
            "Lending, Technology Platform (Galileo), and Financial Services. We offer personal loans, student loan refinancing, "
            "and home loans funded through our national bank charter (SoFi Bank, N.A.). Our digital lending operations expose us "
            "to interest rate fluctuations, credit delinquencies, unsecured consumer loan default risk, loan securitization demand, "
            "and regulatory capital adequacy standards under federal banking laws."
        )
    },
    "AFRM": {
        "company_name": "Affirm Holdings, Inc.",
        "cik": "0001820953",
        "sector": "Fintech & Point-of-Sale Consumer Credit",
        "business_summary": (
            "Affirm provides modern point-of-sale financing and buy-now-pay-later (BNPL) payment solutions for digital commerce. "
            "We originate unsecured installment loans through originating bank partners and securitization warehouses. "
            "Our revenues and asset performance depend directly on merchant transaction volume, consumer spending capacity, "
            "credit loss provisions, debt capital securitization market liquidity, and borrowing benchmark rate spreads."
        )
    },
    "UPST": {
        "company_name": "Upstart Holdings, Inc.",
        "cik": "0001647639",
        "sector": "Fintech & AI Consumer Lending",
        "business_summary": (
            "Upstart operates a proprietary artificial intelligence lending platform that connects consumers to its network of "
            "bank partners and institutional loan buyers. Upstart originates unsecured personal credit and auto refinance loans "
            "using machine learning credit scoring models. The business model is highly sensitive to macroeconomic credit cycles, "
            "consumer delinquency trends, institutional secondary loan buyer liquidity, and cost of warehouse funding."
        )
    },
    "NVDA": {
        "company_name": "NVIDIA Corporation",
        "cik": "0001045810",
        "sector": "Semiconductors & AI Compute",
        "business_summary": (
            "NVIDIA pioneers GPU-accelerated computing and accelerated full-stack platforms for enterprise artificial intelligence, "
            "hyperscale data centers, and advanced graphics. We design compute hardware architectures including Hopper and Blackwell "
            "data center GPUs, NVLink interconnect fabrics, and CUDA software stacks. Our business is sensitive to hyperscaler capital "
            "expenditure budgets, advanced wafer fab foundry capacity (TSMC), and geopolitical semiconductor export controls."
        )
    },
    "AMD": {
        "company_name": "Advanced Micro Devices, Inc.",
        "cik": "0000002488",
        "sector": "Semiconductors & AI Accelerators",
        "business_summary": (
            "AMD designs high-performance semiconductor compute and graphics processors, including EPYC server CPUs, Instinct "
            "accelerator GPUs for data center AI workloads, Ryzen PC client processors, and adaptive embedded SoCs (Xilinx). "
            "Our revenue is tied to data center enterprise server adoption, cloud infrastructure spending, semiconductor supply "
            "chain foundry availability, and competitive x86/GPU compute performance cycles."
        )
    },
    "PLTR": {
        "company_name": "Palantir Technologies Inc.",
        "cik": "0001321655",
        "sector": "Enterprise Software & AI Data Infrastructure",
        "business_summary": (
            "Palantir builds foundational enterprise software platforms (Gotham, Foundry, Apollo, and Artificial Intelligence "
            "Platform / AIP) enabling institutions to integrate structured and unstructured data for decision-making and operational AI. "
            "Our growth depends on defense and national security government procurement cycles, commercial enterprise software sales, "
            "and enterprise adoption of large language model workflow automation."
        )
    },
    "TSLA": {
        "company_name": "Tesla, Inc.",
        "cik": "0001318605",
        "sector": "Automotive & Clean Energy",
        "business_summary": (
            "Tesla designs, manufactures, and sells premium electric vehicles, energy storage systems (Megapack, Powerwall), "
            "and develops full self-driving (FSD) autonomous vehicle software and robotics. Our business is driven by global consumer "
            "auto demand, vehicle average selling prices, automotive financing interest rates, battery cell raw material costs, "
            "and gigafactory manufacturing scale."
        )
    },
    "COIN": {
        "company_name": "Coinbase Global, Inc.",
        "cik": "0001679788",
        "sector": "Digital Assets & Crypto Infrastructure",
        "business_summary": (
            "Coinbase provides financial infrastructure and technology for the crypto economy, operating an institutional and retail "
            "exchange for spot and derivatives crypto trading, institutional staking, and USDC stablecoin reserve revenue. "
            "Revenues correlate directly with digital asset market trading volumes, cryptocurrency volatility, blockchain adoption, "
            "and regulatory oversight across spot and derivatives jurisdictions."
        )
    },
    "XOM": {
        "company_name": "Exxon Mobil Corporation",
        "cik": "0000034088",
        "sector": "Integrated Oil & Gas",
        "business_summary": (
            "ExxonMobil is an integrated energy company engaged in the exploration, production, refining, and marketing of crude oil, "
            "natural gas, petroleum products, and petrochemicals. Financial results are primarily governed by worldwide benchmark prices "
            "for crude oil (Brent, WTI), natural gas spreads, refining crack margins, and global industrial demand."
        )
    },
    "CVX": {
        "company_name": "Chevron Corporation",
        "cik": "0000093410",
        "sector": "Integrated Oil & Gas",
        "business_summary": (
            "Chevron manages integrated energy operations across upstream exploration and production and downstream refining and chemicals. "
            "Our earnings depend heavily on global crude oil and natural gas market price realizations, upstream production volumes, "
            "OPEC+ supply policies, refinery utilization rates, and capital allocation for deepwater and shale operations."
        )
    },
    "SPY": {
        "company_name": "SPDR S&P 500 ETF Trust",
        "cik": "0000888795",
        "sector": "Broad Market Index ETF",
        "business_summary": (
            "The SPDR S&P 500 ETF Trust seeks to provide investment results that correspond generally to the price and yield performance "
            "of the S&P 500 Index. The fund holds large-cap US equities across technology, financial, healthcare, and industrial sectors, "
            "reflecting overall US macroeconomic expansion, monetary policy, and corporate earnings health."
        )
    },
    "QQQ": {
        "company_name": "Invesco QQQ Trust, Series 1",
        "cik": "0001067837",
        "sector": "Large-Cap Growth & Tech ETF",
        "business_summary": (
            "Invesco QQQ Trust tracks the Nasdaq-100 Index, comprising 100 of the largest non-financial companies listed on Nasdaq. "
            "Its performance is heavily weighted toward high-growth technology, consumer discretionary, and communication services companies, "
            "making it sensitive to long-term discount rates and enterprise technology investment."
        )
    },
    "AAPL": {
        "company_name": "Apple Inc.",
        "cik": "0000320193",
        "sector": "Consumer Electronics & Mega-Cap Tech",
        "business_summary": "Apple designs, manufactures, and markets smartphones, personal computers, tablets, wearables, and cloud services."
    },
    "MSFT": {
        "company_name": "Microsoft Corporation",
        "cik": "0000789019",
        "sector": "Enterprise Software & Cloud",
        "business_summary": "Microsoft develops enterprise software platforms, Azure cloud computing, productivity suites, and enterprise AI."
    },
    "AMZN": {
        "company_name": "Amazon.com, Inc.",
        "cik": "0001018724",
        "sector": "E-Commerce & Cloud Infrastructure",
        "business_summary": "Amazon operates global retail e-commerce marketplaces and Amazon Web Services (AWS) hyperscale cloud infrastructure."
    },
    "GOOG": {
        "company_name": "Alphabet Inc.",
        "cik": "0001652044",
        "sector": "Digital Advertising & AI Platforms",
        "business_summary": "Alphabet operates Google Search, YouTube, Google Cloud, Android, and enterprise AI foundation models."
    },
    "GOOGL": {
        "company_name": "Alphabet Inc.",
        "cik": "0001652044",
        "sector": "Digital Advertising & AI Platforms",
        "business_summary": "Alphabet operates Google Search, YouTube, Google Cloud, Android, and enterprise AI foundation models."
    },
    "META": {
        "company_name": "Meta Platforms, Inc.",
        "cik": "0001326801",
        "sector": "Social Media & AI Platforms",
        "business_summary": "Meta develops social media applications, digital advertising platforms, and artificial intelligence hardware/infrastructure."
    },
    "AVGO": {
        "company_name": "Broadcom Inc.",
        "cik": "0001730168",
        "sector": "Semiconductors & Networking",
        "business_summary": "Broadcom designs and develops complex semiconductor devices and mission-critical enterprise infrastructure software."
    },
    "MRVL": {
        "company_name": "Marvell Technology, Inc.",
        "cik": "0001835632",
        "sector": "Semiconductors & Data Infrastructure",
        "business_summary": "Marvell develops data infrastructure semiconductor solutions spanning computing, networking, and custom ASIC accelerators."
    },
    "MU": {
        "company_name": "Micron Technology, Inc.",
        "cik": "0000723125",
        "sector": "Semiconductors & Memory",
        "business_summary": "Micron manufactures advanced semiconductor memory and storage solutions including high-bandwidth memory (HBM), DRAM, and NAND."
    },
    "INTC": {
        "company_name": "Intel Corporation",
        "cik": "0000050863",
        "sector": "Semiconductors & Foundry",
        "business_summary": "Intel designs central processing units, microprocessors, and semiconductor manufacturing and foundry services."
    },
    "CRWD": {
        "company_name": "CrowdStrike Holdings, Inc.",
        "cik": "0001535527",
        "sector": "Cybersecurity & Cloud Protection",
        "business_summary": "CrowdStrike provides cloud-native endpoint security, threat intelligence, and enterprise cyber defense software."
    },
    "BE": {
        "company_name": "Bloom Energy Corporation",
        "cik": "0001664703",
        "sector": "Clean Energy & Power Infrastructure",
        "business_summary": "Bloom Energy manufactures solid oxide fuel cell systems providing on-site clean baseload electricity for enterprise data centers."
    },
    "SNDK": {
        "company_name": "SanDisk Corporation",
        "cik": "0001000180",
        "sector": "Memory & Flash Storage",
        "business_summary": "SanDisk develops flash memory cards, USB drives, solid state drives, and digital data storage hardware."
    }
}

FALLBACK_SEED_PROFILES = CORE_10K_SUMMARIES


def cosine_distance(vec_a: List[float], vec_b: List[float]) -> float:
    """
    Computes cosine distance: 1 - cosine_similarity.
    Returns value in range [0.0, 2.0], where 0.0 = identical direction.
    """
    if not vec_a or not vec_b or len(vec_a) != len(vec_b):
        return 1.0
    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0
    for a, b in zip(vec_a, vec_b):
        dot += a * b
        norm_a += a * a
        norm_b += b * b
    if norm_a <= 0.0 or norm_b <= 0.0:
        return 1.0
    sim = dot / (math.sqrt(norm_a) * math.sqrt(norm_b))
    sim = max(-1.0, min(1.0, sim))
    return round(1.0 - sim, 6)


def resolve_macro_sector_family(sector: Optional[str], ticker: Optional[str] = None) -> str:
    """
    Partitions granular sector taxonomy and tickers into macro industry families.
    Enforces that physical semiconductors/hardware do not cluster with enterprise software,
    fintech, or consumer platforms due to shared text vocabulary in SEC filings.
    """
    clean_ticker = (ticker or "").upper().strip()

    # Explicit ticker overrides for canonical watchlist
    ticker_macro_map = {
        "NVDA": "SEMICONDUCTORS & HARDWARE",
        "AMD": "SEMICONDUCTORS & HARDWARE",
        "AVGO": "SEMICONDUCTORS & HARDWARE",
        "MU": "SEMICONDUCTORS & HARDWARE",
        "INTC": "SEMICONDUCTORS & HARDWARE",
        "MRVL": "SEMICONDUCTORS & HARDWARE",
        "SNDK": "SEMICONDUCTORS & HARDWARE",
        "PLTR": "ENTERPRISE SOFTWARE & CLOUD",
        "CRWD": "ENTERPRISE SOFTWARE & CLOUD",
        "MSFT": "ENTERPRISE SOFTWARE & CLOUD",
        "AAPL": "MEGA-CAP PLATFORMS & CONSUMER TECH",
        "GOOG": "MEGA-CAP PLATFORMS & CONSUMER TECH",
        "GOOGL": "MEGA-CAP PLATFORMS & CONSUMER TECH",
        "META": "MEGA-CAP PLATFORMS & CONSUMER TECH",
        "AMZN": "MEGA-CAP PLATFORMS & CONSUMER TECH",
        "TSLA": "AUTOMOTIVE & MOBILITY",
        "RIVN": "AUTOMOTIVE & MOBILITY",
        "LCID": "AUTOMOTIVE & MOBILITY",
        "SOFI": "FINANCIAL TECHNOLOGY & CRYPTO",
        "AFRM": "FINANCIAL TECHNOLOGY & CRYPTO",
        "UPST": "FINANCIAL TECHNOLOGY & CRYPTO",
        "COIN": "FINANCIAL TECHNOLOGY & CRYPTO",
        "HOOD": "FINANCIAL TECHNOLOGY & CRYPTO",
        "PYPL": "FINANCIAL TECHNOLOGY & CRYPTO",
        "XOM": "ENERGY & INFRASTRUCTURE",
        "CVX": "ENERGY & INFRASTRUCTURE",
        "BE": "ENERGY & INFRASTRUCTURE",
        "SPY": "BROAD MARKET",
        "QQQ": "BROAD MARKET",
    }
    if clean_ticker in ticker_macro_map:
        return ticker_macro_map[clean_ticker]

    sec = (sector or "").lower().strip()
    if any(k in sec for k in ["semiconductor", "memory", "foundry", "wafer", "hardware", "integrated circuit", "storage"]):
        return "SEMICONDUCTORS & HARDWARE"
    if any(k in sec for k in ["software", "cybersecurity", "cloud", "saas", "data infrastructure", "data platform"]):
        return "ENTERPRISE SOFTWARE & CLOUD"
    if any(k in sec for k in ["consumer electronics", "social media", "advertising", "platform", "media"]):
        return "MEGA-CAP PLATFORMS & CONSUMER TECH"
    if any(k in sec for k in ["fintech", "bank", "crypto", "lending", "credit", "financial", "payment"]):
        return "FINANCIAL TECHNOLOGY & CRYPTO"
    if any(k in sec for k in ["automotive", "vehicle", "electric vehicle", "mobility"]):
        return "AUTOMOTIVE & MOBILITY"
    if any(k in sec for k in ["oil", "gas", "energy", "fuel cell", "solar", "utility"]):
        return "ENERGY & INFRASTRUCTURE"
    if any(k in sec for k in ["etf", "index", "broad market"]):
        return "BROAD MARKET"

    return "THEMATIC EQUITIES"


def get_semantic_profile(engine: sa.Engine, ticker: str) -> Optional[Dict[str, Any]]:
    """Retrieves existing semantic profile for ticker from PostgreSQL."""
    clean_ticker = ticker.upper().strip()
    sql = sa.text("""
        SELECT ticker, cik, company_name, sector, business_summary, embedding, last_filing_date, updated_at
        FROM ticker_semantic_profiles
        WHERE ticker = :ticker
    """)
    with engine.connect() as conn:
        row = conn.execute(sql, {"ticker": clean_ticker}).mappings().first()
        if not row:
            return None
        emb = row["embedding"]
        if isinstance(emb, str):
            try:
                emb = [float(x.strip()) for x in emb.strip("[]").split(",") if x.strip()]
            except Exception:
                emb = None
        elif hasattr(emb, "tolist"):
            emb = emb.tolist()
        return {
            "ticker": row["ticker"],
            "cik": row["cik"],
            "company_name": row["company_name"],
            "sector": row["sector"],
            "business_summary": row["business_summary"],
            "embedding": emb,
            "last_filing_date": row["last_filing_date"].isoformat() if row["last_filing_date"] else None,
            "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
        }


def upsert_semantic_profile(
    engine: sa.Engine,
    ticker: str,
    company_name: str,
    sector: str,
    business_summary: str,
    embedding: Optional[List[float]] = None,
    cik: Optional[str] = None,
    last_filing_date: Optional[date] = None
) -> None:
    """Upserts a semantic profile into ticker_semantic_profiles."""
    clean_ticker = ticker.upper().strip()
    emb_str = None
    if embedding:
        emb_str = "[" + ",".join(str(f) for f in embedding) + "]"

    params: Dict[str, Any] = {
        "ticker": clean_ticker,
        "cik": cik,
        "company_name": company_name,
        "sector": sector,
        "business_summary": business_summary,
        "last_filing_date": last_filing_date or date.today()
    }

    if emb_str:
        sql = sa.text("""
            INSERT INTO ticker_semantic_profiles (
                ticker, cik, company_name, sector, business_summary, embedding, last_filing_date, updated_at
            ) VALUES (
                :ticker, :cik, :company_name, :sector, :business_summary, 
                CAST(:embedding AS vector),
                :last_filing_date, CURRENT_TIMESTAMP
            )
            ON CONFLICT (ticker) DO UPDATE SET
                cik = EXCLUDED.cik,
                company_name = EXCLUDED.company_name,
                sector = EXCLUDED.sector,
                business_summary = EXCLUDED.business_summary,
                embedding = EXCLUDED.embedding,
                last_filing_date = EXCLUDED.last_filing_date,
                updated_at = CURRENT_TIMESTAMP;
        """)
        params["embedding"] = emb_str
    else:
        sql = sa.text("""
            INSERT INTO ticker_semantic_profiles (
                ticker, cik, company_name, sector, business_summary, last_filing_date, updated_at
            ) VALUES (
                :ticker, :cik, :company_name, :sector, :business_summary, 
                :last_filing_date, CURRENT_TIMESTAMP
            )
            ON CONFLICT (ticker) DO UPDATE SET
                cik = EXCLUDED.cik,
                company_name = EXCLUDED.company_name,
                sector = EXCLUDED.sector,
                business_summary = EXCLUDED.business_summary,
                last_filing_date = EXCLUDED.last_filing_date,
                updated_at = CURRENT_TIMESTAMP;
        """)

    with engine.begin() as conn:
        conn.execute(sql, params)
    logger.info(f"Successfully upserted semantic profile for {clean_ticker}")


def fetch_sec_edgar_10k_summary(ticker: str) -> Optional[Dict[str, str]]:
    """
    Attempts to fetch business information from SEC EDGAR API.
    Uses free public endpoints with required User-Agent compliant header.
    Falls back to CORE_10K_SUMMARIES if rate limited or not found.
    """
    clean_ticker = ticker.upper().strip()
    if clean_ticker in CORE_10K_SUMMARIES:
        return CORE_10K_SUMMARIES[clean_ticker]

    headers = {
        "User-Agent": "QuantAI ResearchBot institutional-research@quantsystem.internal"
    }

    try:
        # 1. Lookup CIK from SEC company_tickers.json
        url = "https://www.sec.gov/files/company_tickers.json"
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=4) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            for entry in data.values():
                if entry.get("ticker", "").upper() == clean_ticker:
                    cik = str(entry.get("cik_str", "")).zfill(10)
                    title = entry.get("title", clean_ticker)
                    return {
                        "company_name": title,
                        "cik": cik,
                        "sector": "Public US Corporation",
                        "business_summary": f"{title} ({clean_ticker}) is a publicly traded corporation. Operational and macro risks are detailed in SEC Form 10-K."
                    }
    except Exception as e:
        logger.debug(f"SEC EDGAR remote fetch skipped for {clean_ticker}: {e}")

    # Default placeholder if neither in core nor SEC found
    return {
        "company_name": clean_ticker,
        "cik": None,
        "sector": "Equities",
        "business_summary": f"Equities asset profile for {clean_ticker}."
    }


def ingest_ticker_10k(
    ticker: str,
    engine: sa.Engine,
    api_key: Optional[str] = None
) -> Dict[str, Any]:
    """
    Ingests 10-K business description for a ticker:
    1. Checks if already exists in ticker_semantic_profiles with embedding.
    2. If missing embedding, generates 768-dim vector via Gemini text-embedding-004.
    3. Persists to database and returns profile.
    """
    clean_ticker = ticker.upper().strip()
    existing = get_semantic_profile(engine, clean_ticker)
    if existing and existing.get("embedding"):
        return existing

    # Fetch 10-K summary
    info = fetch_sec_edgar_10k_summary(clean_ticker)
    summary = info.get("business_summary", f"{clean_ticker} commercial operations and market risks.")

    # Generate 768-dim embedding
    embedding: Optional[List[float]] = None
    try:
        from common_lib.economic_events.embeddings import generate_embeddings
        embs = generate_embeddings([summary], api_key=api_key)
        if embs and len(embs[0]) == 768:
            embedding = embs[0]
    except Exception as emb_err:
        logger.warning(f"Could not generate Gemini embedding for {clean_ticker}: {emb_err}")

    upsert_semantic_profile(
        engine=engine,
        ticker=clean_ticker,
        company_name=info.get("company_name", clean_ticker),
        sector=info.get("sector", "Equities"),
        business_summary=summary,
        embedding=embedding,
        cik=info.get("cik")
    )

    return get_semantic_profile(engine, clean_ticker) or {}


def seed_core_watchlist_profiles(
    engine: sa.Engine,
    api_key: Optional[str] = None
) -> int:
    """
    Seeds core watchlist profiles into ticker_semantic_profiles.
    Generates embeddings if missing and API key is available.
    """
    seeded = 0
    for ticker, data in CORE_10K_SUMMARIES.items():
        existing = get_semantic_profile(engine, ticker)
        if not existing:
            embedding = None
            if api_key:
                try:
                    from common_lib.economic_events.embeddings import generate_embeddings
                    embs = generate_embeddings([data["business_summary"]], api_key=api_key)
                    if embs and len(embs[0]) == 768:
                        embedding = embs[0]
                except Exception as e:
                    logger.debug(f"Could not embed seeded profile for {ticker}: {e}")

            upsert_semantic_profile(
                engine=engine,
                ticker=ticker,
                company_name=data["company_name"],
                sector=data["sector"],
                business_summary=data["business_summary"],
                embedding=embedding,
                cik=data["cik"]
            )
            seeded += 1
    logger.info(f"Seeded {seeded} core watchlist semantic profiles into database.")
    return seeded


def cluster_thematic_flow(
    engine: sa.Engine,
    trade_date: Optional[Union[str, date]] = None,
    min_cluster_premium: float = 1_000_000.0,
    max_distance: float = 0.25
) -> List[Dict[str, Any]]:
    """
    Clusters active options flow trades on trade_date by semantic business similarity.
    1. Aggregates today's session prints per ticker from unusual_whales_flow_te.
    2. Loads pre-calculated 768-dim embeddings from ticker_semantic_profiles.
    3. Calculates pairwise cosine distance <= max_distance (default 0.25).
    4. Groups into connected components.
    5. Filters to clusters with >= 2 tickers and >= min_cluster_premium.
    """
    # 1. Resolve trade date
    with engine.connect() as conn:
        if trade_date is None:
            max_dt = conn.execute(sa.text("SELECT MAX(trade_date) FROM unusual_option_flow_te;")).scalar()
            resolved_date = str(max_dt) if max_dt else str(date.today())
        elif isinstance(trade_date, str):
            resolved_date = trade_date.split()[0]
        else:
            resolved_date = str(trade_date)

        # 2. Query today's flow aggregated per ticker
        flow_sql = sa.text("""
            SELECT 
                symbol,
                SUM(premium) AS total_premium,
                SUM(CASE WHEN order_type ILIKE '%CALL%' THEN premium ELSE 0 END) AS call_premium,
                SUM(CASE WHEN order_type ILIKE '%PUT%' THEN premium ELSE 0 END) AS put_premium,
                COUNT(*) AS trade_count
            FROM unusual_option_flow_te
            WHERE trade_date = :trade_date
            GROUP BY symbol
            ORDER BY total_premium DESC;
        """)
        flow_rows = conn.execute(flow_sql, {"trade_date": resolved_date}).fetchall()

    if not flow_rows:
        logger.info(f"No flow prints found in unusual_option_flow_te for {resolved_date}.")
        return []

    ticker_stats = {}
    for r in flow_rows:
        sym = str(r[0]).upper()
        tot = float(r[1] or 0.0)
        c_prem = float(r[2] or 0.0)
        p_prem = float(r[3] or 0.0)
        sentiment = "BULLISH" if c_prem >= p_prem else "BEARISH"
        cp_ratio = round(c_prem / p_prem, 2) if p_prem > 0 else (99.9 if c_prem > 0 else 1.0)

        ticker_stats[sym] = {
            "symbol": sym,
            "total_premium": tot,
            "call_premium": c_prem,
            "put_premium": p_prem,
            "sentiment": sentiment,
            "call_put_ratio": cp_ratio,
            "trade_count": int(r[4] or 0)
        }

    active_symbols = list(ticker_stats.keys())

    # 3. Fetch pre-calculated embeddings
    embeddings_map: Dict[str, List[float]] = {}
    meta_map: Dict[str, Dict[str, Any]] = {}
    with engine.connect() as conn:
        profile_sql = sa.text("""
            SELECT ticker, company_name, sector, embedding
            FROM ticker_semantic_profiles
            WHERE ticker = ANY(:tickers) AND embedding IS NOT NULL;
        """)
        p_rows = conn.execute(profile_sql, {"tickers": active_symbols}).fetchall()
        for pr in p_rows:
            sym = str(pr[0]).upper()
            c_name = str(pr[1])
            sector = str(pr[2])
            if (not sector or sector in ("Public Equities", "Thematic Equities", "None")) and sym in FALLBACK_SEED_PROFILES:
                sector = FALLBACK_SEED_PROFILES[sym].get("sector", sector)
            emb = pr[3]
            if isinstance(emb, str):
                try:
                    emb = [float(x.strip()) for x in emb.strip("[]").split(",") if x.strip()]
                except Exception:
                    emb = None
            elif hasattr(emb, "tolist"):
                emb = emb.tolist()

            if emb and len(emb) == 768:
                embeddings_map[sym] = emb
                meta_map[sym] = {"company_name": c_name, "sector": sector}

    # If fewer than 2 tickers have embeddings, no multi-ticker semantic clusters possible
    embedded_tickers = [t for t in active_symbols if t in embeddings_map]
    if len(embedded_tickers) < 2:
        return []

    # 4. Pairwise Cosine Distance & Graph Adjacency
    adj: Dict[str, Set[str]] = {t: set() for t in embedded_tickers}
    distances: Dict[Tuple[str, str], float] = {}

    for i in range(len(embedded_tickers)):
        t1 = embedded_tickers[i]
        fam1 = resolve_macro_sector_family(meta_map.get(t1, {}).get("sector"), t1)
        for j in range(i + 1, len(embedded_tickers)):
            t2 = embedded_tickers[j]
            fam2 = resolve_macro_sector_family(meta_map.get(t2, {}).get("sector"), t2)
            dist = cosine_distance(embeddings_map[t1], embeddings_map[t2])
            distances[(t1, t2)] = dist
            distances[(t2, t1)] = dist
            # Hard Sector Boundary Guard: Only allow cluster linkages if both tickers share the same macro sector family
            if fam1 == fam2 and dist <= max_distance:
                adj[t1].add(t2)
                adj[t2].add(t1)

    # 5. Connected Components Clustering
    visited: Set[str] = set()
    raw_clusters: List[List[str]] = []

    for t in embedded_tickers:
        if t not in visited:
            component = []
            queue = [t]
            visited.add(t)
            while queue:
                curr = queue.pop(0)
                component.append(curr)
                for neighbor in adj[curr]:
                    if neighbor not in visited:
                        visited.add(neighbor)
                        queue.append(neighbor)
            raw_clusters.append(component)

    # 6. Format and Filter Clusters
    results: List[Dict[str, Any]] = []
    cluster_idx = 1

    for comp in raw_clusters:
        if len(comp) < 2:
            continue  # Must be a multi-ticker rotation / cluster

        combined_premium = sum(ticker_stats[t]["total_premium"] for t in comp)
        if combined_premium < min_cluster_premium:
            continue  # Filter below institutional capital hurdle ($1.0M default)

        # Compute cluster internal distances
        pair_dists = []
        for i in range(len(comp)):
            for j in range(i + 1, len(comp)):
                pair_dists.append(distances.get((comp[i], comp[j]), 1.0))
        avg_dist = round(sum(pair_dists) / len(pair_dists), 4) if pair_dists else 0.0
        max_dist = round(max(pair_dists), 4) if pair_dists else 0.0

        total_call_prem = sum(ticker_stats[t]["call_premium"] for t in comp)
        total_put_prem = sum(ticker_stats[t]["put_premium"] for t in comp)
        net_sentiment = "BULLISH" if total_call_prem >= total_put_prem else "BEARISH"

        # Derive representative theme title from dominant sector
        sectors = [meta_map.get(t, {}).get("sector") for t in comp]
        valid_sectors = [
            s for s in sectors
            if s and s not in ("Public Equities", "Thematic Equities", "None", "")
        ]
        dominant_sector = max(set(valid_sectors), key=valid_sectors.count) if valid_sectors else "Thematic Technology & Growth"
        cluster_macro_family = resolve_macro_sector_family(dominant_sector, comp[0])
        theme_title = f"{dominant_sector} Rotation ({', '.join(comp)})"

        ticker_items = []
        for t in sorted(comp, key=lambda x: ticker_stats[x]["total_premium"], reverse=True):
            st = ticker_stats[t]
            ticker_items.append({
                "ticker": t,
                "company_name": meta_map.get(t, {}).get("company_name", t),
                "premium": round(st["total_premium"], 2),
                "sentiment": st["sentiment"],
                "call_put_ratio": st["call_put_ratio"],
                "trade_count": st["trade_count"]
            })

        results.append({
            "cluster_id": f"cluster-{resolved_date}-{cluster_idx}",
            "trade_date": str(resolved_date),
            "theme_name": theme_title,
            "dominant_sector": dominant_sector,
            "macro_sector_family": cluster_macro_family,
            "tickers": ticker_items,
            "ticker_count": len(ticker_items),
            "combined_premium": round(combined_premium, 2),
            "net_sentiment": net_sentiment,
            "call_premium": round(total_call_prem, 2),
            "put_premium": round(total_put_prem, 2),
            "avg_cosine_distance": avg_dist,
            "max_cosine_distance": max_dist,
            "avg_similarity": round(1.0 - avg_dist, 4)
        })
        cluster_idx += 1

    # Sort clusters by highest institutional capital deployed
    results.sort(key=lambda c: c["combined_premium"], reverse=True)
    return results
