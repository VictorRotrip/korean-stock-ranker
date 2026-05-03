"""Local data-source helpers used by the ingest scripts.

Each module exposes a small, clearly-named function that returns a pandas
DataFrame with column names normalized to the Korean Stock Ranker schema
(`ticker`, `date`, `close`, `market_cap`, etc.).
"""
