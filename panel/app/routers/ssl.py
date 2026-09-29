"""
SSL & Domain Management API Router
Endpoints:
  GET    /api/ssl            - Get active SSL status, cert details, server public IP
  POST   /api/ssl/precheck   - Pre-flight DNS & IP match test
  POST   /api/ssl/issue      - Issue automated Let's Encrypt certificate
  POST   /api/ssl/custom     - Install custom certificate (Cloudflare Origin CA / own PEM)
  POST   /api/ssl/renew      - Force manual renewal
  PUT    /api/ssl/auto-renew - Toggle background auto-renewal
  DELETE /api/ssl            - Disable SSL and revert safely to HTTP
"""

import os
import re
import logging
from typing import Optional, List
from pathlib import Path
from pydantic import BaseModel, Field
from fastapi import APIRouter, Depends, HTTPException, status, Response
from fastapi.responses import PlainTextResponse

from app.models import Admin
from app.routers.auth import get_current_user
from app.ssl_manager import (
    get_server_public_ip,
    check_domain_dns,
    get_active_certificate_info,
    get_ssl_db_settings,
    save_ssl_db_settings,
    issue_letsencrypt_cert,
    install_custom_ssl,
    renew_letsencrypt_cert,
    remove_ssl_cert,
    sanitize_domain,
    validate_domain_name,
    validate_email_address,
    get_cert_paths,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ssl", tags=["ssl"])


# Request & Response Schemas
class DomainPrecheckRequest(BaseModel):
    domain: str = Field(..., description="Candidate domain name to verify")


class IssueSslRequest(BaseModel):
    domain: str = Field(..., description="Domain name for Let's Encrypt")
    email: Optional[str] = Field(None, description="Admin email for Let's Encrypt notifications")
    auto_renew: bool = Field(True, description="Enable automatic 12-hour renewal worker")


class CustomSslRequest(BaseModel):
    domain: str = Field(..., description="Domain name corresponding to the certificate")
    cert_pem: str = Field(..., description="Fullchain certificate in PEM format")
    key_pem: str = Field(..., description="Private key in PEM format")
    auto_renew: bool = Field(False, description="Auto-renew should remain false for custom certs")


class AutoRenewUpdateRequest(BaseModel):
    enabled: bool = Field(..., description="Enable or disable auto-renewal")


@router.get("")
async def get_ssl_status(current_user: Admin = Depends(get_current_user)):
    """
    Get current SSL configuration, detected server public IP, and certificate details.
    """
    try:
        db_cfg = await get_ssl_db_settings()
        cert_info = get_active_certificate_info()
        server_ip = await get_server_public_ip()
        
        return {
            "status": "success",
            "enabled": db_cfg.get("enabled", False),
            "domain": db_cfg.get("domain", ""),
            "email": db_cfg.get("email", ""),
            "mode": db_cfg.get("mode", "letsencrypt"),
            "auto_renew": db_cfg.get("auto_renew", True),
            "server_ip": server_ip,
            "cert_info": cert_info
        }
    except Exception as e:
        logger.error(f"Error fetching SSL status: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch SSL status: {str(e)}"
        )


@router.post("/precheck")
async def precheck_domain(
    payload: DomainPrecheckRequest,
    current_user: Admin = Depends(get_current_user)
):
    """
    Perform pre-flight DNS and public IP verification before requesting certificates.
    Prevents Let's Encrypt rate limits and warns if Cloudflare proxy is detected.
    """
    try:
        result = await check_domain_dns(payload.domain)
        return result
    except Exception as e:
        logger.error(f"Error in DNS precheck for {payload.domain}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"DNS precheck error: {str(e)}"
        )


@router.post("/issue")
async def issue_ssl(
    payload: IssueSslRequest,
    current_user: Admin = Depends(get_current_user)
):
    """
    Request and install a free Let's Encrypt certificate via Certbot (webroot or standalone).
    """
    try:
        clean_domain = validate_domain_name(payload.domain)
        valid_email = validate_email_address(payload.email)
    except ValueError as ve:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(ve))

    try:
        logger.info(f"Admin {current_user.username} requested Let's Encrypt SSL for {clean_domain}")
        cert_info = await issue_letsencrypt_cert(clean_domain, valid_email)
        
        await save_ssl_db_settings(
            enabled=True,
            domain=clean_domain,
            email=valid_email or "",
            mode="letsencrypt",
            auto_renew=payload.auto_renew
        )
        
        return {
            "status": "success",
            "message": f"SSL certificate successfully issued and installed for {clean_domain}!",
            "cert_info": cert_info
        }
    except ValueError as ve:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(ve))
    except Exception as e:
        logger.error(f"Failed to issue SSL for {clean_domain}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )


@router.post("/custom")
async def install_custom_ssl_endpoint(
    payload: CustomSslRequest,
    current_user: Admin = Depends(get_current_user)
):
    """
    Install a custom certificate and private key (e.g. Cloudflare Origin CA certificate).
    Validates key pair using native cryptography before applying.
    """
    try:
        clean_domain = validate_domain_name(payload.domain)
    except ValueError as ve:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(ve))

    if not payload.cert_pem or not payload.key_pem:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Both Certificate (PEM) and Private Key (PEM) must be provided."
        )

    try:
        logger.info(f"Admin {current_user.username} installing custom SSL for {clean_domain}")
        cert_info = install_custom_ssl(clean_domain, payload.cert_pem, payload.key_pem)
        
        await save_ssl_db_settings(
            enabled=True,
            domain=clean_domain,
            email="",
            mode="custom",
            auto_renew=False
        )
        
        return {
            "status": "success",
            "message": f"Custom SSL certificate installed successfully for {clean_domain}!",
            "cert_info": cert_info
        }
    except ValueError as ve:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(ve))
    except Exception as e:
        logger.error(f"Failed to install custom SSL for {clean_domain}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to install custom SSL: {str(e)}"
        )


@router.post("/renew")
async def manual_renew_ssl(current_user: Admin = Depends(get_current_user)):
    """
    Manually force renewal of the active Let's Encrypt certificate.
    """
    cfg = await get_ssl_db_settings()
    if not cfg.get("enabled"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="SSL is not currently enabled."
        )
    if cfg.get("mode") == "custom":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Custom certificates cannot be auto-renewed via Let's Encrypt. Please paste the updated certificate in Custom SSL."
        )

    try:
        cert_info = await renew_letsencrypt_cert()
        return {
            "status": "success",
            "message": "Certificate renewed successfully!",
            "cert_info": cert_info
        }
    except Exception as e:
        logger.error(f"SSL renewal failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"SSL renewal failed: {str(e)}"
        )


@router.put("/auto-renew")
async def update_auto_renew(
    payload: AutoRenewUpdateRequest,
    current_user: Admin = Depends(get_current_user)
):
    """
    Enable or disable automatic background renewal.
    """
    try:
        cfg = await get_ssl_db_settings()
        await save_ssl_db_settings(
            enabled=cfg.get("enabled", False),
            domain=cfg.get("domain", ""),
            email=cfg.get("email", ""),
            mode=cfg.get("mode", "letsencrypt"),
            auto_renew=payload.enabled
        )
        return {
            "status": "success",
            "auto_renew": payload.enabled,
            "message": f"Auto-renewal {'enabled' if payload.enabled else 'disabled'}."
        }
    except Exception as e:
        logger.error(f"Failed to update auto-renew setting: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )


@router.delete("")
async def delete_ssl(current_user: Admin = Depends(get_current_user)):
    """
    Safely disable SSL, archive certificates, and revert panel to HTTP.
    Prevents lockouts and leaves node connections completely untouched.
    """
    try:
        logger.info(f"Admin {current_user.username} disabling SSL")
        result = await remove_ssl_cert()
        return result
    except Exception as e:
        logger.error(f"Failed to disable SSL: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to disable SSL: {str(e)}"
        )


_ACME_TOKEN_REGEX = re.compile(r"^[A-Za-z0-9_-]{1,128}$")

# Direct ACME challenge handler on FastAPI level
@router.get("/.well-known/acme-challenge/{token}", include_in_schema=False)
async def acme_challenge_fallback(token: str):
    """
    Serve Let's Encrypt HTTP-01 challenge directly with strict RFC token and path traversal protection
    """
    if not _ACME_TOKEN_REGEX.match(token):
        raise HTTPException(status_code=404, detail="Challenge token not found")

    candidate_bases = [
        Path("/var/www/certbot/.well-known/acme-challenge"),
        Path("./certs/.well-known/acme-challenge"),
    ]
    for base in candidate_bases:
        try:
            if not base.exists():
                continue
            resolved_base = base.resolve()
            candidate_file = (base / token).resolve()
            if os.path.commonpath([str(resolved_base), str(candidate_file)]) == str(resolved_base):
                if candidate_file.is_file():
                    return PlainTextResponse(candidate_file.read_text(encoding="utf-8").strip())
        except Exception:
            continue
    raise HTTPException(status_code=404, detail="Challenge token not found")
