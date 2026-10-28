"""Panel API endpoints"""
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, Response
from pathlib import Path
import logging
from app.config import settings
from app.models import Admin
from typing import Optional
from app.routers.auth import get_current_user, get_current_user_optional
from pydantic import BaseModel

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/ca")
async def get_ca_cert(download: bool = False, current_user: Optional[Admin] = Depends(get_current_user_optional)):
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
    elif cert_path.stat().st_size == 0:
        needs_generation = True
    
    if needs_generation:
        if not current_user:
            logger.warning(f"Unauthenticated request to /ca attempted to trigger certificate generation")
            raise HTTPException(status_code=404, detail="CA certificate not initialized. Admin login required.")

        from app.node_server import trust_material_generation_allowed
        if not await trust_material_generation_allowed(True):
            raise HTTPException(
                status_code=409,
                detail="CA certificate is missing but nodes are registered or the mutation guard is engaged; "
                       "restore certs/ca.crt and certs/ca.key from backup instead of minting a new CA.",
            )

        try:
            cert_path.unlink()
        except OSError:
            pass

        logger.info(f"Generating CA certificate at {cert_path}...")
        h2_server = NodeServer()
        h2_server.cert_path = str(cert_path)
        h2_server.key_path = str(cert_path.parent / "ca.key")
        await h2_server._generate_certs()
        logger.info(f"Certificate generated successfully")
    
    if not cert_path.exists():
        logger.error(f"Failed to find or generate CA certificate at {cert_path}")
        raise HTTPException(status_code=500, detail="Failed to generate CA certificate")
    
    try:
        cert_content = cert_path.read_text()
        if not cert_content or not cert_content.strip():
            raise HTTPException(status_code=500, detail="CA certificate is empty")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error reading certificate at {cert_path}: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to read certificate")
    
    if download:
        return FileResponse(
            cert_path,
            media_type="application/x-pem-file",
            filename="ca.crt",
            headers={"Content-Disposition": "attachment; filename=ca.crt"}
        )
    
    return Response(content=cert_content, media_type="text/plain")


@router.get("/ca/server")
async def get_server_ca_cert(download: bool = False, current_user: Optional[Admin] = Depends(get_current_user_optional)):
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
    elif cert_path.stat().st_size == 0:
        needs_generation = True
    
    if needs_generation:
        if not current_user:
            logger.warning(f"Unauthenticated request to /ca/server attempted to trigger certificate generation")
            raise HTTPException(status_code=404, detail="Server CA certificate not initialized. Admin login required.")

        from app.node_server import trust_material_generation_allowed
        if not await trust_material_generation_allowed(True):
            raise HTTPException(
                status_code=409,
                detail="Server CA certificate is missing but nodes are registered or the mutation guard is engaged; "
                       "restore certs/ca-server.crt and certs/ca-server.key from backup instead of minting a new CA.",
            )

        try:
            cert_path.unlink()
        except OSError:
            pass

        logger.info(f"Generating Server CA certificate at {cert_path}...")
        h2_server = NodeServer()
        h2_server.cert_path = str(cert_path)
        h2_server.key_path = str(cert_path.parent / "ca-server.key")
        await h2_server._generate_certs(common_name="Smite Server CA")
        logger.info(f"Server certificate generated successfully")
    
    if not cert_path.exists():
        logger.error(f"Failed to find or generate server CA certificate at {cert_path}")
        raise HTTPException(status_code=500, detail="Failed to generate server CA certificate")
    
    try:
        cert_content = cert_path.read_text()
        if not cert_content or not cert_content.strip():
            raise HTTPException(status_code=500, detail="Server CA certificate is empty")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error reading server certificate at {cert_path}: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to read server certificate")
    
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


class MutationGuardEngage(BaseModel):
    reason: str
    owner: str = ""
    expires_in_minutes: Optional[int] = None


class MutationGuardRelease(BaseModel):
    confirm: str


@router.get("/mutation-guard")
async def get_mutation_guard(current_user: Admin = Depends(get_current_user)):
    """Report whether automatic fleet mutations are suspended (admin only)"""
    from app.mutation_guard import mutation_guard
    return mutation_guard.state()


@router.post("/mutation-guard")
async def engage_mutation_guard(
    payload: MutationGuardEngage,
    current_user: Admin = Depends(get_current_user),
):
    """Suspend automatic tunnel/token/reset/SSL mutations (admin only)"""
    from app.mutation_guard import GuardError, mutation_guard

    try:
        state = mutation_guard.engage(
            reason=payload.reason,
            owner=payload.owner or current_user.username,
            expires_in_minutes=payload.expires_in_minutes,
        )
    except GuardError as error:
        raise HTTPException(status_code=400, detail=str(error))
    logger.warning(f"Mutation guard engaged via API by {current_user.username}")
    return state


@router.post("/mutation-guard/release")
async def release_mutation_guard(
    payload: MutationGuardRelease,
    current_user: Admin = Depends(get_current_user),
):
    """Resume automatic mutations after an explicit acknowledgement (admin only)"""
    from app.mutation_guard import GuardError, mutation_guard

    if payload.confirm != "release":
        raise HTTPException(status_code=400, detail='Set "confirm" to "release" to resume automatic mutations')
    try:
        state = mutation_guard.release(released_by=current_user.username)
    except GuardError as error:
        raise HTTPException(status_code=409, detail=str(error))
    logger.warning(f"Mutation guard released via API by {current_user.username}")
    return state


@router.get("/join-token")
async def get_join_token(current_user: Admin = Depends(get_current_user)):
    """Get the registration token for node auto-enrollment (admin only)"""
    import hashlib
    token = hashlib.sha256(f"smite_node_reg:{settings.secret_key}".encode()).hexdigest()[:32]
    return {"token": token}


@router.get("/join-command")
async def get_join_command(role: str = "foreign", current_user: Admin = Depends(get_current_user)):
    """Get the one-click node install/join command (admin only)"""
    import hashlib
    token = hashlib.sha256(f"smite_node_reg:{settings.secret_key}".encode()).hexdigest()[:32]
    return {
        "token": token,
        "role": role,
        "ca_endpoint": "/api/panel/ca/server" if role == "foreign" else "/api/panel/ca"
    }


