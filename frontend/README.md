# SecureCare Health Frontend

Static enterprise web application and clinical portal (zero runtime dependencies, ES modules, strict memory-only Cognito PKCE tokens).

## Files

- `index.html`, `about.html`, `privacy.html`, `security.html`, `terms.html`, `contact.html`: public pre-login clinic website.
- `portal.html`: authenticated clinical SPA implementing all endpoints in `securecare-apigateway-v4.json`.
- `js/config.js`: Cognito domain, App Client ID (`265sf66rn74fpel2s798v26nd4`), scopes (`email openid phone`), and API base URL.
- `js/auth.js`: Cognito Hosted UI Authorization Code + PKCE implementation keeping `id_token` strictly in JS memory.
- `js/api.js`: API client and schema validators matching API Gateway request models.

## Local Testing

Serve the `frontend/` directory on port `8080` so the origin matches the registered Cognito callback (`http://localhost:8080/portal.html`) and sign-out (`http://localhost:8080/index.html`) URLs:

```bash
cd PayLogistics/frontend
python -m http.server 8080
```

Open `http://localhost:8080/index.html` in your browser.
