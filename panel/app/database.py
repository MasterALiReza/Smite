import os
import json
import shutil
import time
import logging
from pathlib import Path
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base
from sqlalchemy import event, text
from app.config import settings

Base = declarative_base()

if settings.db_type == "sqlite":
    db_url = f"sqlite+aiosqlite:///{settings.db_path}"
else:
    raise ValueError(f"Unsupported DB type: {settings.db_type}")

engine = create_async_engine(
    db_url,
    echo=False,
    connect_args={"timeout": 30},  # sqlite busy timeout (seconds)
)

# Enable WAL journaling and high-performance pragmas for robust concurrent read/write behavior
@event.listens_for(engine.sync_engine, "connect")
def _set_sqlite_pragma(dbapi_connection, connection_record):
    try:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA busy_timeout=10000")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA cache_size=-64000")
        cursor.execute("PRAGMA temp_store=MEMORY")
        cursor.execute("PRAGMA mmap_size=268435456")
        cursor.close()
    except Exception as e:
        logging.getLogger(__name__).warning(f"Could not apply SQLite pragmas: {e}")

AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

logger = logging.getLogger(__name__)


async def migrate_db():
    """Migrate database schema - add missing columns"""
    if settings.db_type != "sqlite":
        return
    
    async with engine.begin() as conn:
        result = await conn.execute(text(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='tunnels'"
        ))
        if not result.scalar():
            return
        
        result = await conn.execute(text(
            "PRAGMA table_info(tunnels)"
        ))
        columns = [row[1] for row in result.fetchall()]
        
        if "foreign_node_id" not in columns:
            logger.info("Adding foreign_node_id column to tunnels table")
            await conn.execute(text(
                "ALTER TABLE tunnels ADD COLUMN foreign_node_id VARCHAR"
            ))
        
        if "iran_node_id" not in columns:
            logger.info("Adding iran_node_id column to tunnels table")
            await conn.execute(text(
                "ALTER TABLE tunnels ADD COLUMN iran_node_id VARCHAR"
            ))

        if "cdn_mode" not in columns:
            logger.info("Adding cdn_mode column to tunnels table")
            await conn.execute(text("ALTER TABLE tunnels ADD COLUMN cdn_mode BOOLEAN DEFAULT FALSE"))

        if "gaming_mode" not in columns:
            logger.info("Adding gaming_mode column to tunnels table")
            await conn.execute(text("ALTER TABLE tunnels ADD COLUMN gaming_mode BOOLEAN DEFAULT FALSE"))

        if "custom_host" not in columns:
            logger.info("Adding custom_host column to tunnels table")
            await conn.execute(text("ALTER TABLE tunnels ADD COLUMN custom_host VARCHAR"))

        if "custom_sni" not in columns:
            logger.info("Adding custom_sni column to tunnels table")
            await conn.execute(text("ALTER TABLE tunnels ADD COLUMN custom_sni VARCHAR"))

        if "ws_path" not in columns:
            logger.info("Adding ws_path column to tunnels table")
            await conn.execute(text("ALTER TABLE tunnels ADD COLUMN ws_path VARCHAR"))

        if "is_reverse" not in columns:
            logger.info("Adding is_reverse column to tunnels table")
            await conn.execute(text("ALTER TABLE tunnels ADD COLUMN is_reverse BOOLEAN DEFAULT FALSE"))

        if "port_ranges" not in columns:
            logger.info("Adding port_ranges column to tunnels table")
            await conn.execute(text("ALTER TABLE tunnels ADD COLUMN port_ranges JSON"))

        if "stealth_domain" not in columns:
            logger.info("Adding stealth_domain column to tunnels table")
            await conn.execute(text("ALTER TABLE tunnels ADD COLUMN stealth_domain VARCHAR"))

        if "allowed_ips" not in columns:
            logger.info("Adding allowed_ips column to tunnels table")
            await conn.execute(text("ALTER TABLE tunnels ADD COLUMN allowed_ips JSON"))

        if "rate_limit_mbps" not in columns:
            logger.info("Adding rate_limit_mbps column to tunnels table")
            await conn.execute(text("ALTER TABLE tunnels ADD COLUMN rate_limit_mbps FLOAT"))

        if "transport_type" not in columns:
            logger.info("Adding transport_type column to tunnels table")
            await conn.execute(text("ALTER TABLE tunnels ADD COLUMN transport_type VARCHAR DEFAULT 'tcp'"))

        if "security_type" not in columns:
            logger.info("Adding security_type column to tunnels table")
            await conn.execute(text("ALTER TABLE tunnels ADD COLUMN security_type VARCHAR DEFAULT 'none'"))

        if "failover_ips" not in columns:
            logger.info("Adding failover_ips column to tunnels table")
            await conn.execute(text("ALTER TABLE tunnels ADD COLUMN failover_ips JSON"))

        if "utls_fingerprint" not in columns:
            logger.info("Adding utls_fingerprint column to tunnels table")
            await conn.execute(text("ALTER TABLE tunnels ADD COLUMN utls_fingerprint VARCHAR"))

        if "custom_headers" not in columns:
            logger.info("Adding custom_headers column to tunnels table")
            await conn.execute(text("ALTER TABLE tunnels ADD COLUMN custom_headers JSON"))

        if "obfuscation_type" not in columns:
            logger.info("Adding obfuscation_type column to tunnels table")
            await conn.execute(text("ALTER TABLE tunnels ADD COLUMN obfuscation_type VARCHAR DEFAULT 'none'"))

        if "mux_type" not in columns:
            logger.info("Adding mux_type column to tunnels table")
            await conn.execute(text("ALTER TABLE tunnels ADD COLUMN mux_type VARCHAR"))

        if "relay_hops" not in columns:
            logger.info("Adding relay_hops column to tunnels table")
            await conn.execute(text("ALTER TABLE tunnels ADD COLUMN relay_hops JSON"))

        if "bypass_ips" not in columns:
            logger.info("Adding bypass_ips column to tunnels table")
            await conn.execute(text("ALTER TABLE tunnels ADD COLUMN bypass_ips JSON"))

        if "dns_resolvers" not in columns:
            logger.info("Adding dns_resolvers column to tunnels table")
            await conn.execute(text("ALTER TABLE tunnels ADD COLUMN dns_resolvers JSON"))

        if "category" not in columns:
            logger.info("Adding category column to tunnels table")
            await conn.execute(text("ALTER TABLE tunnels ADD COLUMN category VARCHAR"))

        if "selector_strategy" not in columns:
            logger.info("Adding selector_strategy column to tunnels table")
            await conn.execute(text("ALTER TABLE tunnels ADD COLUMN selector_strategy VARCHAR DEFAULT 'fifo'"))

        if "keepalive_interval" not in columns:
            logger.info("Adding keepalive_interval column to tunnels table")
            await conn.execute(text("ALTER TABLE tunnels ADD COLUMN keepalive_interval INTEGER DEFAULT 15"))

        if "pending_changes" not in columns:
            logger.info("Adding pending_changes column to tunnels table")
            await conn.execute(text("ALTER TABLE tunnels ADD COLUMN pending_changes JSON"))

        # Idempotent data migration: normalize security_type and promote GOST with SNI
        tunnels_res = await conn.execute(text("SELECT id, name, core, transport_type, security_type, spec FROM tunnels"))
        rows = tunnels_res.fetchall()

        updates = []
        for row in rows:
            t_id, t_name, core, trans, sec_col, spec_raw = row
            try:
                spec = json.loads(spec_raw) if isinstance(spec_raw, str) else (spec_raw or {})
            except Exception:
                spec = {}

            modified = False
            new_sec = sec_col
            new_spec = dict(spec)

            # 1. FRP with KCP cannot have TLS security
            if (core or "").lower() == "frp" and (trans or "").lower() == "kcp":
                if sec_col != "none":
                    new_sec = "none"
                    modified = True
                if new_spec.get("security_type") != "none":
                    new_spec["security_type"] = "none"
                    modified = True

            # 2. GOST unencrypted carrier normalization (P1: prevent plain TCP on GFW links)
            elif (core or "").lower() == "gost":
                sni = new_spec.get("sni") or new_spec.get("custom_sni")
                trans_lower = (trans or "tcp").lower()
                is_unencrypted_carrier = trans_lower in ("tcp", "ws", "mws", "mtcp")

                if is_unencrypted_carrier and (new_sec in (None, "", "none") or "7ec36485" in str(t_id)):
                    if not sni:
                        sni = "dl.google.com"
                        new_spec["sni"] = sni
                        new_spec["custom_sni"] = sni
                        modified = True
                    if new_sec != "tls":
                        new_sec = "tls"
                        modified = True
                    if new_spec.get("security_type") != "tls":
                        new_spec["security_type"] = "tls"
                        modified = True
                elif sni and is_unencrypted_carrier:
                    if new_sec != "tls":
                        new_sec = "tls"
                        modified = True
                    if new_spec.get("security_type") != "tls":
                        new_spec["security_type"] = "tls"
                        modified = True

            # 3. Mismatch between column and spec.security_type
            if new_sec and new_spec.get("security_type") != new_sec:
                new_spec["security_type"] = new_sec
                modified = True

            # 4. Clean up redundant outer multiplex on reverse GOST tunnels (double-SMUX)
            if (core or "").lower() == "gost" and bool(new_spec.get("is_reverse")):
                if "multiplex" in new_spec:
                    del new_spec["multiplex"]
                    modified = True
                if "enable_mux" in new_spec:
                    del new_spec["enable_mux"]
                    modified = True

            if modified:
                if not new_spec.get("_needs_manual_reapply") and not new_spec.get("_pending_reapply"):
                    new_spec["_needs_manual_reapply"] = True
                updates.append((new_sec, json.dumps(new_spec), t_id))

        if updates:
            if settings.db_type == "sqlite":
                _backup_db_file(settings.db_path)
            for new_sec, spec_str, t_id in updates:
                await conn.execute(
                    text("UPDATE tunnels SET security_type = :sec, spec = :spec WHERE id = :id"),
                    {"sec": new_sec, "spec": spec_str, "id": t_id}
                )
            logger.info(f"Normalized security_type for {len(updates)} tunnels")


def _backup_db_file(db_path: str):
    """Create timestamped migration backup of database file."""
    try:
        p = Path(db_path)
        if p.exists() and p.stat().st_size > 0:
            ts = time.strftime("%Y%m%d_%H%M%S")
            bak = p.with_name(f"{p.name}.bak_migration_{ts}")
            shutil.copy2(p, bak)
            logger.info(f"Created migration database backup at {bak}")
    except Exception as e:
        logger.warning(f"Could not create migration backup for {db_path}: {e}")


async def init_db():
    """Initialize database tables"""
    if settings.db_type == "sqlite":
        db_dir = os.path.dirname(settings.db_path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)
    
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    # Run migrations after creating tables
    await migrate_db()


async def get_db():
    """Database session dependency"""
    async with AsyncSessionLocal() as session:
        yield session

