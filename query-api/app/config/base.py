from pydantic_settings import BaseSettings


class AppConfig(BaseSettings):
    DATABASE_URL: str = "postgresql://user:password@postgres/general_api_db"
    RUN_MIGRATIONS_UPON_LAUNCH: bool = True
    REDIS_URL: str = "redis://localhost:6379"

    model_config = {"env_file": ".env"}


CONFIG = AppConfig()
