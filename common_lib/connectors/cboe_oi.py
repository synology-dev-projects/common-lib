import logging
import math
import re
from typing import Dict, Tuple, Optional, Any
import requests
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

logger = logging.getLogger("quant.connectors.cboe_oi")

# OSI Option Symbol Regex (e.g. AMKR261030C00040000, SPXW261030P05800000, _SPX261030C05800000)
OSI_OPTION_REGEX = re.compile(r"^[_A-Za-z0-9]+?(\d{6})([CP])(\d{8})$")


def _is_valid_float(val: Any) -> bool:
    """Helper to check if a value is a non-null, non-NaN, non-Inf float."""
    if val is None:
        return False
    try:
        f = float(val)
        return not (math.isnan(f) or math.isinf(f))
    except (TypeError, ValueError):
        return False


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(min=1, max=5),
    retry=retry_if_exception_type((requests.exceptions.RequestException, TimeoutError)),
    reraise=False
)
def fetch_cboe_delayed_data(symbol: str) -> Dict[Tuple[str, float, str], Dict[str, Any]]:
    """
    Fetches official OCC open interest and real exchange settlement Greeks from CBOE delayed-quotes.
    Returns a dictionary mapping:
        (expiration_YYYYMMDD, strike, right) -> {
            "open_interest": float,
            "delta": Optional[float],
            "gamma": Optional[float],
            "iv": Optional[float],
            "vega": Optional[float],
            "theta": Optional[float],
            "bid": Optional[float],
            "ask": Optional[float],
            "volume": Optional[float]
        }
    
    If the endpoint is unreachable, returns an empty dict to allow graceful fallback.
    """
    sym = symbol.upper().strip()
    url_candidates = [
        f"https://cdn.cboe.com/api/global/delayed_quotes/options/{sym}.json"
    ]
    if sym in ("SPX", "NDX", "RUT", "VIX", "DJX"):
        url_candidates.insert(0, f"https://cdn.cboe.com/api/global/delayed_quotes/options/_{sym}.json")

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json"
    }

    data_map: Dict[Tuple[str, float, str], Dict[str, Any]] = {}

    for url in url_candidates:
        try:
            resp = requests.get(url, headers=headers, timeout=(5.0, 15.0))
            if resp.status_code != 200:
                logger.debug(f"CBOE delayed data request to {url} returned HTTP {resp.status_code}")
                continue

            payload = resp.json()
            data_obj = payload.get("data", {})
            options_list = data_obj.get("options", [])
            if not options_list:
                continue

            # Extract underlying spot price from CBOE quote summary
            und_price: Optional[float] = None
            for price_key in ("current_price", "close", "prev_day_close"):
                p_val = data_obj.get(price_key)
                if _is_valid_float(p_val) and float(p_val) > 0.0:
                    und_price = float(p_val)
                    break

            for opt in options_list:
                raw_sym = opt.get("option", "")
                m = OSI_OPTION_REGEX.search(raw_sym)
                if not m:
                    continue

                exp6, right, strike8 = m.groups()
                exp_formatted = f"20{exp6}"
                strike = float(strike8) / 1000.0

                oi = opt.get("open_interest")
                oi_val = max(0.0, float(oi)) if _is_valid_float(oi) else None
                delta_val = float(opt.get("delta")) if _is_valid_float(opt.get("delta")) else None
                gamma_val = float(opt.get("gamma")) if _is_valid_float(opt.get("gamma")) else None
                iv_val = float(opt.get("iv")) if _is_valid_float(opt.get("iv")) else None
                vega_val = float(opt.get("vega")) if _is_valid_float(opt.get("vega")) else None
                theta_val = float(opt.get("theta")) if _is_valid_float(opt.get("theta")) else None
                bid_val = float(opt.get("bid")) if _is_valid_float(opt.get("bid")) else None
                ask_val = float(opt.get("ask")) if _is_valid_float(opt.get("ask")) else None
                vol_val = max(0.0, float(opt.get("volume"))) if _is_valid_float(opt.get("volume")) else None

                data_map[(exp_formatted, strike, right)] = {
                    "open_interest": oi_val,
                    "delta": delta_val,
                    "gamma": gamma_val,
                    "iv": iv_val,
                    "vega": vega_val,
                    "theta": theta_val,
                    "bid": bid_val,
                    "ask": ask_val,
                    "volume": vol_val,
                    "und_price": und_price
                }

            if data_map:
                logger.info(f"Loaded {len(data_map)} contracts of official CBOE delayed market data for {sym}.")
                return data_map

        except Exception as e:
            logger.warning(f"Failed to fetch CBOE delayed data for {sym} from {url}: {e}")

    return data_map


def fetch_cboe_open_interest(symbol: str) -> Dict[Tuple[str, float, str], float]:
    """
    Fetches official OCC open interest from CBOE's public delayed-quotes endpoint.
    Returns a dictionary mapping:
        (expiration_YYYYMMDD, strike, right) -> open_interest
    
    Backward-compatible wrapper around fetch_cboe_delayed_data.
    """
    data = fetch_cboe_delayed_data(symbol)
    oi_map: Dict[Tuple[str, float, str], float] = {}
    for k, v in data.items():
        oi = v.get("open_interest")
        if oi is not None:
            oi_map[k] = float(oi)
    return oi_map
