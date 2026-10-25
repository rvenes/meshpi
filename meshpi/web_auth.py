"""Private, single-owner browser authentication, independent of the radio database."""

from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import json
import os
import re
import secrets
import stat
import struct
import subprocess
import tempfile
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fido2.server import Fido2Server
from fido2.utils import websafe_decode, websafe_encode
from fido2.webauthn import (
    AttestedCredentialData,
    AuthenticationResponse,
    RegistrationResponse,
)


class AuthError(ValueError):
    """An authentication request that must fail closed."""


@dataclass
class Session:
    token: str
    csrf: str
    authenticated: bool
    expires_at: float
    generation: str
    authenticated_at: float = 0
    credential_id: str | None = None


@dataclass
class _Challenge:
    kind: str
    state: Any
    expires_at: float
    challenge_id: str = ""


def canonical_origin(value: str) -> str:
    """Accept an exact HTTPS origin, or HTTP loopback for terminal-code login."""
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise AuthError("Ugyldig innloggingsadresse.") from exc
    if (
        not host
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
        or parsed.scheme not in ("https", "http")
    ):
        raise AuthError("Innloggingsadressa må vere ein origin utan sti.")
    if parsed.scheme == "http" and host not in ("localhost", "127.0.0.1", "::1"):
        raise AuthError("Innlogging utan HTTPS er berre tillaten på localhost.")
    host = host.encode("idna").decode("ascii").lower()
    display_host = f"[{host}]" if ":" in host else host
    suffix = f":{port}" if port and port != (443 if parsed.scheme == "https" else 80) else ""
    return f"{parsed.scheme}://{display_host}{suffix}"


class AuthStore:
    """Owns passkeys and codes on disk, and bounded sessions in this process.

    The HTTP adapter must enforce this store's exact origin and call verify_csrf
    on every browser mutation, including login. Never infer authentication from
    a loopback peer: reverse proxies also connect from loopback.
    """

    CODE_TTL = 300
    CODE_ATTEMPTS = 5
    PREAUTH_TTL = 600
    SESSION_TTL = 8 * 3600
    FRESH_TTL = 300
    CHALLENGE_TTL = 120
    MAX_SESSIONS = 256
    MAX_CREDENTIALS = 20

    def __init__(self, directory: Path, origin: str):
        self.directory = Path(directory).absolute()
        self.origin = canonical_origin(origin)
        self._mutex = threading.RLock()
        self._sessions: dict[str, Session] = {}
        self._challenges: dict[str, _Challenge] = {}
        self._prepare_directory()
        self._path = self.directory / "auth.json"
        self._lock_path = self.directory / "auth.lock"
        if os.name == "nt":
            for path in (self._path, self._lock_path):
                self._check_file(path)
                if path.exists():
                    self._secure_windows_path(path, directory=False)
        host = urlsplit(self.origin).hostname or ""
        try:
            ipaddress.ip_address(host)
            is_ip = True
        except ValueError:
            is_ip = False
        self.passkeys_available = (
            self.origin.startswith("https://") and not is_ip and host != "localhost"
        )
        self._server = (
            Fido2Server(
                {"id": host, "name": "MeshPi"},
                verify_origin=lambda candidate: candidate == self.origin,
            )
            if self.passkeys_available
            else None
        )
        with self._locked():
            if self._path.exists():
                self._read(allow_other_origin=True)
            else:
                self._write(
                    {
                        "version": 1,
                        "origin": self.origin,
                        "generation": secrets.token_hex(32),
                        "user_id": websafe_encode(secrets.token_bytes(32)),
                        "credentials": [],
                        "code": None,
                    }
                )

    def _prepare_directory(self) -> None:
        # Reject symlinked state paths, including parents, before opening files.
        for candidate in (self.directory, *self.directory.parents):
            if candidate.is_symlink():
                raise AuthError("Innloggingsmappa kan ikkje vere ei symbolsk lenkje.")
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = self.directory.stat()
        if not stat.S_ISDIR(info.st_mode):
            raise AuthError("Ugyldig innloggingsmappe.")
        if os.name != "nt":
            if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
                raise AuthError(
                    "Innloggingsmappa må vere privat (0700) og eigd av tenestebrukaren."
                )
        else:
            self._secure_windows_path(self.directory, directory=True)

    @staticmethod
    def _secure_windows_path(path: Path, *, directory: bool) -> None:
        # Replace the entire DACL, including old explicit grants, using the
        # current process SID. An absolute system executable avoids PATH lookup.
        from meshpi.platform_service import windows_powershell_path

        quoted = str(path).replace("'", "''")
        kind = "DirectorySecurity" if directory else "FileSecurity"
        inheritance = "ContainerInherit,ObjectInherit" if directory else "None"
        script = (
            "$ErrorActionPreference='Stop'; "
            "$sid=[System.Security.Principal.WindowsIdentity]::GetCurrent().User; "
            f"$acl=New-Object System.Security.AccessControl.{kind}; "
            "$acl.SetOwner($sid); $acl.SetAccessRuleProtection($true,$false); "
            "$rule=New-Object System.Security.AccessControl.FileSystemAccessRule("
            f"$sid,'FullControl','{inheritance}','None','Allow'); "
            "$acl.AddAccessRule($rule); "
            f"Set-Acl -LiteralPath '{quoted}' -AclObject $acl"
        )
        subprocess.run(
            [
                windows_powershell_path(),
                "-NoProfile",
                "-NonInteractive",
                "-EncodedCommand",
                base64.b64encode(script.encode("utf-16-le")).decode("ascii"),
            ],
            capture_output=True,
            check=True,
        )

    def _check_file(self, path: Path) -> None:
        try:
            info = path.lstat()
        except FileNotFoundError:
            return
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise AuthError("Utrygg innloggingsfil.")
        if os.name != "nt" and (info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077):
            raise AuthError("Innloggingsfila må vere privat (0600).")

    @contextmanager
    def _locked(self):
        with self._mutex:
            self._check_file(self._lock_path)
            flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
            fd = os.open(self._lock_path, flags, 0o600)
            try:
                if os.name == "nt":
                    import msvcrt

                    if os.fstat(fd).st_size == 0:
                        os.write(fd, b"0")
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
                else:
                    import fcntl

                    fcntl.flock(fd, fcntl.LOCK_EX)
                yield
            finally:
                os.close(fd)

    def _read(self, *, allow_other_origin: bool = False) -> dict:
        self._check_file(self._path)
        try:
            if self._path.stat().st_size > 128 * 1024:
                raise ValueError("oversize")
            state = json.loads(self._path.read_text(encoding="utf-8"))
            if state["version"] != 1 or (state["origin"] != self.origin and not allow_other_origin):
                raise ValueError("origin/version")
            if (
                not isinstance(state["credentials"], list)
                or len(state["credentials"]) > self.MAX_CREDENTIALS
            ):
                raise ValueError("credentials")
            if len(websafe_decode(state["user_id"])) != 32 or len(state["generation"]) != 64:
                raise ValueError("identity")
            for entry in state["credentials"]:
                if (
                    websafe_encode(
                        AttestedCredentialData(websafe_decode(entry["data"])).credential_id
                    )
                    != entry["id"]
                ):
                    raise ValueError("credential")
            return state
        except (OSError, ValueError, TypeError, KeyError) as exc:
            raise AuthError(
                "Innloggingsfila er ugyldig eller høyrer til ei anna innloggingsadresse."
            ) from exc

    def _write(self, state: dict) -> None:
        self._check_file(self._path)
        serialized = json.dumps(state, separators=(",", ":"))
        if len(serialized.encode("utf-8")) > 128 * 1024:
            raise AuthError("Innloggingslageret er fullt. Eksisterande nøklar er bevarte.")
        fd, name = tempfile.mkstemp(prefix=".auth-", dir=self.directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(serialized)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self._path)
            if os.name != "nt":
                dirfd = os.open(self.directory, os.O_RDONLY)
                try:
                    os.fsync(dirfd)
                finally:
                    os.close(dirfd)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def issue_code(self) -> str:
        """Local CLI only: issue a five-minute single-use code, revoking sessions."""
        code = secrets.token_urlsafe(24)
        with self._locked():
            state = self._read(allow_other_origin=True)
            if state["origin"] != self.origin:
                origins = state.setdefault("previous_origins", {})
                if state["origin"] not in origins and len(origins) >= 16:
                    raise AuthError("For mange lagra innloggingsadresser.")
                origins[state["origin"]] = {
                    "user_id": state["user_id"],
                    "credentials": state["credentials"],
                }
                restored = origins.pop(self.origin, None)
                state["user_id"] = (
                    restored["user_id"] if restored else websafe_encode(secrets.token_bytes(32))
                )
                state["credentials"] = restored["credentials"] if restored else []
                state["origin"] = self.origin
            state["generation"] = secrets.token_hex(32)
            state["code"] = {
                "digest": hashlib.sha256(code.encode()).hexdigest(),
                "expires_at": time.time() + self.CODE_TTL,
                "attempts": self.CODE_ATTEMPTS,
            }
            self._write(state)
            self._sessions.clear()
            self._challenges.clear()
        return code

    def _prune(self, state: dict) -> None:
        now = time.time()
        for token, session in list(self._sessions.items()):
            if session.expires_at <= now or session.generation != state["generation"]:
                self._sessions.pop(token, None)
                self._challenges.pop(token, None)
        for token, challenge in list(self._challenges.items()):
            if challenge.expires_at <= now:
                self._challenges.pop(token, None)

    def _new(
        self, state: dict, authenticated: bool = False, credential_id: str | None = None
    ) -> Session:
        self._prune(state)
        if len(self._sessions) >= self.MAX_SESSIONS:
            raise AuthError("For mange innloggingsforsøk. Prøv igjen seinare.")
        now = time.time()
        session = Session(
            token=secrets.token_urlsafe(32),
            csrf=secrets.token_urlsafe(32),
            authenticated=authenticated,
            expires_at=now + (self.SESSION_TTL if authenticated else self.PREAUTH_TTL),
            generation=state["generation"],
            authenticated_at=now if authenticated else 0,
            credential_id=credential_id,
        )
        self._sessions[session.token] = session
        return session

    def new_session(self) -> Session:
        with self._locked():
            return self._new(self._read())

    def session(self, token: str | None) -> Session | None:
        with self._locked():
            self._prune(self._read())
            return self._sessions.get(token or "")

    def _require(
        self, token: str, state: dict, *, authenticated: bool = False, fresh: bool = False
    ) -> Session:
        self._prune(state)
        session = self._sessions.get(token)
        if not session or (authenticated and not session.authenticated):
            raise AuthError("Du må logge inn på nytt.")
        if fresh and session.authenticated_at + self.FRESH_TTL <= time.time():
            raise AuthError("Stadfest innlogginga på nytt før du endrar passkeys.")
        return session

    def verify_csrf(self, token: str | None, csrf: str) -> bool:
        session = self.session(token)
        return bool(
            session
            and isinstance(csrf, str)
            and csrf.isascii()
            and hmac.compare_digest(session.csrf, csrf)
        )

    def logout(self, token: str) -> None:
        with self._mutex:
            self._sessions.pop(token, None)
            self._challenges.pop(token, None)

    def login_code(self, token: str, code: str) -> Session:
        with self._locked():
            state = self._read()
            self._require(token, state)
            entry = state["code"]
            if not entry or entry["expires_at"] <= time.time() or entry["attempts"] <= 0:
                raise AuthError("Innloggingskoden er ugyldig eller utgått.")
            entry["attempts"] -= 1
            valid = (
                isinstance(code, str)
                and len(code) <= 128
                and hmac.compare_digest(
                    entry["digest"],
                    hashlib.sha256(code.encode()).hexdigest(),
                )
            )
            if valid:
                state["code"] = None
            self._write(state)
            if not valid:
                raise AuthError("Innloggingskoden er ugyldig eller utgått.")
            self.logout(token)
            return self._new(state, True)

    def status(self, token: str | None) -> dict:
        session = self.session(token)
        return {
            "authenticated": bool(session and session.authenticated),
            "passkeys_available": self.passkeys_available,
            "origin": self.origin,
            "canonical_url": self.origin,
            "csrf": session.csrf if session else None,
        }

    def _credentials(self, state: dict) -> list[AttestedCredentialData]:
        return [
            AttestedCredentialData(websafe_decode(entry["data"])) for entry in state["credentials"]
        ]

    def _passkeys(self) -> Fido2Server:
        if not self._server:
            raise AuthError(
                "Passkeys krev den faste HTTPS-adressa. Bruk ein terminalkode på localhost."
            )
        return self._server

    def registration_begin(self, token: str) -> dict:
        with self._locked():
            state = self._read()
            self._require(token, state, authenticated=True, fresh=True)
            if len(state["credentials"]) >= self.MAX_CREDENTIALS:
                raise AuthError("Du har nådd grensa på 20 passkeys.")
            options, challenge = self._passkeys().register_begin(
                {
                    "id": websafe_decode(state["user_id"]),
                    "name": "MeshPi",
                    "displayName": "MeshPi-eigar",
                },
                self._credentials(state),
                resident_key_requirement="required",
                user_verification="required",
            )
            challenge_id = secrets.token_urlsafe(32)
            self._challenges[token] = _Challenge(
                "register", challenge, time.time() + self.CHALLENGE_TTL, challenge_id
            )
            return {"options": dict(options)["publicKey"], "challenge_id": challenge_id}

    def _consume(self, token: str, kind: str, challenge_id: str) -> _Challenge:
        challenge = self._challenges.pop(token, None)
        if (
            not challenge
            or challenge.kind != kind
            or challenge.expires_at <= time.time()
            or not isinstance(challenge_id, str)
            or not challenge_id.isascii()
            or not hmac.compare_digest(challenge.challenge_id, challenge_id)
        ):
            raise AuthError("Innloggingsførespurnaden er utgått eller allereie brukt.")
        return challenge

    @staticmethod
    def _browser_response(response: Any, *, registration: bool) -> dict:
        # The library accepts Python values as well as browser JSON. Validate the
        # JSON boundary first: bytes(integer) would otherwise allocate that many
        # bytes despite the HTTP body limit. Ignore unused browser metadata.
        if not isinstance(response, dict) or response.get("type") != "public-key":
            raise AuthError("Ugyldig passkey-svar.")
        body = response.get("response")
        if not isinstance(body, dict):
            raise AuthError("Ugyldig passkey-svar.")

        def encoded(value: Any, maximum: int) -> str:
            if not isinstance(value, str) or not 1 <= len(value) <= maximum:
                raise AuthError("Ugyldig passkey-svar.")
            if not re.fullmatch(r"[A-Za-z0-9_-]+={0,2}", value):
                raise AuthError("Ugyldig passkey-svar.")
            return value

        raw_id = encoded(response.get("rawId"), 2048)
        if response.get("id", raw_id) != raw_id:
            raise AuthError("Ugyldig passkey-svar.")
        clean = {"clientDataJSON": encoded(body.get("clientDataJSON"), 8192)}
        if registration:
            clean["attestationObject"] = encoded(body.get("attestationObject"), 16384)
        else:
            clean["authenticatorData"] = encoded(body.get("authenticatorData"), 8192)
            clean["signature"] = encoded(body.get("signature"), 2048)
            if body.get("userHandle") is not None:
                clean["userHandle"] = encoded(body["userHandle"], 128)
        return {"id": raw_id, "rawId": raw_id, "type": "public-key", "response": clean}

    @staticmethod
    def _check_client(client_data) -> None:
        if client_data.cross_origin or json.loads(client_data).get("topOrigin") is not None:
            raise AuthError("Innlogging i ei innebygd ramme er ikkje tillaten.")

    def registration_complete(
        self, token: str, challenge_id: str, response: dict, name: str
    ) -> dict:
        if (
            not isinstance(name, str)
            or not name.strip()
            or len(name) > 80
            or any(ord(char) < 32 for char in name)
        ):
            raise AuthError("Gi passkeyen eit namn på 1–80 teikn.")
        with self._locked():
            state = self._read()
            self._require(token, state, authenticated=True, fresh=True)
            challenge = self._consume(token, "register", challenge_id)
            try:
                parsed = RegistrationResponse.from_dict(
                    self._browser_response(response, registration=True)
                )
                self._check_client(parsed.response.client_data)
                data = self._passkeys().register_complete(challenge.state, parsed)
                credential = data.credential_data
                if credential is None or parsed.raw_id != credential.credential_id:
                    raise ValueError("credential ID")
                if data.is_backed_up() and not data.is_backup_eligible():
                    raise ValueError("backup flags")
                credential_id = websafe_encode(credential.credential_id)
                if any(entry["id"] == credential_id for entry in state["credentials"]):
                    raise ValueError("duplicate")
                if len(state["credentials"]) >= self.MAX_CREDENTIALS:
                    raise ValueError("capacity")
            except (
                ValueError,
                TypeError,
                KeyError,
                AttributeError,
                IndexError,
                struct.error,
                OverflowError,
                RecursionError,
            ) as exc:
                raise AuthError("Passkeyen kunne ikkje stadfestast.") from exc
            entry = {
                "id": credential_id,
                "name": name.strip(),
                "data": websafe_encode(bytes(credential)),
                "counter": data.counter,
                "backup_eligible": data.is_backup_eligible(),
                "created_at": int(time.time()),
            }
            state["credentials"].append(entry)
            self._write(state)
            return {key: entry[key] for key in ("id", "name", "created_at")}

    def authentication_begin(self, token: str) -> dict:
        with self._locked():
            state = self._read()
            self._require(token, state)
            if not state["credentials"]:
                raise AuthError("Ingen passkeys er registrerte. Bruk ein terminalkode først.")
            options, challenge = self._passkeys().authenticate_begin(
                self._credentials(state),
                user_verification="required",
            )
            challenge_id = secrets.token_urlsafe(32)
            self._challenges[token] = _Challenge(
                "authenticate", challenge, time.time() + self.CHALLENGE_TTL, challenge_id
            )
            return {"options": dict(options)["publicKey"], "challenge_id": challenge_id}

    def authentication_complete(self, token: str, challenge_id: str, response: dict) -> Session:
        with self._locked():
            state = self._read()
            self._require(token, state)
            challenge = self._consume(token, "authenticate", challenge_id)
            try:
                parsed = AuthenticationResponse.from_dict(
                    self._browser_response(response, registration=False)
                )
                self._check_client(parsed.response.client_data)
                if parsed.response.user_handle not in (None, websafe_decode(state["user_id"])):
                    raise ValueError("user handle")
                credential = self._passkeys().authenticate_complete(
                    challenge.state, self._credentials(state), parsed
                )
                credential_id = websafe_encode(credential.credential_id)
                entry = next(
                    entry for entry in state["credentials"] if entry["id"] == credential_id
                )
                data = parsed.response.authenticator_data
                if data.is_backup_eligible() != entry["backup_eligible"] or (
                    data.is_backed_up() and not data.is_backup_eligible()
                ):
                    raise ValueError("backup flags")
                if (entry["counter"] or data.counter) and data.counter <= entry["counter"]:
                    raise ValueError("counter did not advance")
            except (
                ValueError,
                TypeError,
                KeyError,
                AttributeError,
                IndexError,
                struct.error,
                OverflowError,
                RecursionError,
                StopIteration,
            ) as exc:
                raise AuthError("Passkey-innlogginga kunne ikkje stadfestast.") from exc
            entry["counter"] = data.counter
            self._write(state)
            self.logout(token)
            return self._new(state, True, credential_id)

    def list_credentials(self, token: str) -> list[dict]:
        with self._locked():
            state = self._read()
            self._require(token, state, authenticated=True)
            return [
                {key: entry[key] for key in ("id", "name", "created_at")}
                for entry in state["credentials"]
            ]

    def delete_credential(self, token: str, credential_id: str) -> None:
        """Delete a key and revoke every session. A local code remains recovery."""
        with self._locked():
            state = self._read()
            self._require(token, state, authenticated=True, fresh=True)
            entries = [entry for entry in state["credentials"] if entry["id"] != credential_id]
            if len(entries) == len(state["credentials"]):
                raise AuthError("Passkeyen finst ikkje.")
            state["credentials"] = entries
            state["generation"] = secrets.token_hex(32)
            state["code"] = None
            self._write(state)
            self._sessions.clear()
            self._challenges.clear()
