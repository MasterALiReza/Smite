"""Status API endpoints"""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
import psutil

from app.database import get_db
from app.models import Tunnel, Node


router = APIRouter()

VERSION = "0.1.0"


@router.get("/version")
async def get_version():
    """Get panel version from git tag, VERSION file, Docker image label, or environment"""
    import os
    import asyncio
    from pathlib import Path
    
    try:
        proc = await asyncio.create_subprocess_exec(
            "git", "describe", "--tags", "--always", "--dirty",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd="/app"
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=2.0)
        if proc.returncode == 0:
            git_version = stdout.decode().strip()
            if git_version and not git_version.startswith("fatal"):
                version = git_version.split("-")[0].lstrip("v")
                if version and version not in ["next", "latest", "main", "master"]:
                    return {"version": version}
    except:
        pass
    
    version_file = Path("/app/VERSION")
    if version_file.exists():
        try:
            version = version_file.read_text().strip()
            if version and version not in ["next", "latest"]:
                return {"version": version.lstrip("v")}
        except:
            pass
    
    smite_version = os.getenv("SMITE_VERSION", "")
    if smite_version in ["next", "latest"]:
        try:
            import json
            cgroup_path = Path("/proc/self/cgroup")
            if cgroup_path.exists():
                with open(cgroup_path) as f:
                    for line in f:
                        if "docker" in line or "containerd" in line:
                            container_id = line.split("/")[-1].strip()
                            proc = await asyncio.create_subprocess_exec(
                                "docker", "inspect", container_id,
                                stdout=asyncio.subprocess.PIPE,
                                stderr=asyncio.subprocess.PIPE
                            )
                            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=2.0)
                            if proc.returncode == 0:
                                data = json.loads(stdout.decode())
                                if data and len(data) > 0:
                                    labels = data[0].get("Config", {}).get("Labels", {})
                                    version = labels.get("smite.version") or labels.get("org.opencontainers.image.version", "")
                                    if version and version not in ["next", "latest"]:
                                        return {"version": version.lstrip("v")}
                            break
        except:
            pass
        
        return {"version": smite_version}
    
    if smite_version:
        version = smite_version.lstrip("v")
    else:
        version = VERSION
    
    return {"version": version}


@router.get("")
async def get_status(db: AsyncSession = Depends(get_db)):
    """Get system status"""
    cpu_percent = psutil.cpu_percent(interval=1)
    memory = psutil.virtual_memory()
    
    tunnel_result = await db.execute(select(func.count(Tunnel.id)))
    total_tunnels = tunnel_result.scalar() or 0
    
    active_tunnels_result = await db.execute(
        select(func.count(Tunnel.id)).where(Tunnel.status == "active")
    )
    active_tunnels = active_tunnels_result.scalar() or 0
    
    node_result = await db.execute(select(func.count(Node.id)))
    total_nodes = node_result.scalar() or 0
    
    active_nodes_result = await db.execute(
        select(func.count(Node.id)).where(Node.status == "active")
    )
    active_nodes = active_nodes_result.scalar() or 0
    
    return {
        "system": {
            "cpu_percent": cpu_percent,
            "memory_percent": memory.percent,
            "memory_total_gb": memory.total / (1024**3),
            "memory_used_gb": memory.used / (1024**3),
        },
        "tunnels": {
            "total": total_tunnels,
            "active": active_tunnels,
        },
        "nodes": {
            "total": total_nodes,
            "active": active_nodes,
        }
    }

