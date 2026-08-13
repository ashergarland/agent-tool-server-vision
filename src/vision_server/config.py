from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="VISION_", env_file=".env", extra="ignore")

    service_name: str = "agent-tool-server-vision"
    service_version: str = "0.1.0"
    max_image_bytes: int = Field(default=10 * 1024 * 1024, ge=1)
    max_image_pixels: int = Field(default=25_000_000, ge=1)
    default_language: str = "en"
