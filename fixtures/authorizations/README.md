# Authorization fixtures

Key material is generated, never checked in.

* `dev_keys.json` (git-ignored) is created on first start by `KeyRing.load_or_create`
  at `DEV_KEYS_PATH`. It holds P-256 private keys for the roles below and is
  labelled `DEVELOPMENT KEYS ONLY`. Tests generate fresh keys in memory.
* Each process only signs with the roles it legitimately plays:

| Role | Signs | Held by |
|---|---|---|
| `agent` | closed AP2 mandates (KB-JWT), VI L3a/L3b | worker (agent runtime) |
| `agent_provider` | AP2 delegate SD-JWT (Trusted Agent Provider / Trusted Surface) | API (`WatchService.activate`), never the model |
| `vi_issuer` | VI Layer 1 | API (credential-provider fixture) |
| `user_device` | VI Layer 2 (user consent) | API (trusted surface fixture) |
| `credential_provider` | payment credentials and Payment Receipts | `CredentialProviderSim` |
| `merchant_a`, `merchant_b` | merchant-signed `checkout_jwt`, Checkout Receipts, attestations | the respective merchant fixture |

Verifiers use the **trust store** (public JWKs by role and `kid`) derived from the
same file: merchants trust `agent_provider`, `vi_issuer` and `credential_provider`;
the platform trusts each merchant's `keys[]` published in its UCP business profile.

Consent artifacts produced from these keys are stored encrypted in
`protocol_artifacts` (see `persistence/artifacts.py`); the per-profile test
fixtures live in `tests/protocol/test_profiles.py`.
