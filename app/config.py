from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    contact_email: str = "parlcards@example.com"
    cache_dir: str = "cache"
    session: str = "45-1"

    # TTLs in seconds
    ttl_politician_list: int = 172800   # 48h
    ttl_politician_detail: int = 172800 # 48h
    ttl_session_votes: int = 21600      # 6h — must be ≤ ttl_ballots to prevent attendance > 100%
    ttl_vote_detail: int = 315360000    # 10 years (permanent)
    ttl_ballots: int = 21600            # 6h
    ttl_speeches: int = 14400           # 4h
    ttl_bills: int = 43200              # 12h
    ttl_rankings: int = 315360000        # 10 years — warmup owns invalidation on restart

    government_party: str = "Liberal"

    recess_multiplier: float = 5.0
    free_vote_threshold: float = 0.3
    rate_limit_per_minute: int = 60
    min_delay_seconds: float = 1.0


settings = Settings()
