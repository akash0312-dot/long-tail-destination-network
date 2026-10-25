# Deploying the live demo

**Live:** https://16-178-103-126.sslip.io/docs, on AWS's Free plan (EC2 `t3.small`, Mumbai), deployed 2026-10-25.
Cold start 118 s from nothing, Let's Encrypt certificate on the first try, ~1.1 GB of RAM in use; 22/22 external
checks and 14/14 scenarios ([evidence](../test_pack/batch_2026-10-25_aws_live_demo/external_checks.md)). An hourly
GitHub Actions check ([uptime.yml](../.github/workflows/uptime.yml)) emails the owner if it stops answering.

The demo is the same `docker compose` stack plus Caddy for HTTPS. Only ports 80 and 443 are public:

| Path | Access |
|---|---|
| `/docs`, `/openapi.json`, `/health`, `/ready` | public (`/docs` has an **Authorize** button for the token) |
| `/api/v1/*`, `/connector/v1/*` | API token required, rate limited (`RATE_LIMIT_PER_MINUTE`, default 120 per token) |
| `/api/v1/feeds/*.xml` | public (MRSS feeds are pulled by aggregators) |
| `/mock/*` | the mock platforms (fault injection for the batch scenarios), same API token required |
| object storage, Ghost, Lemmy, Postgres | never exposed; download links go through the token-protected API |

## 1. A server

Any Ubuntu VM with 2 vCPU and at least 2 GB of RAM (the stack uses ~1.1 GB; [server_setup.sh](server_setup.sh) adds
2 GB of swap on small servers), about 20 GB of disk, and inbound **22, 80, 443** open. Docker's port rules bypass
`ufw`, so the compose files bind every other port to `127.0.0.1`: nothing but Caddy listens on a public address.

**How the live demo was set up (AWS Free plan, a debit card is enough; no charge unless upgraded to a Paid plan):**

1. Sign up at aws.amazon.com, choose the **Free plan**, add the card, verify the phone number, choose **Basic**
   support. Never choose "Upgrade to Paid plan". Optional: **Billing → Budgets → Zero spend budget**.
2. Region **Asia Pacific (Mumbai)**. **EC2 → Key pairs → Import key pair:** paste the deploy public key.
3. **EC2 → Launch instance:** Ubuntu Server LTS (64-bit x86), type **t3.small** (Free-plan eligible; ~$22 a month of
   credits with its IP and disk), that key pair, allow SSH/HTTP/HTTPS, storage **30 GiB gp3**, Advanced details →
   **Credit specification: Standard**.
4. **EC2 → Elastic IPs → Allocate → Associate** with the instance, so the address and hostname never change.

**Hostname:** `<IP-with-dashes>.sslip.io` needs no sign-up, and Caddy gets the certificate on first start. sslip.io is
not on the Public Suffix List, so its users share one Let's Encrypt rate limit; if `docker compose logs caddy` shows a
rate-limit error, use a free DuckDNS name (duckdns.org is on the list), set `DEMO_DOMAIN` to it and restart Caddy.

## 2. Set up the server and copy the code

From the project folder on your own computer (`<ip>` is the server's address, `ubuntu` its user):

```bash
scp deploy/server_setup.sh ubuntu@<ip>:
ssh ubuntu@<ip> 'bash server_setup.sh'          # Docker + Compose, log rotation, swap, Compose version check
git archive HEAD | ssh ubuntu@<ip> 'mkdir -p ~/longtail && tar -x -C ~/longtail'   # the code; no GitHub access needed
```

## 3. Settings

Create the server's `.env` from `.env.example` (keep a copy somewhere git ignores, never in the repository) with:

```ini
API_TOKEN=<openssl rand -hex 32: the token the reviewers get>
DEMO_DOMAIN=<the hostname, e.g. 16-178-103-126.sslip.io>
PUBLIC_BASE_URL=https://<the hostname>
CALLBACK_SIGNATURE_HEADER=<the callback signature header name the brief specifies>
RATE_LIMIT_PER_MINUTE=120
```

and replace the local-only passwords (`POSTGRES_PASSWORD`, `LEMMY_ADMIN_PASSWORD`, `LEMMY_DB_PASSWORD`,
`CALLBACK_SIGNING_SECRET`, `MOCK_API_KEY`, `PARTNER_WEBHOOK_SECRET`) with random values (`openssl rand -hex 16`).
Starting without `API_TOKEN`, `DEMO_DOMAIN` or `CALLBACK_SIGNATURE_HEADER` is refused with a message naming the setting.
Leave `ALLOW_INTERNAL_HOSTS=true`: the demo's mock platforms, Ghost and Lemmy are internal hosts. Cloud metadata
addresses (169.254.169.254 and friends) are refused regardless. Copy it up with `scp`, then `chmod 600 ~/longtail/.env`.

## 4. Start

```bash
cd ~/longtail
docker compose -f docker-compose.yml -f deploy/docker-compose.demo.yml up -d --build
docker compose -f docker-compose.yml -f deploy/docker-compose.demo.yml wait bootstrap   # ~2 min on first boot
```

The bootstrap provisions the local Ghost and Lemmy, seeds one destination per connector and certifies all of them.
Everything restarts by itself after a reboot (`restart: unless-stopped`).

## 5. Check it from your own computer

```bash
curl https://<host>/health                                                   # 200
curl -H "Authorization: Bearer <token>" https://<host>/api/v1/connectors     # 200 (401 without the token)
python -m app.batch --api https://<host> --mock-host https://<host>/mock --token <token> \
  --input test_inputs --output test_pack/live_demo_run                       # every scenario should pass
```

## 6. What to put in the Submission Form

- Live demo URL: `https://<host>/docs`
- Access token: the `API_TOKEN` value (never in the repo or a screenshot)
- Cold-start time: none, it runs continuously; 118 s after a full rebuild
- Rate limit: 120 requests per minute per token

## Updating

Copy the new code (step 2, `git archive` line) and run step 4 again. To change one setting, edit `~/longtail/.env` and
restart the services that read it, for example `... up -d --no-deps api worker`.

## Troubleshooting

- `docker compose ... logs caddy`: certificate problems (the hostname must resolve to this server; ports 80/443 open).
- `docker compose ... logs bootstrap`: provisioning or certification failures.
- `curl https://<host>/ready`: shows which dependency is not ready.
