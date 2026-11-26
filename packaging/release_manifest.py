from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from second_mind.updates import (
    MANIFEST_NAME,
    SIGNATURE_NAME,
    UPDATE_PUBLIC_KEY_PEM,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create signed Second Mind release metadata")
    parser.add_argument("--installer", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    encoded_key = os.environ.get("UPDATE_SIGNING_PRIVATE_KEY_B64")
    if not encoded_key:
        raise SystemExit("UPDATE_SIGNING_PRIVATE_KEY_B64 is required")
    try:
        private_key = serialization.load_pem_private_key(
            base64.b64decode(encoded_key, validate=True), password=None
        )
    except (TypeError, ValueError) as exc:
        raise SystemExit("The update signing key is invalid") from exc
    if not isinstance(private_key, Ed25519PrivateKey):
        raise SystemExit("The update signing key must be Ed25519")
    expected_public = serialization.load_pem_public_key(UPDATE_PUBLIC_KEY_PEM)
    if private_key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    ) != expected_public.public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    ):
        raise SystemExit("The update signing key does not match the embedded public key")

    installer = args.installer.resolve()
    digest = hashlib.sha256(installer.read_bytes()).hexdigest()
    manifest = {
        "schema_version": 1,
        "repository": args.repository,
        "tag": f"v{args.version}",
        "version": args.version,
        "installer": {
            "name": installer.name,
            "size": installer.stat().st_size,
            "sha256": digest,
        },
    }
    manifest_bytes = (
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode()
    signature = private_key.sign(manifest_bytes)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / MANIFEST_NAME).write_bytes(manifest_bytes)
    (args.output_dir / SIGNATURE_NAME).write_bytes(base64.b64encode(signature) + b"\n")
    (args.output_dir / "SHA256SUMS.txt").write_text(
        f"{digest}  {installer.name}\n", encoding="ascii"
    )


if __name__ == "__main__":
    main()
