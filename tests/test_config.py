import logging
import os




def test_find_project_root(env_config):
    """
    Verifies that the .env file exists and that Pydantic reads it correctly.
    """
    expected_end = os.path.join("common_config", ".env")
    logging.debug(f"Env Path is: {env_config.env_file_path}")
    assert str(env_config.env_file_path).endswith(expected_end)


def test_load_config_from_env(env_config):
    """
    Verifies that the .env file exists and that Pydantic reads it correctly.
    """
    print(env_config.model_dump_json(indent=2))
    assert env_config.oracle_user != ""
    assert env_config.oracle_pass != ""
    assert env_config.synology_main_ip != ""
    assert env_config.oracle_service != ""