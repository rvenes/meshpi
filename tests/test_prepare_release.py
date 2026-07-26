import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from scripts.prepare_release import sign_manifest


def test_prepare_release_rejects_private_key_that_does_not_match_key_id():
    unrelated_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    manifest = {"schema_version": 1, "product": "MeshPi"}

    with pytest.raises(SystemExit, match="samsvarar ikkje med key_id"):
        sign_manifest(manifest, unrelated_key)
