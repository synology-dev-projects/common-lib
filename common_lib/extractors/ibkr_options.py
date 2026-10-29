import logging
import math
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import List, Dict, Any, Optional, Set, Sequence, Tuple

try:
    import asyncio
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    try:
        from ib_insync import IB, Stock, Index, Option, Contract, Ticker
    except ImportError:
        from ib_async import IB, Stock, Index, Option, Contract, Ticker
except (ImportError, RuntimeError):
    IB = None
    Stock = None
    Index = None
    Option = None
    Contract = None
    Ticker = None

logger = logging.getLogger("quant.extractors.ibkr_options")

INDEX_SYMBOLS: Set[str] = {"SPX", "NDX", "RUT", "VIX", "DJX"}


@dataclass
class OptionContractRecord:
    ticker: str
    strike: float
    expiration: str  # YYYYMMDD
    right: str       # 'C' or 'P'
    dte: int
    bid: Optional[float] = None
    ask: Optional[float] = None
    last: Optional[float] = None
    volume: Optional[int] = None
    open_interest: Optional[int] = None
    implied_vol: Optional[float] = None
    delta: Optional[float] = None
    gamma: Optional[float] = None
    vega: Optional[float] = None
    theta: Optional[float] = None
    und_price: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ticker": self.ticker,
            "strike": self.strike,
            "expiration": self.expiration,
            "right": self.right,
            "dte": self.dte,
            "bid": self.bid,
            "ask": self.ask,
            "last": self.last,
            "volume": self.volume,
            "open_interest": self.open_interest,
            "implied_vol": self.implied_vol,
            "delta": self.delta,
            "gamma": self.gamma,
            "vega": self.vega,
            "theta": self.theta,
            "und_price": self.und_price
        }


@dataclass
class OptionsChainExtractionResult:
    ticker: str
    spot_price: float
    timestamp: str
    total_contracts_extracted: int
    contracts: List[OptionContractRecord] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ticker": self.ticker,
            "spot_price": self.spot_price,
            "timestamp": self.timestamp,
            "total_contracts_extracted": self.total_contracts_extracted,
            "contracts": [c.to_dict() for c in self.contracts]
        }


class IBKROptionsExtractor:
    """
    Extracts high-resolution options chain snapshots from Interactive Brokers.
    Enforces strict rate-limiting, chunk-based market data requests, and
    automatic un-subscription to prevent connection starvation or memory leaks.
    """
    def __init__(
        self,
        ib: Any,
        requests_per_batch: int = 40,
        batch_delay_seconds: float = 1.0,
        request_timeout_seconds: float = 3.5,
        min_ready_pct: float = 0.90,
        min_wait_seconds: float = 1.20
    ):
        self.ib = ib
        self.requests_per_batch = max(5, int(requests_per_batch))
        self.batch_delay_seconds = max(0.01, float(batch_delay_seconds))
        self.request_timeout_seconds = max(0.1, float(request_timeout_seconds))
        self.min_ready_pct = max(0.1, min(1.0, float(min_ready_pct)))
        self.min_wait_seconds = max(0.01, float(min_wait_seconds))

    def qualify_underlying(self, ticker: str) -> Any:
        """
        Creates and qualifies the underlying contract (Stock vs Index).
        """
        sym = ticker.upper().strip()
        if sym in INDEX_SYMBOLS:
            contract = Index(sym, "CBOE", "USD")
        else:
            contract = Stock(sym, "SMART", "USD")

        if hasattr(self.ib, "qualifyContracts"):
            qualified = self.ib.qualifyContracts(contract)
            if qualified:
                return qualified[0]
        return contract

    def fetch_spot_price(self, underlying_contract: Any, timeout: float = 4.0) -> float:
        """
        Fetches the current underlying spot price with fallback across last, close, or mid price.
        """
        ticker_obj = self.ib.reqMktData(underlying_contract, "", False, False)
        start = time.time()
        while time.time() - start < timeout:
            if hasattr(self.ib, "sleep"):
                self.ib.sleep(0.1)
            else:
                time.sleep(0.1)

            price = getattr(ticker_obj, "marketPrice", lambda: None)()
            if self._is_valid_float(price) and price > 0.0:
                self._safe_cancel_mkt_data(underlying_contract)
                return float(price)

            for candidate in ["last", "close", "bid", "ask"]:
                val = getattr(ticker_obj, candidate, None)
                if self._is_valid_float(val) and val > 0.0:
                    self._safe_cancel_mkt_data(underlying_contract)
                    return float(val)

        self._safe_cancel_mkt_data(underlying_contract)
        return 0.0

    def select_chains_and_strikes(
        self,
        underlying_contract: Any,
        spot_price: float,
        max_dte: int = 50,
        strike_range_pct: float = 0.25,
        strike_range: Optional[int] = None,
        target_date: Optional[date] = None,
        allowed_exp_strikes: Optional[Set[Tuple[str, float]]] = None
    ) -> List[Dict[str, Any]]:
        """
        Queries secDefOptParams and filters chains, expirations (DTE <= max_dte),
        and strikes either within strike_range (count of strikes around ATM) or strike_range_pct around the spot price.
        """
        today = target_date or date.today()
        chains = self.ib.reqSecDefOptParams(
            underlying_contract.symbol,
            "",
            underlying_contract.secType,
            underlying_contract.conId
        )

        if not chains:
            logger.warning(f"No option parameter chains returned for {underlying_contract.symbol}")
            return []

        # Filter preferred chain by exchange and tradingClass
        is_index = underlying_contract.symbol in INDEX_SYMBOLS
        sym = underlying_contract.symbol
        preferred_chains = []
        for ch in chains:
            # Avoid mini/non-standard option classes like 2SPY
            if getattr(ch, "tradingClass", None) and ch.tradingClass != sym and not is_index:
                continue
            if is_index:
                if ch.exchange in ("SMART", "CBOE"):
                    preferred_chains.append(ch)
            else:
                if ch.exchange == "SMART":
                    preferred_chains.append(ch)

        if not preferred_chains:
            preferred_chains = chains

        # Deduplicate and sort expirations
        qualified_specs: List[Dict[str, Any]] = []

        seen_exp_strike = set()
        for ch in preferred_chains:
            if not ch.strikes:
                continue

            # Filter out corporate-action non-standard decimal strikes (e.g. .59)
            standard_strikes = [
                float(s) for s in ch.strikes
                if round(float(s) * 2.0, 4) == int(round(float(s) * 2.0, 4))
            ]
            if not standard_strikes:
                standard_strikes = [float(s) for s in ch.strikes]

            # Determine eligible strikes for this chain
            if strike_range is not None and strike_range > 0 and spot_price > 0.0:
                sorted_strikes = sorted(set(standard_strikes))
                atm_idx = min(range(len(sorted_strikes)), key=lambda i: abs(sorted_strikes[i] - spot_price))
                start_idx = max(0, atm_idx - strike_range)
                end_idx = min(len(sorted_strikes), atm_idx + strike_range + 1)
                eligible_strikes = set(sorted_strikes[start_idx:end_idx])
            elif spot_price > 0.0:
                min_strike = spot_price * (1.0 - strike_range_pct)
                max_strike = spot_price * (1.0 + strike_range_pct)
                eligible_strikes = {s for s in standard_strikes if min_strike <= s <= max_strike}
            else:
                eligible_strikes = set(standard_strikes)

            for exp_str in ch.expirations:
                try:
                    exp_dt = datetime.strptime(exp_str, "%Y%m%d").date()
                    dte = (exp_dt - today).days
                except (ValueError, TypeError):
                    continue

                if dte < 0 or dte > max_dte:
                    continue

                for strike in eligible_strikes:
                    key = (exp_str, float(strike))
                    if allowed_exp_strikes is not None and key not in allowed_exp_strikes:
                        continue
                    if key in seen_exp_strike:
                        continue
                    seen_exp_strike.add(key)

                    qualified_specs.append({
                        "symbol": underlying_contract.symbol,
                        "expiration": exp_str,
                        "strike": float(strike),
                        "dte": dte,
                        "multiplier": ch.multiplier or "100",
                        "exchange": "CBOE" if is_index else "SMART"
                    })

        qualified_specs.sort(key=lambda s: (s["dte"], s["strike"]))
        return qualified_specs

    def extract_chain_snapshot(
        self,
        ticker: str,
        max_dte: int = 50,
        strike_range_pct: float = 0.25,
        strike_range: Optional[int] = None,
        target_date: Optional[date] = None,
        known_spot_price: Optional[float] = None,
        allowed_exp_strikes: Optional[Set[Tuple[str, float]]] = None
    ) -> OptionsChainExtractionResult:
        """
        Executes end-to-end extraction for a ticker:
        1. Qualifies underlying contract (or uses known_spot_price + allowed_exp_strikes).
        2. Resolves spot price.
        3. Identifies eligible options within DTE and strike boundaries.
        4. Paces market data requests (101: OI, 106: Greeks) in bounded chunks.
        5. Compiles and returns normalized OptionContractRecord models.
        """
        now_iso = datetime.now(timezone.utc).isoformat()
        sym = ticker.upper().strip()
        is_index = sym in INDEX_SYMBOLS
        today = target_date or date.today()

        if known_spot_price is not None and known_spot_price > 0.0 and allowed_exp_strikes:
            spot_price = float(known_spot_price)
            specs: List[Dict[str, Any]] = []
            for exp_str, strike_val in sorted(allowed_exp_strikes):
                clean_exp = str(exp_str).replace("-", "").strip()
                try:
                    exp_dt = datetime.strptime(clean_exp, "%Y%m%d").date()
                    dte = (exp_dt - today).days
                except (ValueError, TypeError):
                    continue
                if dte < 0 or dte > max_dte:
                    continue
                specs.append({
                    "symbol": sym,
                    "expiration": clean_exp,
                    "strike": float(strike_val),
                    "dte": dte,
                    "multiplier": "100",
                    "exchange": "CBOE" if is_index else "SMART"
                })
            specs.sort(key=lambda s: (s["dte"], s["strike"]))
        else:
            underlying = self.qualify_underlying(ticker)
            spot_price = float(known_spot_price) if (known_spot_price and known_spot_price > 0.0) else self.fetch_spot_price(underlying)

            specs = self.select_chains_and_strikes(
                underlying,
                spot_price=spot_price,
                max_dte=max_dte,
                strike_range_pct=strike_range_pct,
                strike_range=strike_range,
                target_date=target_date,
                allowed_exp_strikes=allowed_exp_strikes
            )

        if not specs:
            logger.warning(f"No option specifications matched filters for {ticker}.")
            return OptionsChainExtractionResult(
                ticker=sym,
                spot_price=spot_price,
                timestamp=now_iso,
                total_contracts_extracted=0,
                contracts=[]
            )

        # Build list of (Option, spec) pairs for both Calls ('C') and Puts ('P')
        all_contract_pairs: List[Any] = []

        for spec in specs:
            for right in ("C", "P"):
                opt = Option(
                    spec["symbol"],
                    spec["expiration"],
                    spec["strike"],
                    right,
                    spec["exchange"],
                    multiplier=spec["multiplier"],
                    currency="USD"
                )
                all_contract_pairs.append((opt, {**spec, "right": right}))

        logger.info(
            f"Extracting {len(all_contract_pairs)} option contracts for {ticker} "
            f"(Chunk Size: {self.requests_per_batch}, Delay: {self.batch_delay_seconds}s)..."
        )

        # Enable Delayed-Frozen data (4) when outside market hours for Greeks/IV
        if hasattr(self.ib, "reqMarketDataType"):
            try:
                self.ib.reqMarketDataType(4)
            except Exception as dt_ex:
                logger.debug(f"Could not set market data type 4: {dt_ex}")

        # Batch request market data with generic ticks: 101 (OI) and 106 (Greeks)
        extracted_records: List[OptionContractRecord] = []
        chunks = [
            all_contract_pairs[i:i + self.requests_per_batch]
            for i in range(0, len(all_contract_pairs), self.requests_per_batch)
        ]

        for chunk_idx, chunk in enumerate(chunks, start=1):
            chunk_records = self._process_batch_chunk(chunk, chunk_idx, len(chunks))
            extracted_records.extend(chunk_records)
            if chunk_idx < len(chunks):
                if hasattr(self.ib, "sleep"):
                    self.ib.sleep(self.batch_delay_seconds)
                else:
                    time.sleep(self.batch_delay_seconds)

        # Straggler retry pass for active contracts where OI arrived (> 0) but modelGreeks timed out
        straggler_indices = [
            idx for idx, rec in enumerate(extracted_records)
            if (rec.open_interest or 0) > 0 and rec.gamma is None and idx < len(all_contract_pairs)
        ]
        if 0 < len(straggler_indices) <= self.requests_per_batch:
            logger.info(f"Running straggler retry pass for {len(straggler_indices)} contracts missing modelGreeks...")
            if hasattr(self.ib, "sleep"):
                self.ib.sleep(self.batch_delay_seconds)
            else:
                time.sleep(self.batch_delay_seconds)
            retry_pairs = [all_contract_pairs[i] for i in straggler_indices]
            retry_records = self._process_batch_chunk(retry_pairs, 1, 1)
            for idx, r_rec in zip(straggler_indices, retry_records):
                if r_rec.gamma is not None or r_rec.implied_vol is not None:
                    extracted_records[idx] = r_rec

        return OptionsChainExtractionResult(
            ticker=sym,
            spot_price=spot_price,
            timestamp=now_iso,
            total_contracts_extracted=len(extracted_records),
            contracts=extracted_records
        )

    def _process_batch_chunk(
        self,
        contract_pairs: Sequence[Any],
        chunk_idx: int,
        total_chunks: int
    ) -> List[OptionContractRecord]:
        """
        Subscribes to market data for a batch of contracts, dynamically polls for
        BOTH OI and Greeks data arrival, and unconditionally cancels market data subscriptions.
        """
        tickers: List[Any] = []
        try:
            for c, spec in contract_pairs:
                try:
                    t = self.ib.reqMktData(c, "101,106", False, False)
                    tickers.append((c, t, spec))
                    if hasattr(self.ib, "sleep"):
                        self.ib.sleep(0.025)
                    else:
                        time.sleep(0.025)
                except Exception as req_ex:
                    logger.debug(f"Failed to request market data for contract {c}: {req_ex}")

            # Dynamic event-driven polling wait instead of unconditional sleep
            start_wait = time.time()
            total_req = len(tickers)
            ready_count = 0
            while True:
                elapsed = time.time() - start_wait
                ready_count = 0
                for c, t, spec in tickers:
                    right = str(spec.get("right", getattr(c, "right", ""))).upper()
                    target_oi_attr = "putOpenInterest" if right == "P" else "callOpenInterest"
                    has_oi = self._is_valid_float(getattr(t, target_oi_attr, None)) or self._is_valid_float(getattr(t, "openInterest", None))
                    has_greeks = (
                        getattr(t, "modelGreeks", None) is not None
                        and getattr(t.modelGreeks, "delta", None) is not None
                    )
                    if has_oi and has_greeks:
                        ready_count += 1

                if total_req > 0 and (ready_count / total_req) >= self.min_ready_pct and elapsed >= self.min_wait_seconds:
                    break
                if elapsed >= self.request_timeout_seconds:
                    break

                if hasattr(self.ib, "sleep"):
                    self.ib.sleep(0.05)
                else:
                    time.sleep(0.05)

            records: List[OptionContractRecord] = []
            for c, t, spec in tickers:
                rec = self._parse_ticker_to_record(c, t, spec)
                records.append(rec)

            logger.info(
                f"Chunk [{chunk_idx}/{total_chunks}] processed: {len(records)} records captured "
                f"({ready_count}/{total_req} ready in {time.time() - start_wait:.2f}s)."
            )
            return records

        finally:
            # Unconditional teardown: cancel all subscriptions in chunk
            for item in tickers:
                self._safe_cancel_mkt_data(item[0])
                if hasattr(self.ib, "sleep"):
                    self.ib.sleep(0.01)
                else:
                    time.sleep(0.01)

    def _parse_ticker_to_record(
        self,
        contract: Any,
        ticker_obj: Any,
        spec: Dict[str, Any]
    ) -> OptionContractRecord:
        """
        Parses an ib_insync Ticker object into an OptionContractRecord.
        """
        bid = self._sanitize_float(getattr(ticker_obj, "bid", None))
        ask = self._sanitize_float(getattr(ticker_obj, "ask", None))
        last = self._sanitize_float(getattr(ticker_obj, "last", None))
        vol = getattr(ticker_obj, "volume", None)
        vol_clean = int(vol) if self._is_valid_float(vol) and vol >= 0 else None

        # Open interest from tick 101 or call/put OI fields with NaN resilience
        right_str = str(spec.get("right", getattr(contract, "right", ""))).upper()
        candidates = ["putOpenInterest", "openInterest", "callOpenInterest"] if right_str == "P" else ["callOpenInterest", "openInterest", "putOpenInterest"]
        oi_clean = None
        for attr in candidates:
            val = getattr(ticker_obj, attr, None)
            if self._is_valid_float(val):
                oi_clean = int(val)
                break

        # Model greeks from tick 106 (with fallback to bidGreeks / askGreeks / lastGreeks)
        greeks = (
            getattr(ticker_obj, "modelGreeks", None)
            or getattr(ticker_obj, "bidGreeks", None)
            or getattr(ticker_obj, "askGreeks", None)
            or getattr(ticker_obj, "lastGreeks", None)
        )
        iv = self._sanitize_float(getattr(greeks, "impliedVol", None))
        delta = self._sanitize_float(getattr(greeks, "delta", None))
        gamma = self._sanitize_float(getattr(greeks, "gamma", None))
        vega = self._sanitize_float(getattr(greeks, "vega", None))
        theta = self._sanitize_float(getattr(greeks, "theta", None))
        und_price = self._sanitize_float(getattr(greeks, "undPrice", None))

        return OptionContractRecord(
            ticker=spec.get("symbol", getattr(contract, "symbol", "")),
            strike=float(spec.get("strike", getattr(contract, "strike", 0.0))),
            expiration=str(spec.get("expiration", getattr(contract, "lastTradeDateOrContractMonth", ""))),
            right=str(spec.get("right", getattr(contract, "right", ""))),
            dte=int(spec.get("dte", 0)),
            bid=bid,
            ask=ask,
            last=last,
            volume=vol_clean,
            open_interest=oi_clean,
            implied_vol=iv,
            delta=delta,
            gamma=gamma,
            vega=vega,
            theta=theta,
            und_price=und_price
        )

    def _safe_cancel_mkt_data(self, contract: Any) -> None:
        try:
            if hasattr(self.ib, "cancelMktData"):
                self.ib.cancelMktData(contract)
        except Exception:
            pass

    @staticmethod
    def _is_valid_float(val: Any) -> bool:
        if val is None or isinstance(val, bool) or hasattr(val, "_mock_return_value"):
            return False
        try:
            f = float(val)
            return not (math.isnan(f) or math.isinf(f))
        except (ValueError, TypeError):
            return False

    @classmethod
    def _sanitize_float(cls, val: Any) -> Optional[float]:
        if cls._is_valid_float(val):
            return float(val)
        return None


def extract_options_chain_snapshot(
    ib: Any,
    ticker: str,
    max_dte: int = 50,
    strike_range_pct: float = 0.25,
    strike_range: Optional[int] = None,
    requests_per_batch: int = 40,
    batch_delay_seconds: float = 1.0,
    request_timeout_seconds: float = 3.5
) -> OptionsChainExtractionResult:
    """
    Convenience function to extract an options chain snapshot using an active IB connection.
    """
    extractor = IBKROptionsExtractor(
        ib=ib,
        requests_per_batch=requests_per_batch,
        batch_delay_seconds=batch_delay_seconds,
        request_timeout_seconds=request_timeout_seconds
    )
    return extractor.extract_chain_snapshot(
        ticker=ticker,
        max_dte=max_dte,
        strike_range_pct=strike_range_pct,
        strike_range=strike_range
    )
