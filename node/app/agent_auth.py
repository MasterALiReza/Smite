"""Node agent API authentication.

The agent API (/api/agent/*) can be protected by a shared secret sent by the
panel in the X-Node-Token header. The secret comes from one of two sources:

1. NODE_API_TOKEN in the node .env (static, manually configured). Takes precedence.
2. A per-node token provisioned automatically by the panel through
   POST /api/agent/auth/bootstrap and persisted in the node data volume, so it
   survives container restarts and re-creation.

When neither is configured the API stays open for backward compatibility with
older panels; the panel provisions a token as soon as it sees the node.

After the panel has provisioned and verified a token it asks the agent to set a
durable enforcement flag. From then on the API stays locked even if the token
file disappears (wiped data volume), so it can never silently fall back to an
open API. Local recovery is `smite-node reset-token`, which removes both files.
"""
import hashlib
import hmac
import ipaddress
import json
import logging
import os
import re
import secrets
import socket
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from .config import settings

logger = logging.getLogger(__name__)

TOKEN_FILE = Path(os.environ.get("SMITE_AGENT_TOKEN_FILE", "/var/lib/smite-node/agent_token"))
ENFORCE_FILE = Path(os.environ.get("SMITE_AGENT_AUTH_MODE_FILE", str(TOKEN_FILE.parent / "agent_auth_enforce")))
BOOTSTRAP_FILE = Path(os.environ.get("SMITE_AGENT_BOOTSTRAP_FILE", str(TOKEN_FILE.parent / "agent_bootstrap.json")))
TLS_CERT_FILE = Path(os.environ.get("SMITE_AGENT_TLS_CERT_FILE", str(TOKEN_FILE.parent / "agent_tls.crt")))
TLS_KEY_FILE = Path(os.environ.get("SMITE_AGENT_TLS_KEY_FILE", str(TOKEN_FILE.parent / "agent_tls.key")))
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_\-]{32,128}$")
_BOOTSTRAP_SCHEMA = 1


class BootstrapProofError(RuntimeError):
    pass

_lock = threading.Lock()
_file_token: Optional[str] = None
_loaded = False


def _load_file_token() -> Optional[str]:
    try:
        if TOKEN_FILE.exists():
            value = TOKEN_FILE.read_text(encoding="utf-8").strip()
            if value and _TOKEN_RE.match(value):
                return value
            if value:
                logger.warning(f"Ignoring malformed agent token file {TOKEN_FILE}")
    except Exception as e:
        logger.warning(f"Could not read agent token file {TOKEN_FILE}: {e}")
    return None


def get_file_token() -> Optional[str]:
    global _file_token, _loaded
    with _lock:
        if not _loaded:
            _file_token = _load_file_token()
            _loaded = True
        return _file_token


def get_env_token() -> str:
    return (settings.node_api_token or "").strip()


def get_effective_token() -> str:
    """Token the agent currently enforces ('' when the API is open)."""
    return get_env_token() or (get_file_token() or "")


def token_source() -> str:
    if get_env_token():
        return "env"
    if get_file_token():
        return "panel"
    return "none"


def is_valid_token_format(value: str) -> bool:
    return bool(value) and bool(_TOKEN_RE.match(value))


def _atomic_write(path: Path, value: str, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(value)
            f.flush()
            os.fsync(f.fileno())
    except Exception:
        try:
            tmp.unlink()
        except Exception:
            pass
        raise
    os.replace(tmp, path)
    try:
        os.chmod(path, mode)
    except OSError:
        pass


def _atomic_write_bytes(path: Path, value: bytes, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(value)
            f.flush()
            os.fsync(f.fileno())
    except Exception:
        try:
            tmp.unlink()
        except Exception:
            pass
        raise
    os.replace(tmp, path)
    try:
        os.chmod(path, mode)
    except OSError:
        pass


def _read_bootstrap_record() -> Optional[Dict[str, Any]]:
    try:
        record = json.loads(BOOTSTRAP_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError, TypeError) as error:
        raise BootstrapProofError(f"Bootstrap proof file is unreadable: {error}") from error
    if not isinstance(record, dict) or record.get("schema") != _BOOTSTRAP_SCHEMA:
        raise BootstrapProofError("Bootstrap proof file has an unsupported format")
    proof = record.get("proof")
    expires_at = record.get("expires_at")
    if not isinstance(proof, str) or not _TOKEN_RE.fullmatch(proof):
        raise BootstrapProofError("Bootstrap proof file contains an invalid proof")
    if not isinstance(expires_at, int):
        raise BootstrapProofError("Bootstrap proof file contains an invalid expiry")
    return record


def get_or_create_bootstrap_proof() -> Optional[Dict[str, Any]]:
    """Return a short-lived, local one-time proof while the node is unpaired."""
    if get_effective_token():
        return None
    ttl = max(60, min(int(settings.node_bootstrap_proof_ttl_seconds), 3600))
    now = int(time.time())
    with _lock:
        try:
            record = _read_bootstrap_record()
        except BootstrapProofError as error:
            logger.warning(f"Discarding unusable bootstrap proof file: {error}")
            try:
                BOOTSTRAP_FILE.unlink()
            except OSError:
                pass
            record = None
        if record and record["expires_at"] > now + 15:
            return dict(record)
        proof = secrets.token_urlsafe(32)
        record = {
            "schema": _BOOTSTRAP_SCHEMA,
            "proof": proof,
            "created_at": now,
            "expires_at": now + ttl,
        }
        _atomic_write(BOOTSTRAP_FILE, json.dumps(record, separators=(",", ":")))
        return dict(record)


def provision_file_token(value: str, proof: str) -> None:
    """Persist the first panel token only after consuming an unexpired local proof."""
    global _file_token, _loaded
    if not is_valid_token_format(value):
        raise ValueError("Invalid token format")
    with _lock:
        expected_static = (settings.node_bootstrap_secret or "").strip()
        static_matches = bool(expected_static) and tokens_match(proof, expected_static)
        if not static_matches:
            record = _read_bootstrap_record()
            if not record:
                raise BootstrapProofError("No bootstrap proof is prepared")
            if record["expires_at"] <= int(time.time()):
                try:
                    BOOTSTRAP_FILE.unlink()
                except OSError:
                    pass
                raise BootstrapProofError("Bootstrap proof has expired")
            if not tokens_match(proof, record["proof"]):
                raise BootstrapProofError("Invalid bootstrap proof")
        _atomic_write(TOKEN_FILE, value)
        _file_token = value
        _loaded = True
        try:
            BOOTSTRAP_FILE.unlink()
        except FileNotFoundError:
            pass


def _management_ip_addresses() -> list[str]:
    values = {"127.0.0.1", "::1"}
    configured = (settings.node_ip or "").strip()
    if configured:
        values.add(configured)
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            values.add(sock.getsockname()[0])
    except OSError:
        pass
    try:
        for result in socket.getaddrinfo(socket.gethostname(), None):
            values.add(result[4][0])
    except OSError:
        pass
    valid = []
    for value in values:
        try:
            valid.append(str(ipaddress.ip_address(value)))
        except ValueError:
            continue
    return sorted(set(valid))


def ensure_management_tls_identity() -> None:
    """Create the node's stable self-signed management identity exactly once."""
    cert_exists = TLS_CERT_FILE.exists()
    key_exists = TLS_KEY_FILE.exists()
    if cert_exists != key_exists:
        raise RuntimeError("Node management TLS identity is incomplete; restore the missing cert or key")

    if cert_exists:
        try:
            try:
                from cryptography import x509
                from cryptography.hazmat.primitives import serialization

                certificate = x509.load_pem_x509_certificate(TLS_CERT_FILE.read_bytes())
                private_key = serialization.load_pem_private_key(TLS_KEY_FILE.read_bytes(), password=None)
                cert_public = certificate.public_key().public_bytes(
                    serialization.Encoding.DER,
                    serialization.PublicFormat.SubjectPublicKeyInfo,
                )
                key_public = private_key.public_key().public_bytes(
                    serialization.Encoding.DER,
                    serialization.PublicFormat.SubjectPublicKeyInfo,
                )
                if cert_public != key_public:
                    raise ValueError("certificate and private key do not match")
                return
            except ImportError:
                import ssl

                ctx = ssl.create_default_context()
                ctx.load_cert_chain(str(TLS_CERT_FILE), str(TLS_KEY_FILE))
                return
        except Exception as error:
            raise RuntimeError("Node management TLS identity is corrupt; restore it instead of rotating the pin") from error

    # 1. Try python cryptography library
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        now = datetime.now(timezone.utc)
        common_name = re.sub(r"[^A-Za-z0-9_.-]", "-", settings.node_name or "smite-node")[:64]
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
        san_entries = [x509.DNSName("localhost"), x509.DNSName(socket.gethostname())]
        san_entries.extend(x509.IPAddress(ipaddress.ip_address(value)) for value in _management_ip_addresses())
        certificate = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(private_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=5))
            .not_valid_after(now + timedelta(days=3650))
            .add_extension(x509.SubjectAlternativeName(san_entries), critical=False)
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .sign(private_key, hashes.SHA256())
        )
        key_bytes = private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        cert_bytes = certificate.public_bytes(serialization.Encoding.PEM)
        _atomic_write_bytes(TLS_KEY_FILE, key_bytes, 0o600)
        try:
            _atomic_write_bytes(TLS_CERT_FILE, cert_bytes, 0o644)
        except Exception:
            try:
                TLS_KEY_FILE.unlink()
            except OSError:
                pass
            raise
        return
    except ImportError:
        logger.debug("Cryptography library not installed; trying system openssl CLI fallback")
    except Exception as e:
        logger.warning(f"Failed to generate TLS identity with cryptography: {e}; trying openssl CLI")

    # 2. Try system openssl CLI fallback
    import shutil
    import subprocess
    import tempfile

    openssl_bin = shutil.which("openssl")
    if openssl_bin:
        try:
            common_name = re.sub(r"[^A-Za-z0-9_.-]", "-", settings.node_name or "smite-node")[:64]
            san_items = ["DNS:localhost", f"DNS:{socket.gethostname()}"]
            for ip in _management_ip_addresses():
                san_items.append(f"IP:{ip}")
            san_str = ",".join(san_items)

            with tempfile.TemporaryDirectory() as tmp_dir:
                tmp_key = Path(tmp_dir) / "key.pem"
                tmp_cert = Path(tmp_dir) / "cert.pem"

                # Try with -addext (OpenSSL 1.1.1+)
                cmd = [
                    openssl_bin, "req", "-x509", "-newkey", "rsa:2048", "-days", "3650",
                    "-nodes", "-subj", f"/CN={common_name}",
                    "-addext", f"subjectAltName={san_str}",
                    "-keyout", str(tmp_key), "-out", str(tmp_cert)
                ]
                res = subprocess.run(cmd, capture_output=True, timeout=10)
                if res.returncode != 0:
                    # Fallback without -addext for older openssl versions
                    cmd = [
                        openssl_bin, "req", "-x509", "-newkey", "rsa:2048", "-days", "3650",
                        "-nodes", "-subj", f"/CN={common_name}",
                        "-keyout", str(tmp_key), "-out", str(tmp_cert)
                    ]
                    res = subprocess.run(cmd, capture_output=True, timeout=10)

                if res.returncode == 0 and tmp_key.exists() and tmp_cert.exists():
                    key_bytes = tmp_key.read_bytes()
                    cert_bytes = tmp_cert.read_bytes()
                    _atomic_write_bytes(TLS_KEY_FILE, key_bytes, 0o600)
                    try:
                        _atomic_write_bytes(TLS_CERT_FILE, cert_bytes, 0o644)
                    except Exception:
                        try:
                            TLS_KEY_FILE.unlink()
                        except OSError:
                            pass
                        raise
                    logger.info("Generated management TLS identity using system OpenSSL CLI")
                    return
        except Exception as e:
            logger.warning(f"Failed to generate TLS identity with openssl CLI: {e}")

    logger.warning(
        "Neither cryptography library nor openssl CLI is available; "
        "node agent will run in HTTP mode without management TLS identity."
    )



def management_tls_metadata() -> Optional[Dict[str, str]]:
    if not TLS_CERT_FILE.exists() or not TLS_KEY_FILE.exists():
        return None
    try:
        pem = TLS_CERT_FILE.read_text(encoding="ascii")
        der = ssl_pem_to_der(pem)
    except (OSError, ValueError):
        return None
    return {
        "certificate": pem,
        "fingerprint": hashlib.sha256(der).hexdigest(),
    }


def ssl_pem_to_der(pem: str) -> bytes:
    import ssl

    return ssl.PEM_cert_to_DER_cert(pem)


def auth_mode() -> str:
    """Enforcement mode: "enforce" or "permissive".

    An explicit AGENT_AUTH_MODE setting wins. Otherwise the durable flag written
    through POST /api/agent/auth/enforce decides. A flag that exists but cannot
    be inspected fails closed.
    """
    explicit = (settings.agent_auth_mode or "").strip().lower()
    if explicit in ("enforce", "permissive"):
        return explicit
    try:
        return "enforce" if ENFORCE_FILE.exists() else "permissive"
    except OSError:
        return "enforce"


def is_enforced() -> bool:
    return auth_mode() == "enforce"


def set_enforced() -> None:
    """Persist the enforcement intent so losing the token file cannot reopen the API."""
    with _lock:
        _atomic_write(ENFORCE_FILE, "1")
    logger.warning("Agent API enforcement flag persisted; a missing token keeps the API locked")


def clear_enforced() -> None:
    with _lock:
        try:
            if ENFORCE_FILE.exists():
                ENFORCE_FILE.unlink()
        except Exception as e:
            logger.warning(f"Could not remove agent auth enforcement flag: {e}")


def tokens_match(provided: Optional[str], expected: Optional[str]) -> bool:
    if not provided or not expected:
        return False
    return hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8"))


def set_file_token(value: str) -> None:
    """Atomically persist a panel-provisioned token (0600)."""
    global _file_token, _loaded
    if not is_valid_token_format(value):
        raise ValueError("Invalid token format")
    with _lock:
        _atomic_write(TOKEN_FILE, value)
        _file_token = value
        _loaded = True


def clear_file_token() -> None:
    """Local unlock: forget the token, proof and enforcement flag."""
    global _file_token, _loaded
    try:
        with _lock:
            try:
                if TOKEN_FILE.exists():
                    TOKEN_FILE.unlink()
                if BOOTSTRAP_FILE.exists():
                    BOOTSTRAP_FILE.unlink()
            except Exception as e:
                logger.warning(f"Could not remove agent authentication state: {e}")
            _file_token = None
            _loaded = True
    finally:
        clear_enforced()


def is_loopback(host: Optional[str]) -> bool:
    if not host:
        return False
    return host in ("127.0.0.1", "::1", "localhost") or host.startswith("127.") or host == "::ffff:127.0.0.1"
