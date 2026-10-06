# Security policy

## Supported versions

| Version | Supported |
| --- | --- |
| 0.2.x | Yes |
| Earlier releases | No |

## Reporting a vulnerability

Please do not open a public issue for a suspected vulnerability. Report it using
[GitHub private vulnerability reporting](https://github.com/Hybrid3D/chronon-vcs/security/advisories/new).

Include the affected version, reproduction steps, impact, and any suggested
mitigation. We will acknowledge a report within seven days and coordinate a fix
and disclosure timeline with you.

Chronon stores snapshots as plaintext on the local filesystem. Do not use it as
a secret manager or rely on it to encrypt sensitive material.
