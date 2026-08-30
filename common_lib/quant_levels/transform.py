import logging
from typing import List, Dict, Any, Optional
import datetime
import re
import pandas as pd

from common_lib.config.main_config import MainConfig as Config

logger = logging.getLogger(__name__)


def run(config: Config, raw_posts_json: list) -> pd.DataFrame:
    """
    Take the unstructured json data from extract and normalizes it here into a structured df
    :param config:
    :param raw_posts_json:
    :return: cleaned pd.DataFrame
    """
    quant_df_with_dupes = _parse_quant_levels_to_data(raw_posts_json)
    if quant_df_with_dupes.empty:
        return quant_df_with_dupes
    deduplicated_days_df = _deduplicate_days(quant_df_with_dupes)
    deduplicated_rows_df = _deduplicate_rows(config, deduplicated_days_df)
    return _clean_df(config, deduplicated_rows_df)


def _parse_quant_levels_to_data(posts: list) -> pd.DataFrame:
    """
    Parses 'quant_lvl_text' from a list of posts into a structured list of dictionaries
    ready for a Pandas DataFrame.
    """
    parsed_rows = []

    # Regex 1: Split sections by "---" (handling variation in dash count/spacing)
    section_split_pattern = re.compile(r'\n\s*-{3,}\s*\n?')

    # Regex 2: Line Parser
    # Group 1: Start Price /Group 2: End Price /Group 3: Comment (Optional)
    line_pattern = re.compile(r'^\s*(\d{2,6}(?:\.\d+)?)(?:\s*-\s*(\d{2,6}(?:\.\d+)?))?\s*(.*)')

    all_posts_with_quant_lvl = [post for post in posts if post.get('quant_lvl_text')]

    for post in all_posts_with_quant_lvl:
        date_of_post = post.get('date_posted')

        logger.info(f"Parsing post: {date_of_post}:{post.get('title')}")
        # DATETIME COL
        if not date_of_post:
            continue
        date_val = datetime.datetime.fromisoformat(date_of_post.replace("Z", "+00:00"))

        # 1. Split text into sections based on '---'
        raw_text = post.get('quant_lvl_text', '')
        sections = section_split_pattern.split(raw_text)

        # 2. Iterate through sections and assign Buy/Sell indicator
        for i, section_content in enumerate(sections):

            # Section 0 = First block / Section 1 = Second block / Section 2 = Third block
            if i == 0:
                buy_sell_ind = None
            elif i == 1:
                buy_sell_ind = "BUY"
            elif i == 2:
                buy_sell_ind = "SELL"
            else:
                buy_sell_ind = None  # Fallback for unexpected extra sections

            # 3. Process lines within this section
            lines = section_content.strip().split('\n')

            for line in lines:
                clean_line = line.strip()
                if not clean_line:
                    continue

                match = line_pattern.match(clean_line)    # Match the line against the price regex

                if match:
                    # FIRST_PRICE_LVL COL
                    price_start = float(match.group(1))
                    # SECOND_PRICE_LVL COL (OPTIONAL)
                    price_end = float(match.group(2)) if match.group(2) else None

                    # COMMENT COL (OPTIONAL)
                    comment = match.group(3).strip()
                    if comment:
                        # Remove leading separators like ": " or "- " from the comment
                        comment = re.sub(r'^[:\-\s]+', '', comment)
                    else:  # Store None if comment is empty string
                        comment = None

                    # Build the row
                    row = {
                        "DATETIME": date_val,
                        "TICKER": "SPX",  # Defaulting to SPX as context implies index levels
                        "START_LVL_PRICE": price_start,
                        "END_LVL_PRICE": price_end,
                        "COMMENTS": comment,
                        "BUY_SELL_IND": buy_sell_ind,
                        "WEB_LINK": post.get('link')
                    }

                    parsed_rows.append(row)

    return _define_quant_dataframe(parsed_rows)


def _define_quant_dataframe(parsed_data: list) -> pd.DataFrame:
    """
    Converts a list of parsed quant level dictionaries into a pandas DataFrame.
    Enforces types for Datetime and Float columns.

    :param parsed_data: List of dicts, typically output from parse_quant_levels_to_data()
    :return: pd.DataFrame
    """
    logger.info("Defining DataFrame from parsed quant data...")

    cols = ["DATETIME", "TICKER", "START_LVL_PRICE", "END_LVL_PRICE", "COMMENTS", "BUY_SELL_IND", "WEB_LINK"]
    if not parsed_data:
        logger.info("Parsing into data returned no rows.")
        return pd.DataFrame(columns=cols)

    # 1. Create DataFrame from list of dicts
    df = pd.DataFrame(parsed_data)
    df = df[cols]

    # 2. Check if data exists
    if df.empty:
        return df

    # 3. Enforce Data Types
    df['DATETIME'] = pd.to_datetime(df['DATETIME'], errors='coerce')
    df['START_LVL_PRICE'] = pd.to_numeric(df['START_LVL_PRICE'], errors='coerce')
    df['END_LVL_PRICE'] = pd.to_numeric(df['END_LVL_PRICE'], errors='coerce')
    if 'COMMENTS' in df.columns:
        df['COMMENTS'] = df['COMMENTS'].astype(str).str.replace('\xa0', ' ')
        df["COMMENTS"] = df["COMMENTS"].astype("string")
    if 'BUY_SELL_IND' in df.columns:
        df["BUY_SELL_IND"] = df["BUY_SELL_IND"].astype("string")
    if 'WEB_LINK' in df.columns:
        df["WEB_LINK"] = df["WEB_LINK"].astype("string")

    return df


def _deduplicate_days(df: pd.DataFrame) -> pd.DataFrame:
    """
    Filters the DataFrame to keep only the records associated with the
    LATEST datetime for each calendar date.

    Uses a vectorized 'transform' (SQL Window Function equivalent) for efficiency.
    """
    logger.info("Deduplicating Days for df...")
    if df.empty:
        return df

    # 1. Create a temporary date column for grouping
    df_copy = df.copy()
    df_copy['temp_date'] = df_copy['DATETIME'].dt.date

    # 2. Calculate the Window Function
    df_copy['latest_datetime_of_day'] = df_copy.groupby('temp_date')['DATETIME'].transform('max')

    # 3. Filter
    deduped_df = df_copy[df_copy['DATETIME'] == df_copy['latest_datetime_of_day']].copy()
    deduped_df = deduped_df.drop(columns=['temp_date', 'latest_datetime_of_day'])

    return deduped_df


def _deduplicate_rows(config: Config, df: pd.DataFrame) -> pd.DataFrame:
    """
    Deduplicates a DataFrame based on a primary key of (DATETIME, TICKER, START_LVL_PRICE).

    Merge Logic:
    1. Group by primary key.
    2. For numeric columns (like END_LVL_PRICE): take the first non-null value.
    3. For string columns (COMMENTS, WEB_LINK, etc.):
       - Ignore nulls.
       - Concatenate distinct, non-empty strings with ' | '.
       - If only one unique value exists, keep that value.
    """
    if df.empty:
        return df

    df_copy = df.copy()
    # normalize
    df_copy['DATETIME'] = pd.to_datetime(df_copy['DATETIME'], utc=True).dt.tz_localize(None)
    df_copy['DATETIME'] = df_copy['DATETIME'].dt.floor('1s')
    df_copy['START_LVL_PRICE'] = pd.to_numeric(df_copy['START_LVL_PRICE'], errors='coerce')
    df_copy['START_LVL_PRICE'] = df_copy['START_LVL_PRICE'].round(2)
    df_copy['TICKER'] = df_copy['TICKER'].astype(str).str.strip()

    primary_key = getattr(config, 'oracle_quant_pks', ['DATETIME', 'TICKER', 'START_LVL_PRICE'])
    deduped_df = df_copy.groupby(primary_key, as_index=False, dropna=False).agg(merge_logic)

    return deduped_df


def merge_logic(series):
    # 1. Drop NA values
    valid_values = series.dropna()

    # 2. If no valid values, return None (or NaN)
    if valid_values.empty:
        return None

    # 3. Handle Numeric Columns (e.g. END_LVL_PRICE)
    if pd.api.types.is_numeric_dtype(series):
        return valid_values.iloc[0]

    # 4. Handle String/Object Columns
    else:
        # Convert to string, strip whitespace, and get unique values
        unique_vals = sorted(set(str(v).strip() for v in valid_values if str(v).strip() and str(v).strip() != '<NA>'))

        # If nothing remains after stripping, return None
        if not unique_vals:
            return None

        # Concatenate unique strings
        return " | ".join(unique_vals)


def _clean_df(config: Config, df: pd.DataFrame) -> pd.DataFrame:
    """
    Normalizes timestamps and validates PK integrity.
    """
    if df.empty:
        return df

    df_clean = df.copy()
    # normalize timestamps
    df_clean['DATETIME'] = pd.to_datetime(df_clean['DATETIME']).dt.normalize().dt.tz_localize(None)

    # check for duplicates based off of pk
    pks = getattr(config, 'oracle_quant_pks', ['DATETIME', 'TICKER', 'START_LVL_PRICE'])
    if df_clean.duplicated(subset=pks).any():
        logger.error("Integrity Error: Duplicate keys found.")

    if df_clean[pks].isnull().any().any():
        logger.error("Integrity Error: PK columns contain Nulls.")

    return df_clean
