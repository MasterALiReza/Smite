"""
Smite Panel - Central Controller
"""
import os
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.database import AsyncSessionLocal
from app.models import Tunnel, Node, CoreResetConfig

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.database import init_db
from app.routers import nodes, tunnels, panel, status, logs, auth, core_health
from app.routers import settings as settings_router
from app.node_server import NodeServer
from app.gost_forwarder import gost_forwarder
from app.rathole_server import rathole_server_manager
from app.backhaul_manager import backhaul_manager
from app.chisel_server import chisel_server_manager
from app.frp_server import frp_server_manager
from app.frp_comm_manager import frp_comm_manager
from app.telegram_bot import telegram_bot
from app.node_client import NodeClient
from app.models import Settings
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events"""
    await init_db()
    
    h2_server = NodeServer()
    await h2_server.start()
    app.state.h2_server = h2_server
    
    try:
        cert_path = Path(settings.node_cert_path)
        if not cert_path.is_absolute():
            cert_path = Path(os.getcwd()) / cert_path
        
        if not cert_path.exists() or cert_path.stat().st_size == 0:
            logger.info("Generating CA certificate for Iran nodes on startup...")
            h2_server.cert_path = str(cert_path)
            h2_server.key_path = str(cert_path.parent / "ca.key")
            await h2_server._generate_certs(common_name="Smite CA")
            logger.info(f"CA certificate generated at {cert_path}")
    except Exception as e:
        logger.warning(f"Failed to generate CA certificate on startup: {e}")
    
    try:
        server_cert_path = Path(settings.node_server_cert_path)
        if not server_cert_path.is_absolute():
            server_cert_path = Path(os.getcwd()) / server_cert_path
        
        if not server_cert_path.exists() or server_cert_path.stat().st_size == 0:
            logger.info("Generating CA certificate for foreign servers on startup...")
            h2_server.cert_path = str(server_cert_path)
            h2_server.key_path = str(server_cert_path.parent / "ca-server.key")
            await h2_server._generate_certs(common_name="Smite Server CA")
            logger.info(f"Server CA certificate generated at {server_cert_path}")
    except Exception as e:
        logger.warning(f"Failed to generate server CA certificate on startup: {e}")
    
    app.state.gost_forwarder = gost_forwarder
    
    app.state.rathole_server_manager = rathole_server_manager
    app.state.backhaul_manager = backhaul_manager
    app.state.chisel_server_manager = chisel_server_manager
    app.state.frp_server_manager = frp_server_manager
    app.state.frp_comm_manager = frp_comm_manager
    
    await _load_and_start_frp_comm()
    await _load_and_start_telegram_bot()
    await _load_and_start_tunnel_reapply()
    
    await _restore_forwards()
    
    await _restore_rathole_servers()
    await _restore_backhaul_servers()
    await _restore_chisel_servers()
    await _restore_frp_servers()
    
    await _restore_node_tunnels()
    
    gost_forwarder.start_monitor()
    
    reset_task = asyncio.create_task(_auto_reset_scheduler(app))
    app.state.reset_task = reset_task
    
    yield
    
    if hasattr(app.state, 'reset_task'):
        app.state.reset_task.cancel()
        try:
            await app.state.reset_task
        except asyncio.CancelledError:
            pass
    
    if hasattr(app.state, 'h2_server'):
        await app.state.h2_server.stop()
    
    if hasattr(app.state, 'frp_comm_manager'):
        await app.state.frp_comm_manager.stop()
    
    await telegram_bot.stop()
    
    await gost_forwarder.cleanup_all()


async def _restore_forwards():
    """Restore forwarding for active tunnels on startup"""
    try:
        logger.info("Starting to restore forwarding for active tunnels...")
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(Tunnel).where(Tunnel.status == "active"))
            tunnels = result.scalars().all()
            logger.info(f"Found {len(tunnels)} active tunnels to restore")
            
            for tunnel in tunnels:
                logger.info(f"Checking tunnel {tunnel.id}: type={tunnel.type}, core={tunnel.core}, node_id={tunnel.node_id}")
                needs_gost_forwarding = tunnel.type in ["tcp", "udp", "ws", "grpc", "tcpmux"] and tunnel.core == "gost" and not tunnel.node_id
                if not needs_gost_forwarding:
                    continue
                
                listen_port = tunnel.spec.get("listen_port")
                forward_to = tunnel.spec.get("forward_to")
                
                if not forward_to:
                    remote_ip = tunnel.spec.get("remote_ip", "127.0.0.1")
                    remote_port = tunnel.spec.get("remote_port", 8080)
                    forward_to = f"{remote_ip}:{remote_port}"
                
                panel_port = listen_port or tunnel.spec.get("remote_port")
                if not panel_port or not forward_to:
                    logger.warning(f"Tunnel {tunnel.id}: Missing panel_port or forward_to, skipping restore")
                    continue
                
                try:
                    use_ipv6 = tunnel.spec.get("use_ipv6", False)
                    logger.info(f"Restoring gost forwarding for tunnel {tunnel.id}: {tunnel.type}://:{panel_port} -> {forward_to}, use_ipv6={use_ipv6}")
                    await gost_forwarder.start_forward(
                        tunnel_id=tunnel.id,
                        local_port=int(panel_port),
                        forward_to=forward_to,
                        tunnel_type=tunnel.type,
                        use_ipv6=bool(use_ipv6)
                    )
                    logger.info(f"Successfully restored gost forwarding for tunnel {tunnel.id}")
                except Exception as e:
                    logger.error(f"Failed to restore forwarding for tunnel {tunnel.id}: {e}", exc_info=True)
    except Exception as e:
        logger.error(f"Error restoring forwards: {e}")


async def _restore_rathole_servers():
    """Restore Rathole servers for active tunnels on startup"""
    try:
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(Tunnel).where(Tunnel.status == "active"))
            tunnels = result.scalars().all()
            
            for tunnel in tunnels:
                if tunnel.core != "rathole" or tunnel.node_id or getattr(tunnel, "iran_node_id", None) or getattr(tunnel, "foreign_node_id", None):
                    continue
                
                remote_addr = tunnel.spec.get("remote_addr")
                token = tunnel.spec.get("token")
                proxy_port = tunnel.spec.get("remote_port") or tunnel.spec.get("listen_port")
                
                if not remote_addr or not token or not proxy_port:
                    continue
                
                use_ipv6 = tunnel.spec.get("use_ipv6", False)
                await rathole_server_manager.start_server(
                    tunnel_id=tunnel.id,
                    remote_addr=remote_addr,
                    token=token,
                    proxy_port=int(proxy_port),
                    use_ipv6=bool(use_ipv6)
                )
    except Exception as e:
        logger.error(f"Error restoring Rathole servers: {e}")


async def _restore_backhaul_servers():
    """Restore Backhaul servers for active tunnels on startup"""
    try:
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(Tunnel).where(Tunnel.status == "active"))
            tunnels = result.scalars().all()

            for tunnel in tunnels:
                if tunnel.core != "backhaul" or tunnel.node_id or getattr(tunnel, "iran_node_id", None) or getattr(tunnel, "foreign_node_id", None):
                    continue

                try:
                    await backhaul_manager.start_server(tunnel.id, tunnel.spec or {})
                except Exception as exc:
                    logger.error(
                        "Failed to restore Backhaul server for tunnel %s: %s",
                        tunnel.id,
                        exc,
                    )
    except Exception as exc:
        logger.error("Error restoring Backhaul servers: %s", exc)


async def _restore_chisel_servers():
    """Restore Chisel servers for active tunnels on startup"""
    try:
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(Tunnel).where(Tunnel.status == "active"))
            tunnels = result.scalars().all()
            
            for tunnel in tunnels:
                if tunnel.core != "chisel" or tunnel.node_id or getattr(tunnel, "iran_node_id", None) or getattr(tunnel, "foreign_node_id", None):
                    continue
                
                listen_port = tunnel.spec.get("listen_port") or tunnel.spec.get("remote_port") or tunnel.spec.get("server_port")
                auth = tunnel.spec.get("auth")
                fingerprint = tunnel.spec.get("fingerprint")
                
                if not listen_port:
                    continue
                
                try:
                    use_ipv6 = tunnel.spec.get("use_ipv6", False)
                    server_control_port = tunnel.spec.get("control_port")
                    if server_control_port:
                        server_control_port = int(server_control_port)
                    else:
                        server_control_port = int(listen_port) + 10000
                    await chisel_server_manager.start_server(
                        tunnel_id=tunnel.id,
                        server_port=server_control_port,
                        auth=auth,
                        fingerprint=fingerprint,
                        use_ipv6=bool(use_ipv6)
                    )
                except Exception as exc:
                    logger.error(
                        "Failed to restore Chisel server for tunnel %s: %s",
                        tunnel.id,
                        exc,
                    )
    except Exception as exc:
        logger.error("Error restoring Chisel servers: %s", exc)


async def _restore_frp_servers():
    """Restore FRP servers for active tunnels on startup"""
    try:
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(Tunnel).where(Tunnel.status == "active"))
            tunnels = result.scalars().all()
            
            for tunnel in tunnels:
                if tunnel.core != "frp" or tunnel.node_id or getattr(tunnel, "iran_node_id", None) or getattr(tunnel, "foreign_node_id", None):
                    continue
                
                bind_port = tunnel.spec.get("bind_port", 7000)
                token = tunnel.spec.get("token")
                
                if not bind_port:
                    continue
                
                try:
                    await frp_server_manager.start_server(
                        tunnel_id=tunnel.id,
                        bind_port=int(bind_port),
                        token=token
                    )
                except Exception as exc:
                    logger.error(
                        "Failed to restore FRP server for tunnel %s: %s",
                        tunnel.id,
                        exc,
                    )
    except Exception as exc:
        logger.error("Error restoring FRP servers: %s", exc)


async def _restore_node_tunnels():
    """Sync node-side tunnels with panel database after panel restart
    
    Note: Nodes restore their own tunnels on startup independently.
    This function syncs the panel's view with nodes, but tunnels will
    continue working even if panel is down or this sync fails.
    """
    try:
        logger.info("Starting to sync node-side tunnels with panel database...")
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(Tunnel).where(Tunnel.status == "active"))
            tunnels = result.scalars().all()
            
            logger.info(f"Found {len(tunnels)} active tunnels to check for sync")
            
            # All 5 proxy cores (rathole, backhaul, chisel, frp, gost) are 2-node tunnels managed via build_tunnel_node_specs
            reverse_tunnels = [t for t in tunnels if t.core in ["rathole", "backhaul", "chisel", "frp", "gost"]]
            
            if not reverse_tunnels:
                logger.info("No node-side tunnels to sync")
                return
            
            logger.info(f"Found {len(reverse_tunnels)} active node-side tunnels to sync")
            
            client = NodeClient()
            restored_count = 0
            failed_count = 0
            skipped_count = 0
            
            result_all_nodes = await db.execute(select(Node))
            all_nodes = result_all_nodes.scalars().all()
            foreign_nodes_fallback = [n for n in all_nodes if n.node_metadata and n.node_metadata.get("role") == "foreign"]
            iran_nodes_fallback = [n for n in all_nodes if n.node_metadata and n.node_metadata.get("role") == "iran"]
            
            for tunnel in reverse_tunnels:
                try:
                    iran_node = None
                    foreign_node = None
                    
                    iran_node_id = getattr(tunnel, "iran_node_id", None) or tunnel.node_id
                    if iran_node_id:
                        matched_iran = [n for n in all_nodes if n.id == iran_node_id]
                        if matched_iran:
                            node_obj = matched_iran[0]
                            if node_obj.node_metadata and node_obj.node_metadata.get("role") != "iran":
                                foreign_node = node_obj
                            else:
                                iran_node = node_obj
                    
                    foreign_node_id = getattr(tunnel, "foreign_node_id", None)
                    if foreign_node_id and not foreign_node:
                        matched_foreign = [n for n in all_nodes if n.id == foreign_node_id]
                        if matched_foreign:
                            foreign_node = matched_foreign[0]
                    
                    if not foreign_node and foreign_nodes_fallback:
                        foreign_node = foreign_nodes_fallback[0]
                    
                    if not iran_node and iran_nodes_fallback:
                        iran_node = iran_nodes_fallback[0]
                    
                    if not foreign_node or not iran_node:
                        logger.warning(f"Tunnel {tunnel.id} ({tunnel.core}): Missing foreign or iran node, skipping sync (nodes will restore themselves)")
                        skipped_count += 1
                        continue
                    
                    iran_node_ip = iran_node.node_metadata.get("ip_address") if iran_node.node_metadata else None
                    if not iran_node_ip:
                        logger.warning(f"Tunnel {tunnel.id}: Iran node has no IP address, skipping sync")
                        skipped_count += 1
                        continue
                    
                    foreign_node_ip = foreign_node.node_metadata.get("ip_address") if foreign_node.node_metadata else None
                    
                    from app.spec_builder import build_tunnel_node_specs
                    try:
                        server_spec, client_spec = build_tunnel_node_specs(tunnel, iran_node_ip, foreign_node_ip or iran_node_ip)
                    except Exception as e:
                        logger.error(f"Spec builder failed for tunnel {tunnel.id} during sync: {e}")
                        skipped_count += 1
                        continue
                    
                    if not iran_node.node_metadata.get("api_address"):
                        iran_node.node_metadata["api_address"] = f"http://{iran_node.node_metadata.get('ip_address', iran_node.fingerprint)}:{iran_node.node_metadata.get('api_port', 8888)}"
                        await db.commit()
                    
                    try:
                        ir_status = await client.get_tunnel_status(iran_node.id, tunnel.id)
                        if ir_status and ir_status.get("status") == "success" and ir_status.get("data", {}).get("active"):
                            server_response = {"status": "success", "message": "Already active"}
                            logger.info(f"Tunnel {tunnel.id} is ALREADY active on Iran node {iran_node.id}, skipping disruptive apply")
                        else:
                            logger.info(f"Restoring tunnel {tunnel.id} ({tunnel.core}): applying server config to iran node {iran_node.id}")
                            server_response = await client.send_to_node(
                                node_id=iran_node.id,
                                endpoint="/api/agent/tunnels/apply",
                                data={
                                    "tunnel_id": tunnel.id,
                                    "core": tunnel.core,
                                    "type": tunnel.type,
                                    "spec": server_spec
                                }
                            )
                    except Exception as e:
                        server_response = {"status": "error", "message": str(e)}
                    
                    if server_response.get("status") == "error":
                        error_msg = server_response.get("message", "Unknown error from iran node")
                        logger.error(f"Failed to restore tunnel {tunnel.id} on iran node {iran_node.id}: {error_msg}")
                        failed_count += 1
                        continue
                    
                    if not foreign_node.node_metadata.get("api_address"):
                        foreign_node.node_metadata["api_address"] = f"http://{foreign_node.node_metadata.get('ip_address', foreign_node.fingerprint)}:{foreign_node.node_metadata.get('api_port', 8888)}"
                        await db.commit()
                    
                    try:
                        fn_status = await client.get_tunnel_status(foreign_node.id, tunnel.id)
                        if fn_status and fn_status.get("status") == "success" and fn_status.get("data", {}).get("active"):
                            client_response = {"status": "success", "message": "Already active"}
                            logger.info(f"Tunnel {tunnel.id} is ALREADY active on foreign node {foreign_node.id}, skipping disruptive apply")
                        else:
                            logger.info(f"Restoring tunnel {tunnel.id} ({tunnel.core}): applying client config to foreign node {foreign_node.id}")
                            client_response = await client.send_to_node(
                                node_id=foreign_node.id,
                                endpoint="/api/agent/tunnels/apply",
                                data={
                                    "tunnel_id": tunnel.id,
                                    "core": tunnel.core,
                                    "type": tunnel.type,
                                    "spec": client_spec
                                }
                            )
                    except Exception as e:
                        client_response = {"status": "error", "message": str(e)}
                    
                    if client_response.get("status") == "error":
                        error_msg = client_response.get("message", "Unknown error from foreign node")
                        logger.error(f"Failed to restore tunnel {tunnel.id} on foreign node {foreign_node.id}: {error_msg}")
                        failed_count += 1
                    else:
                        logger.info(f"Successfully restored/confirmed tunnel {tunnel.id} on both nodes")
                        restored_count += 1
                        
                except Exception as e:
                    logger.error(f"Failed to restore tunnel {tunnel.id}: {e}", exc_info=True)
                    failed_count += 1
            
            logger.info(f"Tunnel sync completed: {restored_count} synced, {failed_count} failed, {skipped_count} skipped out of {len(reverse_tunnels)} total")
            logger.info("Note: Nodes restore their own tunnels on startup, so tunnels work even if panel is down")
                    
    except Exception as e:
        logger.error(f"Error restoring node tunnels: {e}", exc_info=True)


async def _load_and_start_frp_comm():
    """Load FRP communication settings and start server if enabled"""
    try:
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(Settings).where(Settings.key == "frp"))
            setting = result.scalar_one_or_none()
            if setting and setting.value and setting.value.get("enabled"):
                port = setting.value.get("port", 7000)
                token = setting.value.get("token")
                success = await frp_comm_manager.start(port, token)
                if success:
                    logger.info(f"FRP communication server started on port {port}")
                else:
                    logger.warning(f"FRP communication server failed to start (binary may not be available)")
            else:
                logger.info("FRP communication is disabled")
    except Exception as e:
        logger.error(f"Error loading FRP communication settings: {e}", exc_info=True)


async def _load_and_start_telegram_bot():
    """Load Telegram bot settings and start bot if enabled"""
    try:
        await telegram_bot.start()
    except Exception as e:
        logger.error(f"Error starting Telegram bot: {e}", exc_info=True)


async def _load_and_start_tunnel_reapply():
    """Load tunnel reapply settings and start task if enabled"""
    try:
        from app.tunnel_reapply_manager import tunnel_reapply_manager
        await tunnel_reapply_manager.start()
    except Exception as e:
        logger.error(f"Error starting tunnel reapply manager: {e}", exc_info=True)


async def _auto_reset_scheduler(app: FastAPI):
    """Background task to auto-reset cores based on timer configuration"""
    from datetime import datetime, timedelta
    from app.routers.core_health import _reset_core
    
    while True:
        try:
            await asyncio.sleep(60)
            
            async with AsyncSessionLocal() as db:
                result = await db.execute(select(CoreResetConfig).where(CoreResetConfig.enabled == True))
                configs = result.scalars().all()
                
                now = datetime.utcnow()
                
                for config in configs:
                    if not config.next_reset:
                        continue
                    
                    if now >= config.next_reset:
                        try:
                            logger.info(f"Auto-resetting {config.core} core (interval: {config.interval_minutes} minutes)")
                            
                            config.last_reset = now
                            config.next_reset = now + timedelta(minutes=config.interval_minutes)
                            await db.commit()
                            await db.refresh(config)  # Ensure config is refreshed after commit
                            
                            await _reset_core(config.core, app, db)
                            
                            logger.info(f"Auto-reset completed for {config.core}, next reset at {config.next_reset}")
                        except Exception as e:
                            logger.error(f"Error in auto-reset for {config.core}: {e}", exc_info=True)
                            await db.rollback()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Error in auto-reset scheduler: {e}", exc_info=True)
            await asyncio.sleep(60)


app = FastAPI(
    title="Smite Panel",
    description="Tunneling Control Panel",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs" if settings.docs_enabled else None,
    redoc_url="/redoc" if settings.docs_enabled else None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()] or ["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
app.include_router(panel.router, prefix="/api/panel", tags=["panel"])
app.include_router(nodes.router, prefix="/api/nodes", tags=["nodes"])
app.include_router(tunnels.router, prefix="/api/tunnels", tags=["tunnels"])
app.include_router(status.router, prefix="/api/status", tags=["status"])
app.include_router(logs.router, prefix="/api/logs", tags=["logs"])
app.include_router(core_health.router, prefix="/api/core-health", tags=["core-health"])
app.include_router(settings_router.router)

static_dir = os.path.join(os.path.dirname(__file__), "static")
static_path = Path(static_dir)

if static_path.exists() and (static_path / "index.html").exists():
    app.mount("/static", StaticFiles(directory=static_path), name="static-assets")
    
    from fastapi.responses import FileResponse
    
    @app.get("/{full_path:path}")
    async def serve_frontend(full_path: str):
        """Serve frontend for all non-API routes with strict path traversal protection"""
        if full_path.startswith("api/") or full_path.startswith("docs") or full_path.startswith("redoc") or full_path.startswith("openapi.json"):
            raise HTTPException(status_code=404)
        
        try:
            resolved_base = static_path.resolve()
            candidate_path = (static_path / full_path).resolve()
            # Verify candidate_path is strictly within static_path directory
            if os.path.commonpath([str(resolved_base), str(candidate_path)]) == str(resolved_base):
                if candidate_path.is_file():
                    return FileResponse(candidate_path)
        except Exception:
            pass
        
        index_path = static_path / "index.html"
        if index_path.exists():
            return FileResponse(index_path)
        raise HTTPException(status_code=404)

@app.get("/")
async def root():
    """Root redirect"""
    static_dir = os.path.join(os.path.dirname(__file__), "static")
    index_path = Path(static_dir) / "index.html"
    if index_path.exists():
        from fastapi.responses import FileResponse
        return FileResponse(index_path)
    return {"message": "Smite Panel API", "docs": "/docs"}


if __name__ == "__main__":
    import uvicorn
    
    if settings.https_enabled:
        import ssl
        cert_path = Path(settings.https_cert_path).resolve()
        key_path = Path(settings.https_key_path).resolve()
        
        if not cert_path.exists() or not key_path.exists():
            logger.warning(f"HTTPS enabled but certificate files not found. Using HTTP.")
            uvicorn.run(app, host=settings.panel_host, port=settings.panel_port)
        else:
            ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ssl_context.load_cert_chain(str(cert_path), str(key_path))
            uvicorn.run(
                app,
                host=settings.panel_host,
                port=settings.panel_port,
                ssl_keyfile=str(key_path),
                ssl_certfile=str(cert_path)
            )
    else:
        uvicorn.run(app, host=settings.panel_host, port=settings.panel_port)
