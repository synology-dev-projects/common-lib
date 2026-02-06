from pydantic import SecretStr, Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, SecretStr, model_validator
from pathlib import Path
import logging
import sys

# 1. Calculate the Dynamic Path
# Start: git-repos/env/project_root/src/connectors/main_config.py -> End: git-repos/conf/.env
current_file_path = Path(__file__).resolve()
project_root_path = current_file_path.parent.parent.parent
target_env_path = str(project_root_path / ".env")



class MainConfig(BaseSettings):
    """
    Central configuration loader.
    Reads from environment variables or a .env file.
    """

    # --- ORACLE DATABASE SETTINGS ---
    oracle_user: str = Field(...)
    oracle_pass: SecretStr = Field(...)
    oracle_host_ip: str = Field(...)
    oracle_service: str = Field(...)
    te_cookie: SecretStr = Field(...)
    target_env_path : str = target_env_path


    # API Constants (Can be defaults since they rarely change)
    te_base_url: str = "https://tradingedge.club/api/web/v1/spaces/20140900/feed"
    te_user_agent: str = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36..."

    oracle_quant_table_name: str = "QUANT_LVL_DATA_TE"
    oracle_quant_pks: [str] = ['DATETIME', 'TICKER', 'START_LVL_PRICE']

    # Pydantic Config: Tells it to look for a file named .env
    model_config = SettingsConfigDict(
        env_file=str(target_env_path),
        env_file_encoding="utf-8",
        extra="ignore"  # Ignore extra keys in .env
    )


def load_config() -> MainConfig:
    """
    Factory function to instantiate config.
    Raises Validation Error if .env is missing required fields.
    """
    return MainConfig()