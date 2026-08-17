from common_lib.connectors.tradingedge.dexgex import (
    extract_raw_data,
    authenticate_and_get_cookie,
    convert_raw_to_df,
    generate_gexdex_chart
)
import pytest
import logging
import pandas as pd
import numpy as np


def test_auth_is_valid(dex_gex_data_extract):
    cookie_val = dex_gex_data_extract["cookie"]
    assert cookie_val is not None


def test_raw_data_contains_ticker(dex_gex_data_extract):
    raw_data = dex_gex_data_extract["raw_data"]
    assert "ticker" in raw_data


def test_clean_data_math(dex_gex_data_extract):
    clean_data = dex_gex_data_extract["clean_data"]
    assert isinstance(clean_data, pd.DataFrame) and not clean_data.empty, "clean_data must be a non-empty DataFrame"


def test_generate_gexdex_chart_valid_dataframe():
    """
    Tests that generate_gexdex_chart accepts a valid options DataFrame and returns valid PNG bytes.
    """
    rows = []
    strikes = [290, 300, 310, 320, 330]
    expirations = ['2026-08-07', '2026-08-14']

    for s in strikes:
        for exp in expirations:
            rows.append({
                'strike': s,
                'expiration': exp,
                'exp_str': exp,
                'exp_call_gex': 1.5e9,
                'exp_put_gex': 1.2e9,
                'exp_call_dex': 2.5e7,
                'exp_put_dex': 1.8e7,
                'ticker': 'TSLA',
                'spot_price': 310.0,
                'call_wall': 320.0,
                'put_wall': 300.0,
                'call_put_ratio': 0.5
            })

    df = pd.DataFrame(rows)
    img_bytes = generate_gexdex_chart(df)

    assert isinstance(img_bytes, bytes), "Returned value must be bytes"
    assert len(img_bytes) > 0, "PNG image bytes must not be empty"
    assert img_bytes.startswith(b'\x89PNG\r\n\x1a\n'), "Binary header must match PNG magic bytes format"


def test_generate_gexdex_chart_with_custom_metadata():
    """
    Tests generate_gexdex_chart with explicit parameter overrides for ticker, spot price, and walls.
    """
    df = pd.DataFrame({
        'strike': [100, 105, 110],
        'exp_str': ['2026-08-07', '2026-08-07', '2026-08-07'],
        'call_gex': [1e8, 2e8, 3e8],
        'put_gex': [1e8, 2e8, 3e8],
        'call_dex': [1e7, 2e7, 3e7],
        'put_dex': [1e7, 2e7, 3e7]
    })

    img_bytes = generate_gexdex_chart(
        df,
        ticker="AAPL",
        spot_price=105.0,
        call_wall=110.0,
        put_wall=100.0,
        call_put_ratio=0.8
    )

    assert isinstance(img_bytes, bytes)
    assert len(img_bytes) > 1000, "Generated PNG image should be a non-trivial file"
    assert img_bytes.startswith(b'\x89PNG\r\n\x1a\n')


def test_generate_gexdex_chart_empty_dataframe_raises_value_error():
    """
    Tests that passing an empty DataFrame raises ValueError.
    """
    empty_df = pd.DataFrame()
    with pytest.raises(ValueError, match="Cannot generate GEX/DEX chart"):
        generate_gexdex_chart(empty_df)


def test_generate_gexdex_chart_webp_format():
    """
    Tests generate_gexdex_chart with format='webp' outputs valid WebP binary.
    """
    df = pd.DataFrame({
        'strike': [100, 105, 110],
        'exp_str': ['2026-08-07', '2026-08-07', '2026-08-07'],
        'call_gex': [1e8, 2e8, 3e8],
        'put_gex': [1e8, 2e8, 3e8],
        'call_dex': [1e7, 2e7, 3e7],
        'put_dex': [1e7, 2e7, 3e7]
    })
    img_bytes = generate_gexdex_chart(df, ticker="AAPL", format="webp")
    assert isinstance(img_bytes, bytes)
    assert len(img_bytes) > 500
    assert img_bytes.startswith(b'RIFF') and b'WEBP' in img_bytes[:16]