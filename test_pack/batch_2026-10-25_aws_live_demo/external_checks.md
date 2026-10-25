# External checks of the live demo

Run 2026-10-25T05:47:38Z from outside AWS against https://16-178-103-126.sslip.io (AWS Free plan, t3.small, Mumbai). The token is read from a git-ignored file and never printed. Script: the checks below, one curl or TCP connection each.

```
PASS  GET /health (public) (200)
PASS  GET /ready (public) (200)
PASS  GET /docs (public) (200)
PASS  GET /openapi.json (public) (200)
PASS  API without token (401)
PASS  API with a wrong token (401)
PASS  API with the token (200)
PASS  /mock without token (401)
PASS  /mock with the token (200)
PASS  HTTP redirects to HTTPS (308)
PASS  MRSS feed without token (public) (200)
PASS  MRSS feed is XML (yes)
PASS  HSTS header (yes)
PASS  nosniff header (yes)
PASS  Server header removed (yes)
PASS  Publicly trusted certificate (yes)
      certificate: issuer=C=US, O=Let's Encrypt, CN=YE2
PASS  port 8000 closed from outside (closed)
PASS  port 9000 closed from outside (closed)
PASS  port 2368 closed from outside (closed)
PASS  port 8536 closed from outside (closed)
PASS  port 8333 closed from outside (closed)
PASS  port 5432 closed from outside (closed)
---- 22 passed, 0 failed
```
