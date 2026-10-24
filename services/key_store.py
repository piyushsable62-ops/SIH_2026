"""
Cryptographic key management service for APAAR Nexus credentials.
Generates, loads, and manages Ed25519 signing keys.
Private keys are stored exclusively on the local filesystem (instance/keys)
and are NEVER persisted to the database or output in logs.
"""
from pathlib import Path
from typing import Optional, Tuple
import os

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives import serialization
from flask import current_app

DEFAULT_KEY_ID = "KEY-2026-DEV-01"


def get_key_dir() -> Path:
    """Resolve directory where cryptographic private keys reside."""
    if current_app:
        configured_dir = current_app.config.get("SIGNING_KEY_DIR", "instance/keys")
        if os.path.isabs(configured_dir):
            key_path = Path(configured_dir)
        else:
            base_dir = Path(current_app.root_path)
            key_path = base_dir / configured_dir
    else:
        # Fallback when running outside application context (e.g. standalone seed)
        base_dir = Path(__file__).resolve().parents[1]
        key_path = base_dir / "instance" / "keys"

    key_path.mkdir(parents=True, exist_ok=True)
    return key_path


def ensure_development_key(key_id: str = DEFAULT_KEY_ID) -> Tuple[str, str]:
    """
    Ensure an Ed25519 development key pair exists on the filesystem.
    Generates a new key pair if absent.
    Returns (key_id, public_key_pem).
    """
    key_dir = get_key_dir()
    private_key_file = key_dir / f"{key_id}.pem"

    if private_key_file.exists():
        with open(private_key_file, "rb") as f:
            private_key = serialization.load_pem_private_key(f.read(), password=None)
    else:
        # Generate fresh Ed25519 private key
        private_key = Ed25519PrivateKey.generate()
        pem_bytes = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        with open(private_key_file, "wb") as f:
            f.write(pem_bytes)

    # Derive public key in PEM format
    public_key = private_key.public_key()
    pub_pem_bytes = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return key_id, pub_pem_bytes.decode("utf-8")


def load_private_key(key_id: Optional[str] = None) -> Ed25519PrivateKey:
    """
    Load an Ed25519 private key object from the filesystem by key_id.
    Private key is returned as an in-memory object and is never logged.
    """
    actual_key_id = key_id or get_active_key_id()
    key_dir = get_key_dir()
    private_key_file = key_dir / f"{actual_key_id}.pem"

    if not private_key_file.exists():
        ensure_development_key(actual_key_id)

    with open(private_key_file, "rb") as f:
        key_obj = serialization.load_pem_private_key(f.read(), password=None)
        if not isinstance(key_obj, Ed25519PrivateKey):
            raise TypeError(f"Key {actual_key_id} is not an Ed25519 private key")
        return key_obj


def get_active_key_id() -> str:
    """Return the active signing key identifier."""
    if current_app:
        return current_app.config.get("ACTIVE_KEY_ID", DEFAULT_KEY_ID)
    return os.getenv("ACTIVE_KEY_ID", DEFAULT_KEY_ID)


def get_public_key_pem(key_id: Optional[str] = None) -> str:
    """Retrieve public key in PEM format for a given key_id."""
    actual_key_id = key_id or get_active_key_id()
    _, pub_pem = ensure_development_key(actual_key_id)
    return pub_pem
