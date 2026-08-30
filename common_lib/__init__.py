"""
Common Library for Quant System.
Provides shared models, PostgreSQL database connectors, schema auto-migration, and pipelines.
"""

def __getattr__(name: str):
    if name == "database":
        import common_lib.database as database
        return database
    if name == "connectors":
        import common_lib.connectors as connectors
        return connectors
    if name == "config":
        import common_lib.config as config
        return config
    if name == "quant_levels":
        import common_lib.quant_levels as quant_levels
        return quant_levels
    raise AttributeError(f"module 'common_lib' has no attribute '{name}'")