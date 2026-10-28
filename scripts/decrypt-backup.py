#!/usr/bin/env python3
"""Decrypt a passphrase-protected Smite Telegram backup archive.

Usage:
    python scripts/decrypt-backup.py smite_backup_YYYYmmdd_HHMMSS_xxxxxxxx.zip.enc
    python scripts/decrypt-backup.py backup.zip.enc -o backup.zip
"""
import argparse
import base64
import getpass
import sys
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

MAGIC = b"SMITE-BACKUP-1\n"
KDF_ITERATIONS = 390_000


def main() -> int:
    parser = argparse.ArgumentParser(description="Decrypt a Smite panel backup archive.")
    parser.add_argument("archive", type=Path, help="Path to the .zip.enc file")
    parser.add_argument("-o", "--output", type=Path, help="Output ZIP path (default: strip .enc)")
    parser.add_argument("--passphrase", help="Passphrase (omit to be prompted securely)")
    args = parser.parse_args()

    data = args.archive.read_bytes()
    if not data.startswith(MAGIC):
        print("Not a Smite encrypted backup archive.", file=sys.stderr)
        return 2

    salt = data[len(MAGIC):len(MAGIC) + 16]
    token = data[len(MAGIC) + 16:]
    passphrase = args.passphrase or getpass.getpass("Backup passphrase: ")

    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=KDF_ITERATIONS,
    )
    key = base64.urlsafe_b64encode(kdf.derive(passphrase.encode("utf-8")))

    try:
        plaintext = Fernet(key).decrypt(token)
    except InvalidToken:
        print("Invalid passphrase or corrupted archive.", file=sys.stderr)
        return 3

    output = args.output
    if output is None:
        name = args.archive.name
        output = args.archive.with_name(name[:-4] if name.endswith(".enc") else name + ".zip")
    output.write_bytes(plaintext)
    print(f"Decrypted archive written to: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
