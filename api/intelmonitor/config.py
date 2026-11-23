"""Settings loaded from the repo-root .env, shared by the CLI and the server."""

import os
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]
API_ROOT = REPO_ROOT / "api"
SEED_DIR = API_ROOT / "seed"
# The image points this at /data, a local disk: the deployed services keep no volume, and the
# store comes from the bucket (store/publish.py).
DATA_DIR = Path(os.environ.get("INTELMONITOR_DATA_DIR", REPO_ROOT / "data"))
# The embedding model's files; the image has them built in, so runs never download them.
MODELS_DIR = Path(os.environ.get("INTELMONITOR_MODELS_DIR", DATA_DIR / "models"))
ARTIFACTS_DIR = REPO_ROOT / "artifacts"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    # Claude runs on the Anthropic API on anthropic_api_key, billed per token, or with
    # CLAUDE_VIA_API=0 on the user's Claude login for local runs (pipeline/claude.py).
    anthropic_api_key: str = ""
    claude_via_api: bool = True
    anthropic_model: str = "claude-sonnet-5-5"

    exa_api_key: str = ""
    exa_api_key_2: str = ""  # used when Exa refuses the first (ingest/sources/search.py)

    fred_api_key: str = ""
    coingecko_api_key: str = ""

    mas_api_base_url: str = "https://eservices.mas.gov.sg/apimg-gw"
    mas_exchange_rates_api_key: str = ""
    mas_bank_asset_quality_api_key: str = ""
    mas_bank_liquidity_api_key: str = ""
    mas_bank_exposure_api_key: str = ""
    mas_sora_key: str = ""

    # The S3-compatible bucket the runner publishes stores to and the site reads from
    # (store/publish.py). With no bucket set, everything uses the local store.
    store_bucket: str = ""
    store_s3_endpoint: str = ""
    store_s3_region: str = ""
    store_s3_access_key_id: str = ""
    store_s3_secret_access_key: str = ""


@lru_cache
def get_settings() -> Settings:
    """Process-wide settings. Environment variables override values in .env."""
    return Settings()
