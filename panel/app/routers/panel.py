"""Panel API endpoints"""
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, Response
from pathlib import Path
import logging
from app.config import settings

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/ca")
async def get_ca_cert(download: bool = False):
    """Get CA certificate for Iran node enrollment"""
    from app.node_server import NodeServer
    import os
    
    cert_path_str = settings.node_cert_path
    cert_path = Path(cert_path_str)
    
    if not cert_path.is_absolute():
        base_dir = Path(os.getcwd())
        cert_path = base_dir / cert_path
    
    cert_path.parent.mkdir(parents=True, exist_ok=True)
    
    needs_generation = False
    if not cert_path.exists():
        needs_generation = True
        logger.info(f"CA certificate missing at {cert_path}, generating...")
    elif cert_path.stat().st_size == 0:
        needs_generation = True
        logger.info(f"CA certificate is empty (0 bytes) at {cert_path}, deleting and regenerating...")
        try:
            cert_path.unlink()
        except:
            pass
    
    if needs_generation:
        h2_server = NodeServer()
        h2_server.cert_path = str(cert_path)
        h2_server.key_path = str(cert_path.parent / "ca.key")
        await h2_server._generate_certs()
        logger.info(f"Certificate generated at {cert_path}")
    
    if not cert_path.exists():
        raise HTTPException(status_code=500, detail=f"Failed to generate CA certificate at {cert_path}")
    
    try:
        cert_content = cert_path.read_text()
        if not cert_content or not cert_content.strip():
            raise HTTPException(status_code=500, detail="CA certificate is empty after generation")
    except Exception as e:
        logger.error(f"Error reading certificate: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to read certificate: {str(e)}")
    
    if download:
        return FileResponse(
            cert_path,
            media_type="application/x-pem-file",
            filename="ca.crt",
            headers={"Content-Disposition": "attachment; filename=ca.crt"}
        )
    
    return Response(content=cert_content, media_type="text/plain")


@router.get("/ca/server")
async def get_server_ca_cert(download: bool = False):
    """Get CA certificate for foreign server enrollment"""
    from app.node_server import NodeServer
    import os
    
    cert_path_str = settings.node_server_cert_path
    cert_path = Path(cert_path_str)
    
    if not cert_path.is_absolute():
        base_dir = Path(os.getcwd())
        cert_path = base_dir / cert_path
    
    cert_path.parent.mkdir(parents=True, exist_ok=True)
    
    needs_generation = False
    if not cert_path.exists():
        needs_generation = True
        logger.info(f"Server CA certificate missing at {cert_path}, generating...")
    elif cert_path.stat().st_size == 0:
        needs_generation = True
        logger.info(f"Server CA certificate is empty (0 bytes) at {cert_path}, deleting and regenerating...")
        try:
            cert_path.unlink()
        except:
            pass
    
    if needs_generation:
        h2_server = NodeServer()
        h2_server.cert_path = str(cert_path)
        h2_server.key_path = str(cert_path.parent / "ca-server.key")
        await h2_server._generate_certs(common_name="Smite Server CA")
        logger.info(f"Server certificate generated at {cert_path}")
    
    if not cert_path.exists():
        raise HTTPException(status_code=500, detail=f"Failed to generate server CA certificate at {cert_path}")
    
    try:
        cert_content = cert_path.read_text()
        if not cert_content or not cert_content.strip():
            raise HTTPException(status_code=500, detail="Server CA certificate is empty after generation")
    except Exception as e:
        logger.error(f"Error reading server certificate: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to read server certificate: {str(e)}")
    
    if download:
        return FileResponse(
            cert_path,
            media_type="application/x-pem-file",
            filename="ca-server.crt",
            headers={"Content-Disposition": "attachment; filename=ca-server.crt"}
        )
    
    return Response(content=cert_content, media_type="text/plain")


@router.get("/health")
async def health():
    """Health check"""
    return {"status": "ok"}

