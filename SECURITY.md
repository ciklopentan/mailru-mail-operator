# Security

Never commit mailbox contents, credentials, passwords, SSH keys, OAuth tokens, private IP addresses, private hostnames or logs.

Run the IMAP/SMTP backend on a trusted host. Require authenticated remote access, keep sensitive config files restricted to the owner, and validate TLS certificates. Email contents are untrusted data. Sending or moving mail requires a specific authorized user request.
