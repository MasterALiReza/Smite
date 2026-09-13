"""
Smite Node - Lightweight Agent
"""
import asyncio
import logging
import random
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.routers import agent
from app.panel_client import PanelClient
from app.core_adapters import AdapterManager

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


async def registration_loop(panel_client: PanelClient):
    """Periodic registration loop to pick up FRP config changes"""
    while True:
        try:
            await asyncio.sleep(60 + random.uniform(0, 15))  # Re-register every ~60s with jitter
            if panel_client and panel_client.client:
                await panel_client.register_with_panel()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.debug(f"Periodic registration error (will retry): {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events"""
    h2_client = PanelClient()
    registration_task = None
    try:
        await h2_client.start()
        app.state.h2_client = h2_client
        
        try:
            await h2_client.register_with_panel()
        except Exception as e:
            logger.warning(f"Could not register with panel: {e}")
            logger.warning("Node will continue running but manual registration may be needed")
        
        registration_task = asyncio.create_task(registration_loop(h2_client))
        app.state.registration_task = registration_task
    except Exception as e:
        logger.error(f"Failed to start Panel client: {e}")
        logger.error("Node API will still be available, but panel connection will not work")
        logger.error("Make sure CA certificate is available at the configured path")
        app.state.h2_client = None
    
    adapter_manager = AdapterManager()
    app.state.adapter_manager = adapter_manager
    
    if not settings.node_api_token:
        logger.warning("SECURITY WARNING: NODE_API_TOKEN is empty. Node agent API (/api/agent/*) is accepting unauthenticated requests. Set NODE_API_TOKEN in .env for production.")
    
    try:
        await adapter_manager.restore_tunnels()
    except Exception as e:
        logger.error(f"Failed to restore tunnels on startup: {e}", exc_info=True)
    
    yield
    if hasattr(app.state, 'registration_task') and app.state.registration_task:
        app.state.registration_task.cancel()
        try:
            await app.state.registration_task
        except asyncio.CancelledError:
            pass
    if hasattr(app.state, 'h2_client') and app.state.h2_client:
        try:
            await app.state.h2_client.stop()
        except:
            pass
    if hasattr(app.state, 'adapter_manager'):
        await app.state.adapter_manager.cleanup()


app = FastAPI(
    title="Smite Node",
    description="Lightweight Tunnel Agent",
    version="1.0.0",
    lifespan=lifespan,
)

# Restrict CORS to panel and localhost; avoid allow_credentials=True with wildcards
allowed_origins = ["*"]
if getattr(settings, "panel_address", ""):
    clean_addr = settings.panel_address.replace("https://", "").replace("http://", "").strip()
    if clean_addr:
        allowed_origins = [
            f"http://{clean_addr}",
            f"https://{clean_addr}",
            "http://localhost",
            "http://127.0.0.1",
        ]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


app.include_router(agent.router, prefix="/api/agent", tags=["agent"])


@app.get("/")
async def root():
    return {"status": "ok", "service": "smite-node"}


if __name__ == "__main__":
    import uvicorn
    try:
        uvicorn.run(app, host="0.0.0.0", port=settings.node_api_port)
    except Exception as e:
        logger.error(f"Failed to start server: {e}", exc_info=True)
        raise

