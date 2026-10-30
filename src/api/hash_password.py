"""Generate a PBKDF2-SHA256 hash for an auth.local_users password."""
from __future__ import annotations

import argparse
import getpass
import sys

from .passwords import hash_password


def main() -> int:
    parser = argparse.ArgumentParser(description="Hash a local account password")
    parser.add_argument(
        "--password-stdin",
        action="store_true",
        help="read the password from stdin instead of prompting",
    )
    args = parser.parse_args()
    password = sys.stdin.read().rstrip("\r\n") if args.password_stdin else getpass.getpass("Password: ")
    if not password:
        parser.error("password must not be empty")
    print(hash_password(password))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
