from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, SecretStr, model_validator
from pathlib import Path

# 1. Calculate the Dynamic Path
# Start: git-repos/environment_name/project_root/common_lib/connectors/main_config.py -> End: git-repos/environment_name/common_config/.env

class MainConfig(BaseSettings):
    """
    Central configuration loader.
    Reads from environment variables or a .env file.
    """

    # --- FOLDER LOCATIONS ---
    env_root_path: str = str(Path(__file__).resolve().parent.parent.parent.parent)
    common_config_path: str = str(Path(env_root_path) / "common_config")
    env_file_path : str =  str(Path(common_config_path) / ".env")
    db_catalog_file_path: str = str(Path(common_config_path) / "db_catalog.yaml")

    # --- MAIN IP GATEWAY --- #
    synology_main_ip: str = Field(...)

    # --- CONNECTOR PORTS --- #
    ibkr_gateway_port: int = Field(...)

    # --- ORACLE CREDENTIALS  --- #
    oracle_user: str = Field(...)
    oracle_pass: SecretStr = Field(...)
    oracle_service: str = Field(...)

    # --- ORACLE TABLE INFO --- #

    # --- TE CREDENTIALS --- #
    te_cookie: SecretStr = Field(...)

    # --- NFTY --- #
    ntfy_endpoint : str = Field(...)

    # --- ORACLE TABLE INFO --- #

    oracle_quant_table_name: str = "QUANT_LVL_DATA_TE"
    # TODO to remove this must pk automicatally in oracle functions
    oracle_quant_pks: [str] = ['DATETIME', 'TICKER', 'START_LVL_PRICE']

    oracle_ibkr_ticker_table_name: str = "ticker_data_ibkr"


    # --- #API Constants (Can be defaults since they rarely change) --- #
    te_base_url: str = "https://tradingedge.club/api/web/v1/spaces/20140900/feed"
    te_user_agent: str = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36..."

    # Pydantic Config: Tells it to look for a file named .env
    model_config = SettingsConfigDict(
        env_file=str(env_file_path),
        env_file_encoding="utf-8",
        extra="ignore"  # Ignore extra keys in .env
    )


def load_config() -> MainConfig:
    """
    Factory function to instantiate config.
    Raises Validation Error if .env is missing required fields.
    """
    return MainConfig()