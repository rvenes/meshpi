"""Authentication tests use a software ES256 authenticator; no radio or hardware."""

import copy
import hashlib
import json
import os
import stat
from concurrent.futures import ThreadPoolExecutor

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from fido2 import cbor
from fido2.cose import ES256
from fido2.utils import websafe_decode, websafe_encode
from fido2.webauthn import AttestedCredentialData, AuthenticatorData, CollectedClientData

from meshpi.web_auth import AuthError, AuthStore, canonical_origin

ORIGIN = "https://meshpi.example.test"


class SoftwareAuthenticator:
    def __init__(self):
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.credential_id = os.urandom(32)
        self.user_handle = None

    def register(self, begin, *, origin=ORIGIN, rp_id=None, flags=0x45):
        options = begin["options"]
        self.user_handle = options["user"]["id"]
        client = CollectedClientData.create("webauthn.create", options["challenge"], origin)
        credential = AttestedCredentialData.create(
            bytes(16), self.credential_id, ES256.from_cryptography_key(self.key.public_key())
        )
        auth = AuthenticatorData.create(
            hashlib.sha256((rp_id or options["rp"]["id"]).encode()).digest(),
            flags,
            0,
            credential,
        )
        # Self-attestation is signed, although the RP intentionally requests none:
        # ownership of the key is proved by a subsequent signed assertion.
        attestation = cbor.encode(
            {
                "fmt": "packed",
                "authData": bytes(auth),
                "attStmt": {
                    "alg": -7,
                    "sig": self.key.sign(auth + client.hash, ec.ECDSA(hashes.SHA256())),
                },
            }
        )
        return {
            "id": websafe_encode(self.credential_id),
            "rawId": websafe_encode(self.credential_id),
            "type": "public-key",
            "response": {
                "clientDataJSON": websafe_encode(client),
                "attestationObject": websafe_encode(attestation),
            },
        }

    def authenticate(
        self, begin, *, origin=ORIGIN, rp_id=None, flags=0x05, counter=0, cross_origin=False
    ):
        options = begin["options"]
        client = CollectedClientData.create(
            "webauthn.get",
            options["challenge"],
            origin,
            cross_origin=cross_origin,
        )
        auth = AuthenticatorData.create(
            hashlib.sha256((rp_id or options["rpId"]).encode()).digest(),
            flags,
            counter,
        )
        signature = self.key.sign(auth + client.hash, ec.ECDSA(hashes.SHA256()))
        return {
            "id": websafe_encode(self.credential_id),
            "rawId": websafe_encode(self.credential_id),
            "type": "public-key",
            "response": {
                "clientDataJSON": websafe_encode(client),
                "authenticatorData": websafe_encode(auth),
                "signature": websafe_encode(signature),
                "userHandle": self.user_handle,
            },
        }


@pytest.fixture
def store(tmp_path):
    return AuthStore(tmp_path / "auth", ORIGIN)


def owner(store):
    code = store.issue_code()
    session = store.new_session()
    return store.login_code(session.token, code)


def register(store):
    session = owner(store)
    authenticator = SoftwareAuthenticator()
    begin = store.registration_begin(session.token)
    entry = store.registration_complete(
        session.token, begin["challenge_id"], authenticator.register(begin), "Testnøkkel"
    )
    return session, authenticator, entry


def test_terminal_code_hash_private_permissions_and_single_use(store):
    code = store.issue_code()
    assert code not in (store.directory / "auth.json").read_text()
    assert stat.S_IMODE(store.directory.stat().st_mode) == 0o700
    assert stat.S_IMODE((store.directory / "auth.json").stat().st_mode) == 0o600
    preauth = store.new_session()
    assert store.verify_csrf(preauth.token, preauth.csrf)
    assert not store.verify_csrf(preauth.token, "wrong")
    authenticated = store.login_code(preauth.token, code)
    assert authenticated.authenticated
    assert authenticated.csrf != preauth.csrf
    assert authenticated.token != preauth.token
    assert store.session(preauth.token) is None
    with pytest.raises(AuthError):
        store.login_code(store.new_session().token, code)


def test_code_attempt_limit_persists_across_process_instances(store):
    code = store.issue_code()
    for _ in range(store.CODE_ATTEMPTS):
        another = AuthStore(store.directory, ORIGIN)
        with pytest.raises(AuthError):
            another.login_code(another.new_session().token, "bad")
    with pytest.raises(AuthError):
        store.login_code(store.new_session().token, code)


def test_code_expiry_and_generation_revocation(store, monkeypatch):
    import meshpi.web_auth as module

    now = module.time.time()
    code = store.issue_code()
    session = store.new_session()
    monkeypatch.setattr(module.time, "time", lambda: now + store.CODE_TTL + 1)
    with pytest.raises(AuthError):
        store.login_code(session.token, code)
    authenticated = owner(store)
    other = AuthStore(store.directory, ORIGIN)
    other.issue_code()
    assert store.session(authenticated.token) is None


def test_code_concurrent_consumers_only_one_succeeds(store):
    code = store.issue_code()
    stores = [AuthStore(store.directory, ORIGIN) for _ in range(8)]
    sessions = [entry.new_session() for entry in stores]

    def consume(pair):
        other, session = pair
        try:
            other.login_code(session.token, code)
            return True
        except AuthError:
            return False

    with ThreadPoolExecutor(8) as pool:
        assert sum(pool.map(consume, zip(stores, sessions, strict=True))) == 1


def test_real_es256_registration_assertion_rotation_and_replay(store):
    registered, authenticator, entry = register(store)
    assert store.list_credentials(registered.token)[0] == entry
    assert "data" not in entry
    preauth = store.new_session()
    begin = store.authentication_begin(preauth.token)
    response = authenticator.authenticate(begin)
    logged_in = store.authentication_complete(preauth.token, begin["challenge_id"], response)
    assert logged_in.authenticated and logged_in.credential_id == entry["id"]
    assert store.session(preauth.token) is None
    with pytest.raises(AuthError):
        store.authentication_complete(preauth.token, begin["challenge_id"], response)
    fresh = store.new_session()
    another = store.authentication_begin(fresh.token)
    with pytest.raises(AuthError):
        store.authentication_complete(fresh.token, another["challenge_id"], response)


@pytest.mark.parametrize(
    "mutation", ["origin", "rp", "uv", "presence", "cross", "signature", "user", "backup"]
)
def test_signed_assertions_reject_security_property_violations(store, mutation):
    _, authenticator, _ = register(store)
    session = store.new_session()
    begin = store.authentication_begin(session.token)
    kwargs = {
        "origin": {"origin": "https://evil.example.test"},
        "rp": {"rp_id": "evil.example.test"},
        "uv": {"flags": 0x01},
        "presence": {"flags": 0x04},
        "cross": {"cross_origin": True},
        "backup": {"flags": 0x15},
    }.get(mutation, {})
    response = authenticator.authenticate(begin, **kwargs)
    if mutation == "signature":
        signature = bytearray(websafe_decode(response["response"]["signature"]))
        signature[-1] ^= 1
        response["response"]["signature"] = websafe_encode(signature)
    if mutation == "user":
        response["response"]["userHandle"] = websafe_encode(b"another-owner")
    with pytest.raises(AuthError):
        store.authentication_complete(session.token, begin["challenge_id"], response)
    with pytest.raises(AuthError):
        store.authentication_complete(
            session.token, begin["challenge_id"], authenticator.authenticate(begin)
        )
    assert not store.session(session.token).authenticated


@pytest.mark.parametrize(
    "kwargs",
    [
        {"origin": "https://elsewhere.example.test"},
        {"rp_id": "another.example.test"},
        {"flags": 0x41},
        {"flags": 0x44},
    ],
)
def test_registration_checks_origin_rp_and_presence_verification(store, kwargs):
    session = owner(store)
    authenticator = SoftwareAuthenticator()
    begin = store.registration_begin(session.token)
    with pytest.raises(AuthError):
        store.registration_complete(
            session.token, begin["challenge_id"], authenticator.register(begin, **kwargs), "Key"
        )
    assert store.list_credentials(session.token) == []


def test_registration_needs_fresh_authenticated_session(store, monkeypatch):
    with pytest.raises(AuthError):
        store.registration_begin(store.new_session().token)
    authenticated = owner(store)
    monkeypatch.setattr(
        "meshpi.web_auth.time.time", lambda: authenticated.authenticated_at + store.FRESH_TTL + 1
    )
    with pytest.raises(AuthError):
        store.registration_begin(authenticated.token)


def test_challenges_are_session_bound_expire_and_consumed_on_wrong_id(store, monkeypatch):
    _, authenticator, _ = register(store)
    one, two = store.new_session(), store.new_session()
    begin = store.authentication_begin(one.token)
    response = authenticator.authenticate(begin)
    with pytest.raises(AuthError):
        store.authentication_complete(two.token, begin["challenge_id"], response)
    with pytest.raises(AuthError):
        store.authentication_complete(one.token, "wrong", response)
    with pytest.raises(AuthError):
        store.authentication_complete(one.token, begin["challenge_id"], response)
    begin = store.authentication_begin(one.token)
    now = __import__("time").time()
    monkeypatch.setattr("meshpi.web_auth.time.time", lambda: now + store.CHALLENGE_TTL + 1)
    with pytest.raises(AuthError):
        store.authentication_complete(
            one.token, begin["challenge_id"], authenticator.authenticate(begin)
        )


def test_multiple_keys_and_delete_revokes_every_session(store):
    authenticated, _, first = register(store)
    second = SoftwareAuthenticator()
    begin = store.registration_begin(authenticated.token)
    store.registration_complete(
        authenticated.token, begin["challenge_id"], second.register(begin), "Andre"
    )
    other = store.new_session()
    assert len(store.list_credentials(authenticated.token)) == 2
    store.delete_credential(authenticated.token, first["id"])
    assert store.session(authenticated.token) is None
    assert store.session(other.token) is None
    begin_session = store.new_session()
    options = store.authentication_begin(begin_session.token)
    assert len(options["options"]["allowCredentials"]) == 1


def test_sign_counter_detects_reuse_but_supports_zero_counters(store):
    _, authenticator, _ = register(store)
    for counter, expected in [
        (0, True),
        (0, True),
        (10, True),
        (10, False),
        (0, False),
        (11, True),
    ]:
        session = store.new_session()
        begin = store.authentication_begin(session.token)
        response = authenticator.authenticate(begin, counter=counter)
        if expected:
            assert store.authentication_complete(
                session.token, begin["challenge_id"], response
            ).authenticated
        else:
            with pytest.raises(AuthError):
                store.authentication_complete(session.token, begin["challenge_id"], response)


def test_origin_migration_preserves_keys_and_requires_local_code(store):
    authenticated, authenticator, entry = register(store)
    local = AuthStore(store.directory, "http://localhost:8780")
    with pytest.raises(AuthError):
        local.new_session()
    local_owner = owner(local)
    assert local.list_credentials(local_owner.token) == []
    assert not local.passkeys_available
    with pytest.raises(AuthError):
        store.list_credentials(authenticated.token)
    with pytest.raises(AuthError):
        local.registration_begin(local_owner.token)
    restored = owner(store)
    assert store.list_credentials(restored.token) == [entry]
    session = store.new_session()
    begin = store.authentication_begin(session.token)
    assert store.authentication_complete(
        session.token, begin["challenge_id"], authenticator.authenticate(begin)
    ).authenticated


def test_session_capacity_and_expiry(store, monkeypatch):
    monkeypatch.setattr(store, "MAX_SESSIONS", 2)
    first = store.new_session()
    store.new_session()
    with pytest.raises(AuthError):
        store.new_session()
    monkeypatch.setattr("meshpi.web_auth.time.time", lambda: first.expires_at + 1)
    assert store.session(first.token) is None
    assert store.new_session()


@pytest.mark.parametrize(
    "bad",
    [
        "https://a.test/path",
        "https://user:pass@a.test",
        "http://a.test",
        "https://a.test#x",
        "https://a.test?q=x",
    ],
)
def test_invalid_origins(bad):
    with pytest.raises(AuthError):
        canonical_origin(bad)


def test_symlink_and_permissive_files_rejected(tmp_path):
    target = tmp_path / "target"
    target.mkdir(mode=0o700)
    link = tmp_path / "link"
    link.symlink_to(target)
    with pytest.raises(AuthError):
        AuthStore(link, ORIGIN)
    store = AuthStore(target, ORIGIN)
    (target / "auth.json").chmod(0o644)
    with pytest.raises(AuthError):
        store.new_session()


def test_state_corruption_fails_closed_and_keeps_file(store):
    path = store.directory / "auth.json"
    original = path.read_text()
    path.write_text("broken")
    with pytest.raises(AuthError):
        store.issue_code()
    assert path.read_text() == "broken"
    path.write_text(original)
    data = json.loads(original)
    data["origin"] = "https://elsewhere.example.test"
    path.write_text(json.dumps(data))
    with pytest.raises(AuthError):
        store.new_session()


def test_json_options_are_browser_serializable(store):
    session = owner(store)
    options = store.registration_begin(session.token)
    assert json.loads(json.dumps(options)) == options
    assert options["options"]["authenticatorSelection"]["userVerification"] == "required"
    malformed = copy.deepcopy(options)
    assert isinstance(malformed["options"]["challenge"], str)


def _consume_code_in_process(arguments):
    directory, code = arguments
    instance = AuthStore(directory, ORIGIN)
    try:
        return instance.login_code(instance.new_session().token, code).authenticated
    except AuthError:
        return False


def test_separate_processes_atomically_consume_code(store):
    import multiprocessing
    from concurrent.futures import ProcessPoolExecutor

    code = store.issue_code()
    with ProcessPoolExecutor(3, mp_context=multiprocessing.get_context("spawn")) as executor:
        results = list(executor.map(_consume_code_in_process, [(store.directory, code)] * 6))
    assert results.count(True) == 1


def test_csrf_tokens_bound_to_session_and_authenticated_session_expires(store, monkeypatch):
    authenticated = owner(store)
    other = store.new_session()
    assert not store.verify_csrf(authenticated.token, other.csrf)
    monkeypatch.setattr("meshpi.web_auth.time.time", lambda: authenticated.expires_at + 1)
    assert store.session(authenticated.token) is None
    assert not store.verify_csrf(authenticated.token, authenticated.csrf)


def test_browser_cannot_register_more_than_limit_or_reuse_registration(store, monkeypatch):
    session = owner(store)
    authenticator = SoftwareAuthenticator()
    begin = store.registration_begin(session.token)
    response = authenticator.register(begin)
    store.registration_complete(session.token, begin["challenge_id"], response, "Første")
    with pytest.raises(AuthError):
        store.registration_complete(session.token, begin["challenge_id"], response, "Omatt")
    monkeypatch.setattr(store, "MAX_CREDENTIALS", 1)
    with pytest.raises(AuthError):
        store.registration_begin(session.token)


def test_untrusted_file_types_never_followed(store, tmp_path):
    path = store.directory / "auth.json"
    saved = path.read_bytes()
    path.unlink()
    external = tmp_path / "external"
    external.write_bytes(saved)
    external.chmod(0o600)
    path.symlink_to(external)
    with pytest.raises(AuthError):
        store.issue_code()
    assert external.read_bytes() == saved


@pytest.mark.parametrize("registration", [True, False])
@pytest.mark.parametrize(
    "malformation",
    [
        "none",
        "list",
        "integer",
        "no-body",
        "list-body",
        "integer-id",
        "integer-client",
        "integer-binary",
        "bad-id",
        "large-client",
        "truncated",
        "bad-base64",
        "unicode-challenge",
    ],
)
def test_malformed_browser_inputs_are_auth_errors(store, registration, malformation):
    authenticated, authenticator, _ = register(store)
    if registration:
        authenticator = SoftwareAuthenticator()
        session = authenticated
        begin = store.registration_begin(session.token)
        response = authenticator.register(begin)
    else:
        session = store.new_session()
        begin = store.authentication_begin(session.token)
        response = authenticator.authenticate(begin)
    challenge = begin["challenge_id"]
    if malformation in ("none", "list", "integer"):
        response = {"none": None, "list": [], "integer": 42}[malformation]
    elif malformation == "no-body":
        response.pop("response")
    elif malformation == "list-body":
        response["response"] = []
    elif malformation == "integer-id":
        response["rawId"] = response["id"] = 10**18
    elif malformation == "integer-client":
        response["response"]["clientDataJSON"] = 10**18
    elif malformation == "integer-binary":
        response["response"]["attestationObject" if registration else "authenticatorData"] = 10**18
    elif malformation == "bad-id":
        response["id"] = "not-the-raw-id"
    elif malformation == "large-client":
        response["response"]["clientDataJSON"] = "a" * 8193
    elif malformation == "truncated":
        response["response"]["attestationObject" if registration else "authenticatorData"] = "AA"
    elif malformation == "bad-base64":
        response["response"]["clientDataJSON"] = "{}!"
    elif malformation == "unicode-challenge":
        challenge = "nøkkel"
    with pytest.raises(AuthError):
        if registration:
            store.registration_complete(session.token, challenge, response, "Nøkkel")
        else:
            store.authentication_complete(session.token, challenge, response)


def test_unicode_csrf_rejected_without_exception(store):
    session = store.new_session()
    assert not store.verify_csrf(session.token, "nøkkel")


def test_session_exhaustion_preserves_owner_session(store, monkeypatch):
    authenticated = owner(store)
    monkeypatch.setattr(store, "MAX_SESSIONS", 2)
    store.new_session()
    with pytest.raises(AuthError):
        store.new_session()
    assert store.session(authenticated.token) is authenticated


def test_oversized_state_write_preserves_previous_valid_store(store):
    before = (store.directory / "auth.json").read_bytes()
    with store._locked():
        state = store._read()
        state["oversize"] = "x" * (128 * 1024)
        with pytest.raises(AuthError):
            store._write(state)
    assert (store.directory / "auth.json").read_bytes() == before
    assert store.new_session()
