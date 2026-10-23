import logging
import math
import time as time_mod
from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo
from typing import List, Dict, Any, Optional, Set, Sequence, Tuple

from ib_async import IB, Stock, Index, Option, Contract, Ticker

from common_lib.extractors.ibkr_options import OptionContractRecord
from common_lib.connectors.cboe_oi import fetch_cboe_open_interest, fetch_cboe_delayed_data

logger = logging.getLogger("quant.extractors.ibkr_gexdex")

INDEX_SYMBOLS: Set[str] = {"SPX", "NDX", "RUT", "VIX", "DJX"}


class TokenBucketPacer:
    """
    Token bucket rate limiter enforcing IBKR's 50 msgs/sec limit with burst capacity (Gritli pattern).
    """
    def __init__(self, rate: float = 45.0, capacity: float = 10.0):
        self.rate = rate
        self.capacity = capacity
        self.tokens = capacity
        self.last_refill = time_mod.monotonic()

    def wait(self, ib: IB):
        now = time_mod.monotonic()
        elapsed = now - self.last_refill
        self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
        self.last_refill = now
        if self.tokens < 1.0:
            sleep_needed = (1.0 - self.tokens) / self.rate
            ib.sleep(sleep_needed)
            self.tokens = 0.0
            self.last_refill = time_mod.monotonic()
        else:
            self.tokens -= 1.0


def _is_valid_float(val: Any) -> bool:
    """Helper to check if a value is a non-null, non-NaN float."""
    if val is None:
        return False
    try:
        f = float(val)
        return not (math.isnan(f) or math.isinf(f))
    except (TypeError, ValueError):
        return False


def _get_open_interest(ticker_obj: Ticker, right: str) -> Optional[float]:
    """Extracts open interest from ticker object for Call or Put if populated."""
    if not ticker_obj:
        return None
    oi = None
    if right == "C":
        oi = getattr(ticker_obj, "callOpenInterest", None)
    elif right == "P":
        oi = getattr(ticker_obj, "putOpenInterest", None)

    if oi is None or (isinstance(oi, float) and math.isnan(oi)):
        oi = getattr(ticker_obj, "openInterest", None)

    if _is_valid_float(oi):
        return max(0.0, float(oi))
    return None


def fetch_gex_dex_payload(
    ib: IB,
    symbol: str,
    min_strike: int = 0,
    max_strike: int = 0,
    max_expirations: int = 2,
    batch_size: int = 40,
    batch_timeout: float = 8.0,
    min_dte: int = 0,
    max_dte: int = 30
) -> dict:
    """
    Fetches an options chain snapshot from Interactive Brokers for a single underlying ticker
    using ib_async with strict pacing (<50 msgs/sec), smart early-exit polling, and error 1100/1102
    disconnect resilience. Returns a TradingEdge-compatible GEX/DEX dictionary payload.

    :param ib: Existing connected IB client instance.
    :param symbol: Underlying stock or index ticker symbol (e.g. 'AMKR', 'SPY', 'SPX').
    :param min_strike: Minimum strike filter (inclusive). If min_strike and max_strike are > 0,
                       retrieves strikes in [min_strike, max_strike]. Otherwise defaults to 25 strikes near spot.
    :param max_strike: Maximum strike filter (inclusive).
    :param max_expirations: Number of nearest active expirations to evaluate (default: 2).
    :param batch_size: Maximum contracts to stream concurrently (max 40 per IBKR 100-line limit).
    :param batch_timeout: Maximum polling seconds per market data batch (default: 4.0).
    :return: 1:1 TradingEdge-compatible GEX/DEX dictionary payload.
    """
    sym = symbol.upper().strip()
    is_index = sym in INDEX_SYMBOLS
    today = date.today()

    pacer = TokenBucketPacer(rate=45.0, capacity=10.0)

    # Pre-fetch official OCC open interest and delayed settlement Greeks from CBOE (Gritli pattern)
    cboe_delayed_data: Dict[Tuple[str, float, str], Dict[str, Any]] = {}
    cboe_oi: Dict[Tuple[str, float, str], float] = {}
    try:
        cboe_delayed_data = fetch_cboe_delayed_data(sym)
    except Exception as e:
        logger.warning(f"Could not load CBOE delayed market data for {sym}: {e}")
        cboe_delayed_data = {}

    if cboe_delayed_data:
        cboe_oi = {
            k: v["open_interest"]
            for k, v in cboe_delayed_data.items()
            if v.get("open_interest") is not None
        }
    else:
        try:
            cboe_oi = fetch_cboe_open_interest(sym)
        except Exception:
            cboe_oi = {}

    from common_lib.calculators.gexdex import GexDexCalculator

    # --------------------------------------------------------------------------
    # 1. Attach Error 1100/1101/1102 Disconnect Resilience Listener
    # --------------------------------------------------------------------------
    is_paused = False

    def on_error(reqId: int, errorCode: int, errorString: str, contract: Any = None):
        nonlocal is_paused
        if errorCode == 1100:
            logger.warning(f"IBKR Error 1100: Connectivity lost between TWS and IB servers. Pausing batch execution.")
            is_paused = True
        elif errorCode in (1101, 1102):
            logger.info(f"IBKR Error {errorCode}: Connectivity restored ({errorString}). Resuming batch execution.")
            is_paused = False

    ib.errorEvent += on_error

    def wait_if_paused():
        while is_paused:
            logger.warning("IBKR connection paused (Error 1100). Sleeping 0.5s...")
            ib.sleep(0.5)

    try:
        # Enable delayed-frozen market data (type 4) for after-hours Greek extraction
        try:
            ib.reqMarketDataType(4)
        except Exception as e:
            logger.debug(f"Could not set reqMarketDataType(4): {e}")

        # ----------------------------------------------------------------------
        # 2. Qualify Underlying & Resolve Spot Price
        # ----------------------------------------------------------------------
        wait_if_paused()
        if is_index:
            underlying = Index(sym, "CBOE", "USD")
        else:
            underlying = Stock(sym, "SMART", "USD")

        qualified_und = ib.qualifyContracts(underlying)
        if qualified_und:
            underlying = qualified_und[0]

        spot_price = 0.0
        mkt_ticker = ib.reqMktData(underlying, "", False, False)
        start_spot = datetime.now()
        while (datetime.now() - start_spot).total_seconds() < 4.0:
            ib.sleep(0.1)
            mp = getattr(mkt_ticker, "marketPrice", lambda: None)()
            if _is_valid_float(mp) and mp > 0.0:
                spot_price = float(mp)
                break
            for attr in ("last", "close", "bid", "ask"):
                val = getattr(mkt_ticker, attr, None)
                if _is_valid_float(val) and val > 0.0:
                    spot_price = float(val)
                    break
            if spot_price > 0.0:
                break

        ib.cancelMktData(underlying)
        ib.sleep(0.025)

        if spot_price <= 0.0:
            logger.warning(f"Unable to resolve spot price for {sym}. Attempting contract selection with fallback.")

        # ----------------------------------------------------------------------
        # 3. Retrieve Option Chains and Filter Expirations & Strikes
        # ----------------------------------------------------------------------
        wait_if_paused()
        chains = ib.reqSecDefOptParams(
            underlying.symbol,
            "",
            underlying.secType,
            underlying.conId
        )

        if not chains:
            logger.error(f"No option chains returned from IBKR for {sym}.")
            return GexDexCalculator().calculate([], spot_price or 1.0, sym, today)

        # Select preferred SMART chain
        preferred_chains = []
        for ch in chains:
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

        chain = preferred_chains[0]

        # Filter valid standard expirations chronologically (within [min_dte, max_dte])
        valid_expirations: List[str] = []
        now_et = datetime.now(ZoneInfo("America/New_York"))
        for exp_str in sorted(chain.expirations):
            try:
                exp_dt = datetime.strptime(exp_str, "%Y%m%d").date()
                dte = (exp_dt - today).days
                if dte < min_dte or dte > max_dte:
                    continue
                # If dte == 0 and market closed (>= 16:00 ET), contract has expired
                if dte == 0 and now_et.time() >= time(16, 0):
                    continue
                valid_expirations.append(exp_str)
            except (ValueError, TypeError):
                continue

        selected_expirations = valid_expirations[:max_expirations]
        if not selected_expirations:
            logger.error(f"No valid future expirations found for {sym}.")
            return GexDexCalculator().calculate([], spot_price or 1.0, sym, today)

        # Filter standard strikes
        standard_strikes = [
            float(s) for s in chain.strikes
            if round(float(s) * 2.0, 4) == int(round(float(s) * 2.0, 4))
        ]
        if not standard_strikes:
            standard_strikes = [float(s) for s in chain.strikes]

        standard_strikes = sorted(set(standard_strikes))

        # Strike selection logic
        if min_strike > 0 and max_strike >= min_strike:
            selected_strikes = [
                s for s in standard_strikes
                if float(min_strike) <= s <= float(max_strike)
            ]
            if not selected_strikes and standard_strikes:
                # If exact strike not in standard list, search closest
                closest = min(standard_strikes, key=lambda s: abs(s - min_strike))
                if abs(closest - min_strike) <= 0.5:
                    selected_strikes = [closest]
        else:
            ref_price = spot_price if spot_price > 0.0 else standard_strikes[len(standard_strikes) // 2]
            sorted_by_dist = sorted(standard_strikes, key=lambda s: abs(s - ref_price))
            selected_strikes = sorted(sorted_by_dist[:25])

        logger.info(
            f"Configured {sym} extraction: {len(selected_expirations)} expirations {selected_expirations}, "
            f"{len(selected_strikes)} strikes (Range: {min(selected_strikes, default=0)} - {max(selected_strikes, default=0)})."
        )

        # ----------------------------------------------------------------------
        # 4. Build Contracts & Qualify in Chunks of <= 40 with 0.05s Pacing
        # ----------------------------------------------------------------------
        all_contracts: List[Tuple[Option, str, str, float, int]] = []
        exchange = "CBOE" if is_index else "SMART"
        multiplier = getattr(chain, "multiplier", "100") or "100"

        for exp in selected_expirations:
            exp_dt = datetime.strptime(exp, "%Y%m%d").date()
            dte = (exp_dt - today).days
            for strike in selected_strikes:
                for right in ("C", "P"):
                    opt = Option(
                        sym,
                        exp,
                        strike,
                        right,
                        exchange,
                        multiplier=multiplier,
                        currency="USD"
                    )
                    all_contracts.append((opt, exp, right, strike, dte))

        # Qualify in chunks of at most 40 contracts
        qual_chunk_size = 40
        for i in range(0, len(all_contracts), qual_chunk_size):
            wait_if_paused()
            chunk_opts = [item[0] for item in all_contracts[i:i + qual_chunk_size]]
            try:
                ib.qualifyContracts(*chunk_opts)
            except Exception as q_err:
                logger.debug(f"Contract qualification notice: {q_err}")
            ib.sleep(0.05)

        # Filter to successfully qualified contracts only (if qualification populated conId)
        qualified = [
            item for item in all_contracts
            if getattr(item[0], "conId", 0) and item[0].conId > 0
        ]
        if qualified:
            all_contracts = qualified

        # ----------------------------------------------------------------------
        # 5. Stream Market Data in Batches of batch_size with Early-Exit & Pacing
        # ----------------------------------------------------------------------
        extracted_records: List[OptionContractRecord] = []
        chunks = [
            all_contracts[i:i + batch_size]
            for i in range(0, len(all_contracts), batch_size)
        ]

        for chunk_idx, chunk in enumerate(chunks, start=1):
            wait_if_paused()
            active_subscriptions = []
            ticker_map: Dict[int, Any] = {}

            try:
                # Subscribe with genericTickList="100,101,106" and snapshot=False, token bucket pacing
                for item in chunk:
                    contract = item[0]
                    pacer.wait(ib)
                    t = ib.reqMktData(contract, genericTickList="100,101,106", snapshot=False)
                    active_subscriptions.append((contract, item))
                    ticker_map[id(contract)] = t

                # Smart Early-Exit Polling loop (poll every 0.2s up to batch_timeout)
                start_poll = datetime.now()
                threshold_needed = len(chunk)

                while (datetime.now() - start_poll).total_seconds() < batch_timeout:
                    wait_if_paused()
                    ib.sleep(0.2)

                    ready_count = 0
                    for contract, item in active_subscriptions:
                        _, exp, right, strike, _ = item
                        t = ticker_map.get(id(contract))
                        if not t:
                            continue

                        # Check Greeks and Open Interest
                        mg = getattr(t, "modelGreeks", None)
                        key = (exp, float(strike), right)
                        cboe_entry = cboe_delayed_data.get(key)
                        has_greeks = (
                            (mg is not None and
                             _is_valid_float(getattr(mg, "gamma", None)) and
                             _is_valid_float(getattr(mg, "delta", None))) or
                            (cboe_entry is not None and
                             cboe_entry.get("gamma") is not None and
                             cboe_entry.get("delta") is not None)
                        )
                        if (cboe_entry and cboe_entry.get("open_interest") is not None) or (key in cboe_oi):
                            has_oi = True
                        else:
                            oi_val = _get_open_interest(t, right)
                            has_oi = oi_val is not None

                        if has_greeks and has_oi:
                            ready_count += 1

                    if ready_count >= threshold_needed:
                        logger.debug(
                            f"Early exit: Chunk {chunk_idx}/{len(chunks)} reached 100% ({ready_count}/{len(chunk)}) "
                            f"ready in {(datetime.now() - start_poll).total_seconds():.2f}s."
                        )
                        break


                # Extract records from completed batch
                for contract, item in active_subscriptions:
                    _, exp, right, strike, dte = item
                    t = ticker_map.get(id(contract))
                    key = (exp, float(strike), right)
                    cboe_entry = cboe_delayed_data.get(key)

                    mg = getattr(t, "modelGreeks", None) if t else None
                    # Greeks precedence: IBKR real live streaming ticks first, then CBOE official exchange settlement Greeks
                    gamma_val = float(mg.gamma) if (mg and _is_valid_float(mg.gamma)) else (cboe_entry.get("gamma") if cboe_entry else None)
                    delta_val = float(mg.delta) if (mg and _is_valid_float(mg.delta)) else (cboe_entry.get("delta") if cboe_entry else None)
                    iv_val = float(mg.impliedVol) if (mg and _is_valid_float(mg.impliedVol)) else (cboe_entry.get("iv") if cboe_entry else None)
                    vega_val = float(mg.vega) if (mg and _is_valid_float(mg.vega)) else (cboe_entry.get("vega") if cboe_entry else None)
                    theta_val = float(mg.theta) if (mg and _is_valid_float(mg.theta)) else (cboe_entry.get("theta") if cboe_entry else None)
                    und_p = float(mg.undPrice) if (mg and _is_valid_float(mg.undPrice)) else spot_price

                    if spot_price <= 0.0 and und_p > 0.0:
                        spot_price = und_p

                    # Open Interest precedence: CBOE OCC ground-truth first, then IBKR tick fallback
                    if cboe_entry and cboe_entry.get("open_interest") is not None:
                        oi_val = int(cboe_entry["open_interest"])
                    elif key in cboe_oi:
                        oi_val = int(cboe_oi[key])
                    else:
                        raw_oi = _get_open_interest(t, right)
                        oi_val = int(raw_oi) if raw_oi is not None else 0

                    record = OptionContractRecord(
                        ticker=sym,
                        strike=strike,
                        expiration=exp,
                        right=right,
                        dte=dte,
                        open_interest=oi_val,
                        implied_vol=iv_val,
                        delta=delta_val,
                        gamma=gamma_val,
                        vega=vega_val,
                        theta=theta_val,
                        und_price=und_p
                    )
                    extracted_records.append(record)

            finally:
                # Cancel market data with token bucket pacing
                for contract, _ in active_subscriptions:
                    try:
                        pacer.wait(ib)
                        ib.cancelMktData(contract)
                    except Exception as c_err:
                        logger.debug(f"Cancel market data notice: {c_err}")

        # ----------------------------------------------------------------------
        # 6. Calculate GEX/DEX Distribution matching TradingEdge Schema
        # ----------------------------------------------------------------------
        calc = GexDexCalculator()
        payload = calc.calculate(
            contracts=extracted_records,
            spot_price=spot_price or 1.0,
            ticker=sym,
            target_date=today
        )
        return payload

    finally:
        # Detach error event handler
        try:
            if hasattr(ib, "errorEvent") and ib.errorEvent is not None:
                ib.errorEvent -= on_error
        except Exception:
            pass
