from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, SecretStr, model_validator
from pathlib import Path

# 1. Calculate the Dynamic Path
# Start: git-repos/environment_name/project_root/common_lib/connectors/main_config.py -> End: git-repos/environment_name/common_config/.env

def _get_env_file_path() -> str:
    pkg_dir = Path(__file__).resolve().parent.parent
    candidates = [
        pkg_dir.parent.parent / "common_config" / ".env",
        pkg_dir.parent / "common_config" / ".env",
        Path("/app/common_config/.env")
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return str(candidates[0])

class MainConfig(BaseSettings):
    """
    Central configuration loader.
    Reads from environment variables or a .env file.
    """

    # --- FOLDER LOCATIONS ---
    env_file_path: str = _get_env_file_path()
    common_config_path: str = str(Path(env_file_path).parent)
    env_root_path: str = str(Path(common_config_path).parent)
    db_catalog_file_path: str = str(Path(common_config_path) / "db_catalog.yaml")


    # --- MAIN IP GATEWAY --- #
    synology_main_ip: str = Field(...)

    # --- CONNECTOR PORTS --- #
    ibkr_gateway_port: int = Field(...)

    # --- DB SELECTION --- #
    db_type: str = Field(default="postgres", alias="DB_TYPE")

    # --- POSTGRES CREDENTIALS --- #
    postgres_host: str = Field(default="localhost", alias="POSTGRES_HOST")
    postgres_port: int = Field(default=5432, alias="POSTGRES_PORT")
    postgres_db: str = Field(default="quant_db", alias="POSTGRES_DB")
    postgres_user: str = Field(default="quant_admin", alias="POSTGRES_USER")
    postgres_pass: SecretStr = Field(default=SecretStr("quant_secure_pass"), alias="POSTGRES_PASSWORD")
    postgres_quant_table_name: str = "quant_lvl_data_te"
    postgres_unusual_flow_table_name: str = "unusual_option_flow_te"

    # --- ORACLE CREDENTIALS  --- #
    oracle_user: str = Field(...)
    oracle_pass: SecretStr = Field(...)
    oracle_service: str = Field(...)

    # --- ORACLE TABLE INFO --- #

    # --- TE CREDENTIALS --- #
    te_cookie: SecretStr = Field(...)
    te_dex_gex_url: str = "https://tools.tradingedge.club/api/dex/data"
    te_option_flow_url: str = "https://flow.tradingedge.club"
    te_login_gate: str = "https://tools.tradingedge.club/gate"
    te_option_login_gate: str = "https://flow.tradingedge.club/Login.aspx?ReturnUrl=%2fdefault.aspx"
    te_pass: SecretStr = Field(default=SecretStr("GoWithTheFlow"))

    # --- NFTY --- #
    ntfy_endpoint : str = Field(...)

    # --- ORACLE TABLE INFO --- #

    oracle_quant_table_name: str = "QUANT_LVL_DATA_TE"
    # TODO to remove this must pk automicatally in oracle functions
    oracle_quant_pks: list[str] = ['DATETIME', 'TICKER', 'START_LVL_PRICE']

    oracle_ibkr_ticker_table_name: str = "ticker_data_ibkr"
    oracle_unusual_flow_table_name: str = "UNUSUAL_OPTION_FLOW_TE"
    oracle_unusual_flow_pks: list[str] = ['FLOW_ID']


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
    return MainConfig() # pyright: ignore[reportCallIssue]