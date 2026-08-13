// app/static/js/passkey.js
// WebAuthn glue. The server speaks urlencoded form bodies, not JSON, because
// CSRFMiddleware reads its token out of form bodies and rejects anything else.

(function () {
    "use strict";

    // Browsers hand out ArrayBuffers and expect ArrayBuffers, while the server
    // speaks base64url. These two conversions are the whole impedance mismatch.
    function fromBase64Url(value) {
        const padded = value.replace(/-/g, "+").replace(/_/g, "/");
        const raw = atob(padded + "=".repeat((4 - (padded.length % 4)) % 4));
        return Uint8Array.from(raw, function (character) {
            return character.charCodeAt(0);
        });
    }

    function toBase64Url(buffer) {
        const bytes = new Uint8Array(buffer);
        let raw = "";
        for (let index = 0; index < bytes.length; index += 1) {
            raw += String.fromCharCode(bytes[index]);
        }
        return btoa(raw).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
    }

    function csrfToken() {
        const field = document.querySelector("input[name='csrf_token']");
        return field ? field.value : "";
    }

    async function post(url, fields) {
        const body = new URLSearchParams(Object.assign({ csrf_token: csrfToken() }, fields || {}));
        const response = await fetch(url, {
            method: "POST",
            body: body,
            credentials: "same-origin",
        });
        if (!response.ok) {
            throw new Error("Request to " + url + " failed with " + response.status);
        }
        return response.json();
    }

    async function login() {
        const options = await post("/passkey/login/options");
        const credential = await navigator.credentials.get({
            publicKey: {
                challenge: fromBase64Url(options.challenge),
                rpId: options.rpId,
                allowCredentials: [],
                userVerification: options.userVerification,
                timeout: options.timeout,
            },
        });
        if (!credential) {
            throw new Error("No credential returned");
        }

        const result = await post("/passkey/login", {
            credential: JSON.stringify({
                id: credential.id,
                response: {
                    clientDataJSON: toBase64Url(credential.response.clientDataJSON),
                    authenticatorData: toBase64Url(credential.response.authenticatorData),
                    signature: toBase64Url(credential.response.signature),
                },
            }),
        });
        window.location.assign(result.redirect);
    }

    async function register(name) {
        const options = await post("/passkey/register/options");
        const credential = await navigator.credentials.create({
            publicKey: {
                challenge: fromBase64Url(options.challenge),
                rp: options.rp,
                user: {
                    // The user handle is opaque bytes to the authenticator.
                    id: new TextEncoder().encode(options.user.id),
                    name: options.user.name,
                    displayName: options.user.displayName,
                },
                pubKeyCredParams: options.pubKeyCredParams,
                authenticatorSelection: options.authenticatorSelection,
                attestation: options.attestation,
                timeout: options.timeout,
            },
        });
        if (!credential) {
            throw new Error("No credential created");
        }

        const result = await post("/passkey/register", {
            name: name || "",
            credential: JSON.stringify({
                id: credential.id,
                response: {
                    clientDataJSON: toBase64Url(credential.response.clientDataJSON),
                    attestationObject: toBase64Url(credential.response.attestationObject),
                },
            }),
        });
        window.location.assign(result.redirect);
    }

    function reportFailure(element, error) {
        console.error(error);
        const message = element && element.dataset.errorMessage;
        if (message) {
            window.alert(message);
        }
    }

    window.periodicalPasskeyLogin = login;
    window.periodicalPasskeyRegister = register;

    document.addEventListener("DOMContentLoaded", function () {
        const supported = Boolean(window.PublicKeyCredential);

        const loginButton = document.getElementById("passkey-login");
        if (loginButton) {
            // Hidden by default in the markup, so a browser without WebAuthn
            // never sees a button that cannot work.
            loginButton.hidden = !supported;
            loginButton.addEventListener("click", function () {
                login().catch(function (error) {
                    reportFailure(loginButton, error);
                });
            });
        }

        const registerButton = document.getElementById("passkey-register");
        if (registerButton) {
            registerButton.hidden = !supported;
            registerButton.addEventListener("click", function () {
                const field = document.getElementById("passkey-name");
                register(field ? field.value : "").catch(function (error) {
                    reportFailure(registerButton, error);
                });
            });
        }
    });
})();
