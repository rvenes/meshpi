import base64
import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from meshpi.signing import (
    PUBLIC_EXPONENT,
    PUBLIC_MODULUS,
    SIGNING_KEY_ID,
    SignatureError,
    canonical_manifest_bytes,
    verify_manifest_signature,
)

ROOT = Path(__file__).resolve().parents[1]


def manifest():
    return json.loads((ROOT / "website" / "version.json").read_text(encoding="utf-8"))


def beta_manifest():
    return json.loads(
        (ROOT / "website" / "beta" / "version.json").read_text(encoding="utf-8")
    )


def test_manifest_accepts_key_from_explicit_trust_registry():
    verify_manifest_signature(
        manifest(),
        trusted_keys={SIGNING_KEY_ID: (PUBLIC_EXPONENT, PUBLIC_MODULUS)},
    )


def test_beta_seed_manifest_is_signed_and_keeps_stable_manifest_isolated():
    stable = manifest()
    beta = beta_manifest()

    verify_manifest_signature(beta)
    assert stable["channel"] == "stable"
    assert beta["channel"] == "beta"
    assert beta["latest_version"] == stable["latest_version"]
    assert beta["package"] == stable["package"]


def test_manifest_rejects_revoked_signing_key():
    with pytest.raises(SignatureError, match="tilbakekalla"):
        verify_manifest_signature(
            manifest(),
            revoked_key_ids=frozenset({SIGNING_KEY_ID}),
        )


def test_manifest_rejects_unknown_signing_key_before_crypto():
    value = manifest()
    value["signature"]["key_id"] = "ukjend"

    with pytest.raises(SignatureError, match="ukjend signeringsnøkkel"):
        verify_manifest_signature(value)


def test_manifest_accepts_a_second_key_during_rotation():
    next_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_numbers = next_key.public_key().public_numbers()
    value = {"schema_version": 1, "product": "MeshPi"}
    signature = next_key.sign(
        canonical_manifest_bytes(value),
        padding.PKCS1v15(),
        hashes.SHA256(),
    )
    value["signature"] = {
        "algorithm": "rsa-pkcs1v15-sha256",
        "key_id": "meshpi-release-next-test",
        "value": base64.b64encode(signature).decode("ascii"),
    }

    verify_manifest_signature(
        value,
        trusted_keys={
            SIGNING_KEY_ID: (PUBLIC_EXPONENT, PUBLIC_MODULUS),
            "meshpi-release-next-test": (public_numbers.e, public_numbers.n),
        },
    )
