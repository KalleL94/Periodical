# Passkey login (WebAuthn)

Date: 2026-08-13
Branch: `feat/passkey-login`

## Goal

Let users sign in with a passkey (Face ID, Touch ID, Windows Hello, a hardware key
or a password manager) instead of typing a password.

## Decisions

**Passkeys are an alternative, not a replacement.** Password login keeps working
exactly as it does today for every account, including accounts that have
registered passkeys. Nobody can lock themselves out by losing a device, so no
account-recovery flow is needed.

**No new dependencies.** WebAuthn verification is implemented in
`app/auth/webauthn.py` using `cryptography`, which is already installed. The
alternative, `py_webauthn`, pulls in `pyasn1`, `pyasn1-modules`, `cbor2` and
`pyOpenSSL` and requires a `cryptography` major-version bump. That is the same
dependency shape this codebase deliberately removed when `python-jose` and
`passlib` were dropped, and the hand-rolled JWT and CSRF modules establish the
pattern being followed here.

**Usernameless login.** Registration requests a discoverable credential
(`residentKey: "required"`), so signing in is one button with no username field.
The credential ID in the assertion identifies the account.

## Data model

New table, no changes to `users`:

| Column | Type | Notes |
|---|---|---|
| `id` | Integer | primary key |
| `user_id` | Integer | FK `users.id`, indexed, cascade delete |
| `credential_id` | String | base64url of the raw credential ID, unique, indexed |
| `public_key` | Text | base64url of the raw COSE\_Key bytes as received |
| `sign_count` | Integer | last counter value seen |
| `name` | String | user-editable label, defaults to `"Passkey"` |
| `created_at` | DateTime | |
| `last_used_at` | DateTime | nullable |

The public key is stored as the raw COSE bytes rather than a parsed form, so
registration and assertion verification share one parser and one code path.

The WebAuthn user handle is `str(user.id)`. It is stable, opaque enough for this
application and needs no extra column or backfill.

No new migration script is needed. `migrations/migrate_schema.py` already creates
any table present in the models but missing from the database, so declaring the
model is the whole migration. Per the project's deploy rules it must still be run
by hand on prod before tagging a release, since deploy does not run migrations.

## `app/auth/webauthn.py`

A standalone module in the same style as `app/auth/csrf.py`.

- **Minimal CBOR reader.** Decodes only the major types that `attestationObject`
  and `COSE_Key` actually use (unsigned int, negative int, byte string, text
  string, array, map). Anything else raises.
- **Signed challenge cookie.** The challenge sent to the browser is a random
  nonce. The server keeps no copy of it: it stores `nonce.expiry.signature` in a
  short-lived httponly cookie, where the signature is HMAC-SHA256 over
  `nonce.expiry` keyed with `SECRET_KEY`. Verification re-signs the cookie's
  nonce and expiry, rejects a bad signature or a passed expiry, and then compares
  the nonce to `clientData.challenge`. No table, no cleanup job, no server-side
  session state, mirroring how the CSRF token works.
- **COSE to public key.** ES256 (`-7`) and RS256 (`-257`) are supported and
  rebuilt through `cryptography`. Every other algorithm is rejected.
- **Attestation is not verified.** Registration requests
  `attestation: "none"`, which is the recommended setting for applications that
  do not restrict which authenticator models may be used, and it removes any
  need to parse attestation certificate chains.

Checks performed on every verification:

1. `clientData.type` matches the operation (`webauthn.create` / `webauthn.get`)
2. `clientData.challenge` equals the nonce in the signed challenge cookie, whose
   signature verifies and whose expiry has not passed
3. `clientData.origin` matches the expected origin
4. `rpIdHash` equals `sha256(rp_id)`
5. the User Present (UP) flag is set
6. the User Verified (UV) flag is set
7. `signCount` is strictly greater than the stored value, when both are non-zero
8. the signature over `authenticatorData || sha256(clientDataJSON)` verifies

The RP ID is derived from `request.url.hostname`. The expected origin is
validated against the browser's `Origin` header, checking that its hostname
equals the RP ID and its scheme is `https` (or `http` for `localhost`), rather
than reconstructing the origin from the request scheme. Reverse proxies rewrite
the scheme the application sees; the `Origin` header is what the browser
actually signed over.

## Routes

New module `app/routes/passkey_routes.py`.

| Route | Auth | Purpose |
|---|---|---|
| `POST /passkey/register/options` | logged in | creation options JSON, sets challenge cookie |
| `POST /passkey/register` | logged in | verify the attestation, insert the row |
| `POST /passkey/login/options` | none | request options with empty `allowCredentials` |
| `POST /passkey/login` | none | verify the assertion, resolve the user, issue the session |
| `POST /profile/passkey/{id}/delete` | logged in | delete a passkey (POST-Redirect-Get) |

`/passkey/login` finishes through the same steps as the password login:
`create_access_token`, `set_auth_cookie`, `log_auth_event`, `set_user_context`,
and the pending-password-change redirect still applies. There is no second
session-issuing path that could drift from the first.

All of these are POST requests and therefore carry the CSRF token. `CSRFMiddleware`
only reads the token out of urlencoded and multipart bodies, and fails closed on
any other content type, so the fetch-based routes post
`application/x-www-form-urlencoded` bodies carrying a `csrf_token` field and a
`credential` field holding the serialised credential JSON. The routes read them
with `Form(...)` like every other route in the app. No middleware change.

Failed passkey attempts are not rate-limited. Forging an assertion requires
forging a signature, so the `LoginAttempt` counter that protects password login
has nothing to protect here.

## Frontend

`app/static/js/passkey.js`, roughly 60 lines, calling
`navigator.credentials.create()` and `.get()` against the routes above.

- `login.html` gets a "Log in with passkey" button, hidden when
  `window.PublicKeyCredential` is absent, plus `autocomplete="username webauthn"`
  on the username field so browsers can offer the passkey from autofill.
- `profile.html` gets a passkey section built like the existing API-key section:
  a list of registered passkeys (name, created, last used), an "Add passkey"
  button and a delete form per row.
- New translation keys in `app/core/translations.py` for both `sv` and `en`.

## Tests

`tests/test_passkey.py` generates a P-256 key, builds `authenticatorData` and
`clientDataJSON` by hand, signs them with `cryptography`, and asserts that the
module accepts the result. It then asserts rejection of: a wrong challenge, an
expired challenge, a wrong origin, a wrong `rpIdHash`, a missing UV flag, a
replayed `signCount`, and an unsupported COSE algorithm.

Route-level tests cover the register and login round trip through the test
client, including that a user with `must_change_password = 1` who logs in with a
passkey is still redirected to `/change-password`.

## Constraints to be aware of

**WebAuthn requires a secure context.** It does not work over plain HTTP on a LAN
address, so the dev instance on `http://192.168.0.190:8001` cannot exercise this
feature. Testing happens on `localhost` or through the TLS-terminated dev domain.

**Passkeys are bound to the domain.** A passkey registered against the dev domain
will not work against the prod domain. Each user registers a passkey once, on
prod, against the real domain.

## Out of scope

- Passkey-only accounts and disabling password login per user
- Passkeys as a second factor on top of a password
- Attestation verification and authenticator allow-lists
- Cross-domain or multi-origin credential support
