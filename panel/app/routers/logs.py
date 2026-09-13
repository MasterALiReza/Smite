"""Logs API endpoints"""
from collections import deque
from fastapi import APIRouter, Depends
from datetime import datetime
from typing import List, Dict
import logging

from app.models import Admin
from app.routers.auth import get_current_user


router = APIRouter()

log_buffer = deque(maxlen=1000)


class MemoryHandler(logging.Handler):
    """Custom handler that stores logs in memory (bounded ring buffer)"""
    def emit(self, record):
        log_buffer.append({
            "timestamp": datetime.utcnow().isoformat(),
            "level": record.levelname,
            "message": self.format(record)
        })


handler = MemoryHandler()
handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
logging.getLogger().addHandler(handler)
logging.getLogger().setLevel(logging.INFO)


def get_recent_logs(limit: int = 100) -> List[Dict]:
    """In-process helper for reading recent logs (used by the Telegram bot)"""
    logs = list(log_buffer)
    return logs[-limit:]


@router.get("")
async def get_logs(limit: int = 100, current_user: Admin = Depends(get_current_user)):
    """Get logs"""
    return {"logs": get_recent_logs(limit)}
