from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .config import Settings
from .models import ExtractTextRequest, ExtractTextResponse, HealthResponse
from .ocr import OcrEngine, PaddleOcrEngine
from .service import TextExtractionService


def create_app(settings: Settings | None = None, engine: OcrEngine | None = None) -> FastAPI:
    config = settings or Settings()
    service = TextExtractionService(config, engine or PaddleOcrEngine())
    app = FastAPI(
        title=config.service_name,
        version=config.service_version,
        description="Local, token-efficient vision operations for AI agents.",
    )

    @app.exception_handler(RuntimeError)
    async def runtime_error_handler(_request: Request, exc: RuntimeError) -> JSONResponse:
        return JSONResponse(status_code=503, content={"detail": str(exc)})

    @app.get("/health", response_model=HealthResponse, tags=["system"])
    async def health() -> HealthResponse:
        return HealthResponse(service=config.service_name, version=config.service_version)

    @app.post(
        "/tools/extract_text_and_layout",
        response_model=ExtractTextResponse,
        tags=["vision tools"],
        summary="Extract text and layout metadata from an image",
    )
    async def extract_text_and_layout(request: ExtractTextRequest) -> ExtractTextResponse:
        return service.extract(request)

    return app


app = create_app()

