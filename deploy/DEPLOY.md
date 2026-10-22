# Deploying the live demo

The demo is the same `docker compose` stack plus Caddy for HTTPS. Only ports 80 and 443 are public:

| Path | Access |
|---|---|
| `/docs`, `/openapi.json`, `/health`, `/ready` | public (`/docs` has an **Authorize** button for the token) |
| `/api/v1/*`, `/connector/v1/*` | API token required, rate limited (`RATE_LIMIT_PER_MINUTE`, default 120 per token) |
| `/api/v1/feeds/*.xml` | public (MRSS feeds are pulled by aggregators) |
| `/mock/*` | the mock platforms (fault injection for the batch scenarios), same API token required |
| object storage, Ghost, Lemmy, Postgres | never exposed; download links go through the token-protected API |

Rehearsed locally on 2026-10-23 with `DEMO_DOMAIN=localhost`: every boundary above behaved as listed, and all 14 batch
scenarios passed through HTTPS + token + `/mock` ([evidence](../test_pack/batch_2026-10-23_demo_rehearsal_https/summary.md)).

## 1. A server

Any Linux VM with **at least 4 GB RAM and 2 vCPU** (Ghost + Lemmy + Postgres ×2 + the service), Ubuntu 24.04, ~20 GB
disk. For example Hetzner CX22, DigitalOcean 4 GB, AWS Lightsail 4 GB, or Oracle Cloud's free Ampere (ARM) tier.
Firewall: allow inbound **22, 80, 443** only. (Docker's port rules bypass `ufw`, so the compose files also bind every
other port to `127.0.0.1`; nothing but Caddy listens on a public address.)

## 2. A hostname

Either point a DNS `A` record (for example `demo.yourdomain.com`) at the server's IP, or use a free wildcard DNS name
that needs no setup: `<IP-with-dashes>.sslip.io`, for example `203-0-113-10.sslip.io`. Caddy gets the HTTPS
certificate automatically on first start.

## 3. Install Docker and get the code

```bash
# Docker Engine + compose plugin: follow https://docs.docker.com/engine/install/ubuntu/
sudo usermod -aG docker $USER && newgrp docker
docker compose version   # needs v2.24 or newer
```

The repository is private. Give the server read-only access with a **deploy key** (GitHub → repo → Settings → Deploy
keys → Add, read-only):

```bash
ssh-keygen -t ed25519 -f ~/.ssh/demo_deploy -N ""
cat ~/.ssh/demo_deploy.pub          # paste this into the deploy key form
GIT_SSH_COMMAND="ssh -i ~/.ssh/demo_deploy" git clone git@github.com:akash0312-dot/long-tail-destination-network.git
cd long-tail-destination-network
```

## 4. Settings

```bash
cp .env.example .env
openssl rand -hex 32                 # the API token you will give the reviewers
nano .env
```

Set at least:

```ini
API_TOKEN=<the value from openssl>
DEMO_DOMAIN=<your hostname, e.g. 203-0-113-10.sslip.io>
CALLBACK_SIGNATURE_HEADER=<the callback signature header name the brief specifies>
RATE_LIMIT_PER_MINUTE=120
```

and replace the local-only passwords (`POSTGRES_PASSWORD`, `LEMMY_ADMIN_PASSWORD`, `LEMMY_DB_PASSWORD`,
`CALLBACK_SIGNING_SECRET`, `MOCK_API_KEY`, `PARTNER_WEBHOOK_SECRET`) with random values (`openssl rand -hex 16`).
Starting without `API_TOKEN`, `DEMO_DOMAIN` or `CALLBACK_SIGNATURE_HEADER` is refused with a message naming the setting.
Leave `ALLOW_INTERNAL_HOSTS=true`: the demo's mock platforms, Ghost and Lemmy are internal hosts. Cloud metadata
addresses (169.254.169.254 and friends) are refused regardless.

## 5. Start

```bash
docker compose -f docker-compose.yml -f deploy/docker-compose.demo.yml up -d --build
docker compose -f docker-compose.yml -f deploy/docker-compose.demo.yml wait bootstrap   # ~2 min on first boot
curl https://$DEMO_DOMAIN/ready
```

The bootstrap provisions the local Ghost and Lemmy, seeds one destination per connector and certifies all of them.
Everything restarts by itself after a reboot (`restart: unless-stopped`).

## 6. Check it from your own computer

```bash
curl https://<host>/health                                                   # 200
curl -H "Authorization: Bearer <token>" https://<host>/api/v1/connectors     # 200 (401 without the token)
python -m app.batch --api https://<host> --mock-host https://<host>/mock --token <token> \
  --input test_inputs --output test_pack/live_demo_run                       # every scenario should pass
```

Then add a free uptime monitor (for example UptimeRobot) on `https://<host>/health` so you hear if it goes down
during the review period.

## 7. What to put in the Submission Form

- Live demo URL: `https://<host>/docs`
- Access token: the `API_TOKEN` value (in the form only; never in the repo or a screenshot)
- Cold-start time: none, it runs continuously; the first boot after a full rebuild takes about 2 minutes
- Rate limit: 120 requests per minute per token

## Updating

```bash
git pull
docker compose -f docker-compose.yml -f deploy/docker-compose.demo.yml up -d --build
```

## Troubleshooting

- `docker compose ... logs caddy`: certificate problems (the hostname must resolve to this server; ports 80/443 open).
- `docker compose ... logs bootstrap`: provisioning or certification failures.
- `curl https://<host>/ready`: shows which dependency is not ready.
