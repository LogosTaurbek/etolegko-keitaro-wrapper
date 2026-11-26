import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.database import AsyncSessionLocal
from app.keitaro_client import KeitaroAPIError, keitaro_client
from app.router import router
from app.services import get_or_create_offer_cache

logger = logging.getLogger("keitaro_wrapper")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await keitaro_client.init_client()
    # Best-effort initial offer cache warmup when credentials are configured
    if settings.KEITARO_API_KEY and settings.KEITARO_API_KEY != "your_api_key_here":
        try:
            async with AsyncSessionLocal() as db:
                await get_or_create_offer_cache(db)
        except Exception as exc:
            logger.warning("Initial offers cache warmup skipped: %s", exc)
    yield
    await keitaro_client.close()


app = FastAPI(
    title="Keitaro Wrapper & Editor",
    version="2.3.0",
    lifespan=lifespan,
)


@app.exception_handler(KeitaroAPIError)
async def keitaro_api_exception_handler(request: Request, exc: KeitaroAPIError):
    return JSONResponse(status_code=502, content={"detail": str(exc)})


app.include_router(router)

os.makedirs("static", exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
async def read_index():
    index_path = os.path.join("static", "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return {"message": "Keitaro Wrapper API is running. Please place index.html in static/"}
