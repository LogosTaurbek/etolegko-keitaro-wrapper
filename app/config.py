from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    KEITARO_BASE_URL: str = "https://your-tracker-domain.com"
    KEITARO_API_KEY: str = "your_api_key_here"
    DEFAULT_DOMAIN_NAME: str = "https://in.posdk.xyz/"
    DEFAULT_GROUP_NAME: str = "FORTESTS"
    DEFAULT_SOURCE_NAME: str = "FORTESTS"
    DATABASE_URL: str = "sqlite+aiosqlite:///./keitaro_wrapper.db"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
