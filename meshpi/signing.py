from __future__ import annotations

import base64
import hashlib
import hmac
import json
from typing import Any

SIGNATURE_ALGORITHM = "rsa-pkcs1v15-sha256"
SIGNING_KEY_ID = "meshpi-release-2026-01"
PUBLIC_EXPONENT = 65537
PUBLIC_MODULUS = int(
    "c1370fa9e2eb0d22e354c58594e369f9db44156f834522bf69a8da523a30ac0d4539e08a30d76e854b40ae693da388af11ca62ee24c1e6f43ec128be550e8b7655d86955ae858b9f30237ba02e2773e9ad2fcfe1644484e909a8805a6c8a289dda69cedbc973d7427278442d8acb1d00a0c5cd242c34404843ea684ece7ad40a59d902633624ae36ae3f4e8c9e401bb887ef650f1fe001f9fd7661841b98a95f67aea496c05054a4c41c287c09d1dd1e94e9c01cc997162a50e02df6d28645d268cceb35daf7ad1e4202b2b1714a71e2b18d0564f12a468c2bb4d7e678a1c4c493de0c945f0f2665efb658238dd4dd617b73acd8e20e4c5f440d2d4ee13617f2c2857c0457e0a3a73aac43d0e23f5c0f56f9042a6d1e6221383481a9bcc952576904895e013a5f12b6c0aa08b9ba911df7be42a4d0a3c31ca98111b4344d8079fdb55a43379fde9968edf9ce7b3554333d5819ad196935e928012d1b20b4aed5ee48d8851dd69458b15998712530b4d91228b06ae109741c0cf4ab723f092e49",
    16,
)
_SHA256_DIGEST_INFO = bytes.fromhex("3031300d060960864801650304020105000420")


class SignatureError(ValueError):
    pass


def canonical_manifest_bytes(manifest: dict[str, Any]) -> bytes:
    unsigned = dict(manifest)
    unsigned.pop("signature", None)
    return json.dumps(
        unsigned,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def verify_manifest_signature(manifest: dict[str, Any]) -> None:
    signature = manifest.get("signature")
    if not isinstance(signature, dict):
        raise SignatureError("Versjonsmanifestet manglar signatur")
    if signature.get("algorithm") != SIGNATURE_ALGORITHM:
        raise SignatureError("Versjonsmanifestet bruker ukjend signaturalgoritme")
    if signature.get("key_id") != SIGNING_KEY_ID:
        raise SignatureError("Versjonsmanifestet bruker ukjend signeringsnøkkel")
    try:
        raw_signature = base64.b64decode(str(signature["value"]), validate=True)
    except (KeyError, ValueError) as exc:
        raise SignatureError("Versjonsmanifestet har ugyldig signatur") from exc

    size = (PUBLIC_MODULUS.bit_length() + 7) // 8
    if len(raw_signature) != size:
        raise SignatureError("Versjonsmanifestet har feil signaturlengd")
    encoded = pow(int.from_bytes(raw_signature, "big"), PUBLIC_EXPONENT, PUBLIC_MODULUS)
    actual = encoded.to_bytes(size, "big")
    digest = hashlib.sha256(canonical_manifest_bytes(manifest)).digest()
    padding_size = size - len(_SHA256_DIGEST_INFO) - len(digest) - 3
    expected = (
        b"\x00\x01"
        + b"\xff" * padding_size
        + b"\x00"
        + _SHA256_DIGEST_INFO
        + digest
    )
    if padding_size < 8 or not hmac.compare_digest(actual, expected):
        raise SignatureError("Signaturen på versjonsmanifestet stemmer ikkje")

