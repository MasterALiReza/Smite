"""Authentication endpoints and dependencies"""
import logging
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import delete, select
from pydantic import BaseModel
from passlib.context import CryptContext
from jose import JWTError, jwt

from app.database import AsyncSessionLocal, get_db
from app.models import Admin, RevokedToken
from app.config import settings

import hashlib
import bcrypt

logger = logging.getLogger(__name__)

router = APIRouter()
security = HTTPBearer()
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = getattr(settings, "access_token_expire_minutes", 1440)


def _secret_key() -> str:
    """Resolved on every call so a key loaded at startup is honored."""
    return settings.secret_key


# Token revocation (SHA-256 hash -> expiration timestamp). The panel runs a
# single uvicorn process, so after hydration the in-memory map is complete for
# this process; SQLite keeps revocations effective across restarts.
_revoked_token_hashes: dict[str, float] = {}
_session_factory = AsyncSessionLocal


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _prune_revoked_tokens() -> None:
    """Remove expired tokens from the revocation blocklist."""
    now = time.time()
    expired = [h for h, exp in _revoked_token_hashes.items() if exp < now]
    for h in expired:
        _revoked_token_hashes.pop(h, None)


async def _persist_revocation(token_digest: str, expiry: float) -> None:
    if _session_factory is None:
        return
    try:
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert

        async with _session_factory() as session:
            await session.execute(
                delete(RevokedToken).where(RevokedToken.expires_at < _utcnow_naive())
            )
            await session.execute(
                sqlite_insert(RevokedToken)
                .values(
                    token_hash=token_digest,
                    expires_at=datetime.fromtimestamp(expiry, timezone.utc).replace(tzinfo=None),
                    revoked_at=_utcnow_naive(),
                )
                .on_conflict_do_nothing(index_elements=["token_hash"])
            )
            await session.commit()
    except Exception as exc:
        logger.warning("Could not persist token revocation in SQLite: %s", exc)


async def revoke_token(token: str, exp: Optional[float] = None) -> None:
    """Add a JWT to the revocation blocklist until its expiration."""
    _prune_revoked_tokens()
    token_digest = _token_hash(token)
    expiry = exp if exp is not None else (time.time() + (ACCESS_TOKEN_EXPIRE_MINUTES * 60))
    _revoked_token_hashes[token_digest] = expiry
    await _persist_revocation(token_digest, expiry)


async def is_token_revoked(token: str) -> bool:
    """Check if token is in the revocation blocklist."""
    if len(_revoked_token_hashes) > 512:
        _prune_revoked_tokens()
    token_digest = _token_hash(token)
    exp = _revoked_token_hashes.get(token_digest)
    if exp is None:
        return False
    if exp < time.time():
        _revoked_token_hashes.pop(token_digest, None)
        return False
    return True


async def hydrate_revoked_tokens() -> int:
    """Load unexpired revocations after a restart and drop expired rows."""
    if _session_factory is None:
        return 0
    try:
        async with _session_factory() as session:
            await session.execute(
                delete(RevokedToken).where(RevokedToken.expires_at < _utcnow_naive())
            )
            result = await session.execute(
                select(RevokedToken.token_hash, RevokedToken.expires_at)
            )
            now = time.time()
            loaded = 0
            for token_digest, expires_at in result.all():
                if expires_at is None:
                    continue
                expires_ts = (
                    expires_at.replace(tzinfo=timezone.utc).timestamp()
                    if expires_at.tzinfo is None
                    else expires_at.timestamp()
                )
                if expires_ts > now:
                    _revoked_token_hashes[token_digest] = expires_ts
                    loaded += 1
            await session.commit()
        if loaded:
            logger.info("Loaded %s unexpired revoked token(s) from the database", loaded)
        return loaded
    except Exception as exc:
        logger.warning("Could not hydrate revoked tokens from SQLite: %s", exc)
        return 0

# Simple in-memory rate limiter for login attempts (per client IP)
LOGIN_MAX_ATTEMPTS = 10
LOGIN_WINDOW_SECONDS = 300  # 5 minutes
LOGIN_LOCKOUT_SECONDS = 900  # 15 minutes
_login_attempts: dict[str, deque] = defaultdict(deque)
_login_locked_until: dict[str, float] = {}


def _prune_rate_limit_state() -> None:
    """Bound memory usage of the login rate limiter (per-IP state)."""
    if len(_login_attempts) > 1000:
        now = time.monotonic()
        stale = [ip for ip, attempts in _login_attempts.items()
                 if not attempts or now - attempts[-1] > LOGIN_WINDOW_SECONDS]
        for ip in stale:
            _login_attempts.pop(ip, None)


_TRUSTED_PROXIES = {"127.0.0.1", "::1", "localhost"}


def _client_ip(request: Request) -> str:
    direct_ip = request.client.host if request.client else "unknown"
    if direct_ip in _TRUSTED_PROXIES:
        # nginx sets X-Real-IP to the real peer address; the left-most
        # X-Forwarded-For entry is client-controlled and can be spoofed to
        # bypass the login rate limiter.
        real_ip = request.headers.get("X-Real-IP")
        if real_ip and real_ip.strip():
            return real_ip.strip()
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            client_candidate = forwarded.split(",")[0].strip()
            if client_candidate:
                return client_candidate
    return direct_ip


def _check_login_rate_limit(client_ip: str) -> None:
    _prune_rate_limit_state()
    now = time.monotonic()
    locked_until = _login_locked_until.get(client_ip, 0.0)
    if now < locked_until:
        remaining = int(locked_until - now)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Too many failed login attempts. Try again in {remaining} seconds.",
        )

    attempts = _login_attempts[client_ip]
    while attempts and now - attempts[0] > LOGIN_WINDOW_SECONDS:
        attempts.popleft()

    if len(attempts) >= LOGIN_MAX_ATTEMPTS:
        _login_locked_until[client_ip] = now + LOGIN_LOCKOUT_SECONDS
        attempts.clear()
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Too many failed login attempts. Try again in {LOGIN_LOCKOUT_SECONDS} seconds.",
        )


def _record_failed_login(client_ip: str) -> None:
    _login_attempts[client_ip].append(time.monotonic())


def _clear_failed_logins(client_ip: str) -> None:
    _login_attempts.pop(client_ip, None)
    _login_locked_until.pop(client_ip, None)


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    username: str


class TokenData(BaseModel):
    username: Optional[str] = None


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against a hash using native bcrypt with fallback"""
    try:
        pw_bytes = plain_password.encode("utf-8")[:72]
        hash_bytes = hashed_password.encode("utf-8")
        return bcrypt.checkpw(pw_bytes, hash_bytes)
    except Exception:
        try:
            return pwd_context.verify(plain_password, hashed_password)
        except Exception:
            return False


def get_password_hash(password: str) -> str:
    """Hash a password using native bcrypt"""
    pw_bytes = password.encode("utf-8")[:72]
    return bcrypt.hashpw(pw_bytes, bcrypt.gensalt()).decode("utf-8")


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    """Create a JWT token"""
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, _secret_key(), algorithm=ALGORITHM)
    return encoded_jwt


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: AsyncSession = Depends(get_db)
) -> Admin:
    """Get the current authenticated user"""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    
    try:
        token = credentials.credentials
        if await is_token_revoked(token):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token has been revoked",
                headers={"WWW-Authenticate": "Bearer"},
            )
        payload = jwt.decode(token, _secret_key(), algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise credentials_exception
        token_data = TokenData(username=username)
    except JWTError:
        raise credentials_exception
    
    result = await db.execute(select(Admin).where(Admin.username == token_data.username))
    user = result.scalar_one_or_none()
    if user is None:
        raise credentials_exception
    return user


_optional_bearer = HTTPBearer(auto_error=False)


async def get_current_user_optional(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_optional_bearer),
    db: AsyncSession = Depends(get_db)
) -> Optional[Admin]:
    """Like get_current_user but returns None instead of raising when
    unauthenticated. Used by node-facing endpoints that must keep working
    for older deployments without tokens."""
    if credentials is None:
        return None
    try:
        token = credentials.credentials
        if await is_token_revoked(token):
            return None
        payload = jwt.decode(token, _secret_key(), algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            return None
    except JWTError:
        return None
    
    result = await db.execute(select(Admin).where(Admin.username == username))
    user = result.scalar_one_or_none()
    return user


# Pre-computed bcrypt hash for constant-time dummy verification on unknown usernames
_DUMMY_BCRYPT_HASH = "$2b$12$bDSQ5nf6E2iMUMWkjRapHOb3QsH9LdEWXY4QH6vqlEbXjz4eoMZGa"


@router.post("/login", response_model=LoginResponse)
async def login(login_data: LoginRequest, request: Request, db: AsyncSession = Depends(get_db)):
    """Login endpoint (rate-limited per client IP with constant-time verification)"""
    client_ip = _client_ip(request)
    _check_login_rate_limit(client_ip)

    result = await db.execute(select(Admin).where(Admin.username == login_data.username))
    user = result.scalar_one_or_none()
    
    if not user:
        # Constant-time dummy check to prevent timing-based username enumeration
        verify_password(login_data.password, _DUMMY_BCRYPT_HASH)
        _record_failed_login(client_ip)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    if not verify_password(login_data.password, user.password_hash):
        _record_failed_login(client_ip)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    _clear_failed_logins(client_ip)
    
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": user.username}, expires_delta=access_token_expires
    )
    
    return LoginResponse(
        access_token=access_token,
        token_type="bearer",
        username=user.username
    )


@router.get("/me")
async def get_current_user_info(current_user: Admin = Depends(get_current_user)):
    """Get current user information"""
    return {
        "username": current_user.username,
        "id": current_user.id,
        "created_at": current_user.created_at.isoformat() if current_user.created_at else None
    }


@router.post("/logout")
async def logout(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_optional_bearer)
):
    """Logout endpoint with server-side token revocation"""
    if credentials and credentials.credentials:
        try:
            token = credentials.credentials
            payload = jwt.decode(token, _secret_key(), algorithms=[ALGORITHM], options={"verify_exp": False})
            exp = float(payload.get("exp", time.time() + (ACCESS_TOKEN_EXPIRE_MINUTES * 60)))
            await revoke_token(token, exp)
        except Exception:
            await revoke_token(credentials.credentials)
    return {"message": "Logged out successfully"}
