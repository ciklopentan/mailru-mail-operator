# Mail.ru Mail Operator (v0.2.0)

A self-hosted ChatGPT skill and Python IMAP/SMTP mail operator for Mail.ru.

**Not a standalone MCP connector.** An authenticated remote execution connection is required to use this backend from ChatGPT. This repository does not include a running server, credentials or access to anyone's mailbox.

## Contents

- `plugin.json`, `.codex-plugin/plugin.json`: plugin metadata
- `skills/mailru-mail/SKILL.md`: ChatGPT instructions
- `assets/backend/mailru.py`: Python IMAP/SMTP command-line backend
- `assets/backend/configure.py`: interactive Mail.ru app-password setup
- `assets/tests/`: fixture-based unit tests

## Installation

Install the Python backend in a trusted directory, for example `/opt/mailru-agent`. Run `sudo python3 /opt/mailru-agent/configure.py` in a private terminal with an app-specific Mail.ru password. Use the standard Mail.ru IMAP/SMTP TLS endpoints, and protect configuration with restricted file permissions.

Check `python3 /opt/mailru-agent/mailru.py status`, then `python3 /opt/mailru-agent/mailru.py folders`. A true configured flag does not prove online authentication. The CLI supports searching, reading, drafts, marking, moving, sending, and attachment staging; state-changing operations require user intent.

## Tests

From the project root, run `python3 -m unittest discover -s assets/tests -v`. Tests use synthetic data and do not access a mailbox.

## Security and licensing

Never commit private account, server or SSH data. See SECURITY.md. Copyright remains with its owners; no open-source license is granted by publishing this repository. This is an independent project and is not an official Mail.ru product.
