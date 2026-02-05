# tests/conftest.py
import pytest
import config.main_config as config


@pytest.fixture(scope="session")
def env_config():
    """Load config once for the whole session."""
    return config.load_config()
