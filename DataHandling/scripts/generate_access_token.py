#!/usr/bin/env python3
"""Generate a short-lived access token for trusted non-production tooling."""

from __future__ import annotations

import argparse
from app.config import AUTH_AUDIENCE, AUTH_ISSUER, AUTH_SIGNING_SECRET
from app.security.access_tokens import create_access_token


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--subject", required=True)
    parser.add_argument("--role", choices=("patient", "clinician", "admin"), required=True)
    parser.add_argument("--scope", action="append", required=True)
    parser.add_argument("--patient", action="append", default=[])
    parser.add_argument("--lifetime-seconds", type=int, default=3600)
    args = parser.parse_args()

    token = create_access_token(
        subject=args.subject,
        role=args.role,
        scopes=args.scope,
        patient_ids=args.patient,
        lifetime_seconds=args.lifetime_seconds,
        secret=AUTH_SIGNING_SECRET,
        issuer=AUTH_ISSUER,
        audience=AUTH_AUDIENCE,
    )
    print(token)


if __name__ == "__main__":
    main()
