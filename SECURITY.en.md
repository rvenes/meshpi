# Security in MeshPi

[Nynorsk](SECURITY.md) | **English**

## Reporting vulnerabilities

Do not publish details about an unverified vulnerability in a public issue.
Contact the maintainer privately through the contact information at
`venes.org`. Include the MeshPi version, platform, reproduction steps, and
possible impact. Do not attach private messages, node IDs, tokens, databases,
or release keys.

## Update trust model

`meshpi update` downloads the manifest over HTTPS and accepts it only when its
RSA-PKCS1v1.5/SHA-256 signature comes from an allow-listed, non-revoked
`key_id`. The updater then downloads the installer, platform lock file, and
wheel into a private temporary directory. The signed size and SHA-256 must
match for all three before the locally verified installer is run. The updater
never runs `update_command` or any other command from the manifest.

The stable and beta channels have separate HTTPS addresses and a signed
`channel` value. `meshpi update` accepts only the stable manifest, while
`meshpi update --beta` accepts only the beta manifest. Both channels use the
same allow-listed release key and the same size and SHA-256 checks.

HTTPS remains important against downgrade and denial-of-service attacks, but
changing the manifest or artifacts on the web server is not enough to execute
code without the private release key.

## Key rotation and revocation

First installation still trusts the downloaded installer and its distribution
channel. A compromised first installer could replace its own trust checks;
its embedded public key is not an independent trust root. Inspect or obtain
the first installer through a separately authenticated route when needed.

Since 0.9.2, the verified MeshPi wheel bootstraps pip from a wheel whose hash
is in the signed platform lock. Only then are dependencies installed, using
`--require-hashes` and `--only-binary=:all:`. The Python interpreter and OS
remain trusted. The bootstrap does not use venv's older pip to download or
install dependencies, and fails if compatible binary wheels are unavailable.

The key registry is stored in `meshpi/signing.py` and in each installer. The
normal rotation procedure is:

1. Generate the new private key outside the repository and release directory.
2. Add only the public key and its new `key_id` to the application, all three
   installers, and the release tool.
3. Publish a transition release signed by the old, still-trusted key.
4. After the transition release has been distributed, sign new manifests with
   the new key.
5. Retain the old public key for as long as supported clients may encounter
   older signed manifests, or add it to the revocation list if compromised.

Revocation cannot repair a client that knows only a compromised key. Such a
client must receive new trust through a separate authenticated distribution
route or a manual installation. Private keys must never be stored in Git,
build artifacts, logs, or the public website directory.

## Local boundaries

The default installation uses a private Unix socket on Linux and macOS. On
Windows, IPC is restricted to an exclusively reserved loopback TCP port. An
IPC token is checked for both transports. Meshtastic TCP on port 4403 is
unencrypted and should be used only on a trusted network or through a protected
tunnel. MeshPi does not change radio configuration and never sends messages
automatically.

Linux `always` mode grants socket access through the operator's primary group
only when that group is private to one account. Installation rejects a shared
primary group so that other local users cannot open the IPC socket or exhaust
the connection quota.

Language selection does not change these boundaries. Nynorsk and English
messages describe the same checks and operations; language values are limited
to `nn` and `en`. A fresh installer writes only the per-user language file.
Existing configuration, profiles, database content, IPC tokens, and service
permissions are not changed by language selection.

## Local identity

Windows clients verify the server process's Windows-account SID against their
own account before sending the IPC token. This covers the IPv4 loopback
transport used by the installer and fails closed if identity inspection fails.
The account is the trust boundary, not individual processes within it. Other
processes running as that same account can already read its token and data.
Use private Unix sockets on Linux/macOS; an explicitly configured TCP socket
there does not provide this Windows OS-identity check. IPC is not a secure
remote-network protocol and must never be exposed outside loopback.

## Data and sending safety

Outgoing requests are committed with a unique local request ID before radio
handoff. Storage failure at this stage means nothing was sent. A crash, radio
exception or failure after handoff leaves an explicitly uncertain outcome;
MeshPi never automatically retries it. Check history before manually sending
again. An internal request ID is not an end-to-end idempotency guarantee:
radio delivery and SQLite cannot be committed atomically.

An older daemon refuses a newer database schema before migration or retention
work. Switching the application version does not roll a database schema back.
Keep the newer version or obtain an explicitly approved recovery plan using a
pre-upgrade export/backup; never edit `user_version` to bypass the guard. Version
0.9.2 does not change the database schema. Linux uninstallation keeps data by
default, including session data nested inside the installation directory.

## Website and dependencies

HSTS on `venes.org` is intentionally restricted to the host itself. Do not add
`includeSubDomains` or request preload until every current and future subdomain
has been reviewed and always works over HTTPS. An incorrect expansion can make
other services under the domain unavailable for a long time.

All runtime dependencies are version- and hash-locked per platform.
`meshtastic` declares `bleak` as a direct runtime requirement even when MeshPi
uses only TCP or serial. It therefore cannot safely be removed from the lock
files without an upstream change or a maintained fork. New releases must check
the actually installed environments on Windows, Linux, and macOS for known
vulnerabilities.

## Supported versions

Security fixes are normally delivered in the latest published MeshPi version.
Upgrade before reporting a problem that may already be fixed in a newer
release.
