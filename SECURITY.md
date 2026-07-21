# Security / 安全说明

- Do not commit `.env`, `config/grok/config.json`, authentication files, mailbox data, generated accounts, or proxy credentials.
- Use private repository visibility if deployment configuration may contain sensitive business metadata.
- Rotate a secret immediately if it is ever pasted into an issue, commit, image layer, log, or CI output.
- Restrict the gateway with your own reverse-proxy authentication and network policy before exposing it to the public internet.
