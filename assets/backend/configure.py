#!/usr/bin/env python3
"""Set Mail.ru app credentials interactively; validate IMAP before replacing config."""
import getpass
import imaplib
import json
import os
import re
import ssl
import tempfile
from pathlib import Path

CREDENTIALS = Path("/etc/mailru-agent/credentials.json")

def authenticate(address: str, password: str) -> bool:
    """Verify IMAP credentials without exposing server response or password."""
    conn = None
    try:
        conn = imaplib.IMAP4_SSL("imap.mail.ru", 993,
                                 ssl_context=ssl.create_default_context(), timeout=20)
        conn.login(address, password)
        return True
    except (imaplib.IMAP4.error, OSError, TimeoutError, ssl.SSLError):
        return False
    finally:
        if conn is not None:
            try:
                conn.logout()
            except Exception:
                pass

def save_credentials(address: str, password: str):
    """Atomically replace 0600 credentials; preserve previous on failure."""
    CREDENTIALS.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(prefix=".credentials-", dir=str(CREDENTIALS.parent))
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"address": address, "app_password": password}, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, CREDENTIALS)
        os.chmod(CREDENTIALS, 0o600)
    finally:
        if tmp and os.path.exists(tmp):
            os.unlink(tmp)

def main():
    if os.geteuid() != 0:
        raise SystemExit("Run on your own VPS as root")
    print("Mail.ru secure setup: validation BEFORE saving; current file preserved if rejected.")
    addr = input("Full Mail.ru email address: ").strip()
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", addr):
        raise SystemExit("Invalid email address; configuration unchanged")
    pwd = getpass.getpass("Mail.ru NEW app-specific password (hidden): ").strip()
    if not pwd:
        raise SystemExit("Empty app password; configuration unchanged")
    print("Validating IMAP login with Mail.ru...")
    if not authenticate(addr, pwd):
        raise SystemExit("IMAP login rejected or unavailable. Configuration unchanged. Check address, application password and Mail.ru IMAP access.")
    print("IMAP login accepted; saving credentials...")
    save_credentials(addr, pwd)
    print("VALIDATED_AND_SAVED: credentials are stored privately, no password displayed.")

if __name__ == "__main__":
    main()
