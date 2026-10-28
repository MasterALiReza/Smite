"""Node server for panel-node communication"""
import asyncio
import ssl
import logging
from pathlib import Path
from typing import Optional
from app.config import settings

logger = logging.getLogger(__name__)


async def registered_node_count() -> int:
    """Registered node count; assumes nodes exist when the database cannot be read."""
    try:
        from sqlalchemy import func, select
        from app.database import AsyncSessionLocal
        from app.models import Node

        async with AsyncSessionLocal() as db:
            result = await db.execute(select(func.count()).select_from(Node))
            return int(result.scalar() or 0)
    except Exception as e:
        logger.warning(f"Could not count registered nodes: {e}")
        return 1


async def trust_material_generation_allowed(missing: bool) -> bool:
    """Never mint new CA material on a live fleet or under the mutation guard.

    Agents pin the CA they were enrolled with. Regenerating it because the certs
    volume was not mounted (a common compose mistake) silently breaks every
    node's HTTPS/H2 trust. A fresh install has zero nodes, so it still works.
    """
    if not missing:
        return False
    from app.mutation_guard import mutation_guard

    if mutation_guard.is_engaged():
        logger.critical("Mutation guard is engaged; refusing to generate CA trust material")
        return False
    node_count = await registered_node_count()
    if node_count > 0:
        logger.critical(
            "Refusing to generate CA trust material while %d node(s) are registered. "
            "If certs/ is missing, restore ca.crt/ca.key (and ca-server.*) from backup; "
            "a new CA would break node trust.",
            node_count,
        )
        return False
    return True


class NodeServer:
    """Node server for secure panel-node communication"""
    
    def __init__(self):
        self.port = settings.node_port
        self.cert_path = settings.node_cert_path
        self.key_path = settings.node_key_path
        self.server: Optional[asyncio.Server] = None
        self.clients = {}
        self.allow_ca_generation = False
    
    async def start(self):
        """Start Node server"""
        cert_path = Path(self.cert_path)
        key_path = Path(self.key_path)
        
        if not cert_path.exists() or not key_path.exists():
            if self.allow_ca_generation:
                await self._generate_certs()
            else:
                logger.critical(
                    "Node server trust material is missing at %s / %s and CA generation is disabled; "
                    "restore the existing certificates instead of minting new ones",
                    cert_path,
                    key_path,
                )
        
        logger.info(f"Node server starting on port {self.port}")
    
    async def stop(self):
        """Stop Node server"""
        if self.server:
            self.server.close()
            await self.server.wait_closed()
    
    async def _generate_certs(self, common_name: str = "Smite CA"):
        """Generate CA certificate and key"""
        from cryptography import x509
        from cryptography.x509.oid import NameOID
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from datetime import datetime, timedelta, timezone
        import os
        
        cert_path = Path(self.cert_path)
        key_path = Path(self.key_path)
        
        if not cert_path.is_absolute():
            base_dir = Path(os.getcwd())
            cert_path = base_dir / cert_path
            key_path = base_dir / key_path
        
        logger.info(f"Generating certificate at: {cert_path}")
        logger.info(f"Generating key at: {key_path}")
        
        cert_path.parent.mkdir(parents=True, exist_ok=True)
        key_path.parent.mkdir(parents=True, exist_ok=True)
        
        if not os.access(cert_path.parent, os.W_OK):
            raise PermissionError(f"Cannot write to {cert_path.parent}")
        
        private_key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=2048,
        )
        
        subject = issuer = x509.Name([
            x509.NameAttribute(NameOID.COUNTRY_NAME, "US"),
            x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME, "CA"),
            x509.NameAttribute(NameOID.LOCALITY_NAME, "SF"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Smite Panel"),
            x509.NameAttribute(NameOID.COMMON_NAME, common_name),
        ])
        
        now_time = datetime.now(timezone.utc)
        cert = x509.CertificateBuilder().subject_name(
            subject
        ).issuer_name(
            issuer
        ).public_key(
            private_key.public_key()
        ).serial_number(
            x509.random_serial_number()
        ).not_valid_before(
            now_time
        ).not_valid_after(
            now_time + timedelta(days=3650)
        ).add_extension(
            x509.BasicConstraints(ca=True, path_length=None),
            critical=True,
        ).sign(private_key, hashes.SHA256())
        
        try:
            cert_bytes = cert.public_bytes(serialization.Encoding.PEM)
            with open(cert_path, "wb") as f:
                f.write(cert_bytes)
            if cert_path.stat().st_size == 0:
                raise IOError(f"Certificate file is empty after write: {cert_path}")
            logger.info(f"Certificate written successfully ({cert_path.stat().st_size} bytes)")
        except Exception as e:
            logger.error(f"Error writing certificate: {e}")
            raise
        
        try:
            key_bytes = private_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption()
            )
            with open(key_path, "wb") as f:
                f.write(key_bytes)
            if key_path.stat().st_size == 0:
                raise IOError(f"Key file is empty after write: {key_path}")
            logger.info(f"Key written successfully ({key_path.stat().st_size} bytes)")
        except Exception as e:
            logger.error(f"Error writing key: {e}")
            raise
        
        self.cert_path = str(cert_path)
        self.key_path = str(key_path)
        
        logger.info(f"Generated CA certificate at {cert_path}")

