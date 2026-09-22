"""
Embeddings generation module for Economic Events.
Generates 768-dimensional dense vector embeddings using Gemini text-embedding-004.
"""

import os
import time
import json
import logging
import urllib.request
import urllib.error
from typing import List, Dict, Any, Optional
import sqlalchemy as sa

logger = logging.getLogger("quant.common_lib.economic_events.embeddings")

DEFAULT_EMBEDDING_MODEL = "text-embedding-004"
DEFAULT_BATCH_SIZE = 100


def get_gemini_api_key(api_key: Optional[str] = None) -> str:
    """Resolves Gemini API key from parameter or environment."""
    key = api_key or os.getenv("GEMINI_API_KEY", "").strip()
    if not key:
        raise ValueError("GEMINI_API_KEY is not configured in environment or parameters.")
    return key


def generate_embeddings_http(
    texts: List[str],
    api_key: str,
    model: str = DEFAULT_EMBEDDING_MODEL,
    max_retries: int = 3,
    backoff_factor: float = 1.5
) -> List[List[float]]:
    """
    Generates embeddings via direct Google Generative Language REST API.
    Zero dependency on heavy client libraries; lightweight and fast.
    """
    if not texts:
        return []

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:batchEmbedContents?key={api_key}"
    requests_payload = [{"model": f"models/{model}", "content": {"parts": [{"text": t}]}} for t in texts]
    body = json.dumps({"requests": requests_payload}).encode("utf-8")
    headers = {"Content-Type": "application/json"}

    last_err: Optional[Exception] = None
    for attempt in range(1, max_retries + 1):
        try:
            req = urllib.request.Request(url, data=body, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=30) as resp:
                if resp.status == 200:
                    resp_data = json.loads(resp.read().decode("utf-8"))
                    embeddings_list = resp_data.get("embeddings", [])
                    return [item.get("values", []) for item in embeddings_list]
        except urllib.error.HTTPError as he:
            last_err = he
            err_body = he.read().decode("utf-8", errors="replace")
            logger.warning(f"Embedding API HTTP {he.code} (attempt {attempt}/{max_retries}): {err_body}")
            if he.code == 429 or 500 <= he.code < 600:
                time.sleep(backoff_factor ** attempt)
            else:
                break
        except Exception as ex:
            last_err = ex
            logger.warning(f"Embedding error (attempt {attempt}/{max_retries}): {ex}")
            time.sleep(backoff_factor ** attempt)

    raise RuntimeError(f"Failed to generate embeddings after {max_retries} attempts: {last_err}") from last_err


def generate_embeddings(
    texts: List[str],
    api_key: Optional[str] = None,
    model: str = DEFAULT_EMBEDDING_MODEL,
    batch_size: int = DEFAULT_BATCH_SIZE
) -> List[List[float]]:
    """
    Splits texts into batches and generates 768-dim embeddings.
    """
    key = get_gemini_api_key(api_key)
    all_embeddings: List[List[float]] = []

    for i in range(0, len(texts), batch_size):
        chunk = texts[i : i + batch_size]
        logger.info(f"Generating embeddings for batch {i // batch_size + 1} ({len(chunk)} texts)...")
        emb_batch = generate_embeddings_http(chunk, api_key=key, model=model)
        all_embeddings.extend(emb_batch)

    return all_embeddings


def embed_missing_records(
    engine: sa.Engine,
    api_key: Optional[str] = None,
    table_name: str = "economic_events",
    batch_size: int = DEFAULT_BATCH_SIZE
) -> int:
    """
    Queries rows where embedding IS NULL, generates vector embeddings from synthetic_summary,
    and updates the database. Returns count of newly embedded rows.
    """
    try:
        resolved_key = get_gemini_api_key(api_key)
    except ValueError as e:
        logger.warning(f"Skipping vector embedding generation: {e}")
        return 0

    select_sql = sa.text(f"""
        SELECT event_id, event_timestamp, synthetic_summary 
        FROM {table_name} 
        WHERE embedding IS NULL AND synthetic_summary IS NOT NULL 
        LIMIT 500
    """)

    with engine.connect() as conn:
        rows = conn.execute(select_sql).fetchall()

    if not rows:
        logger.info("Zero records require embedding (all records already embedded).")
        return 0

    logger.info(f"Found {len(rows)} records with missing embeddings. Generating in batches...")
    texts = [r[2] for r in rows]
    vectors = generate_embeddings(texts, api_key=resolved_key, batch_size=batch_size)

    update_sql = sa.text(f"""
        UPDATE {table_name} 
        SET embedding = :vector, updated_at = CURRENT_TIMESTAMP 
        WHERE event_id = :event_id AND event_timestamp = :event_timestamp
    """)

    updated_count = 0
    with engine.begin() as conn:
        for row, vec in zip(rows, vectors):
            # Format vector as string for pgvector: '[0.01, -0.02, ...]'
            vec_str = "[" + ",".join(str(f) for f in vec) + "]"
            conn.execute(update_sql, {
                "vector": vec_str,
                "event_id": row[0],
                "event_timestamp": row[1]
            })
            updated_count += 1

    logger.info(f"Successfully embedded and updated {updated_count} records in {table_name}.")
    return updated_count
