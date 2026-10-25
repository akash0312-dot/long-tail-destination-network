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

### AWS Free plan (a debit card is enough)

AWS's Free plan (accounts created after 2025-07-15) gives up to $200 of credits for up to 6 months; AWS states you are
not charged unless you upgrade to a Paid plan, and the account closes when the plan ends. Only these EC2 types are
allowed: `t3.micro`, `t3.small`, `t4g.micro`, `t4g.small`, `c7i-flex.large`, `m7i-flex.large`. Use **`t3.small`**
(2 vCPU, 2 GB; about $0.022 an hour in Mumbai, about $16 a month, plus about $3.65 for the public IPv4 and about $2.50
for a 30 GB disk: roughly $22 a month of credits). `server_setup.sh` adds 2 GB of swap on servers this size. The larger
`c7i-flex`/`m7i-flex` types would use the credits about three times faster.

1. Sign up at aws.amazon.com, choose the **Free plan**, add the debit card (a small verification charge is
   refunded), verify the phone number, choose the **Basic** (free) support plan. Never choose "Upgrade to Paid plan".
2. Console, region **Asia Pacific (Mumbai)**. **EC2 → Key pairs → Actions → Import key pair:** name `longtail-demo`,
   paste the deploy public key.
3. **EC2 → Launch instance:** image **Ubuntu Server 24.04 LTS (64-bit x86)**; type **t3.small**; key pair
   `longtail-demo`; network: allow SSH, **HTTP** and **HTTPS** from the internet; storage **30 GiB gp3**; Advanced
   details → **Credit specification: Standard** (so CPU bursts never use extra credits).
4. **EC2 → Elastic IPs → Allocate → Associate** with the instance, so the address (and the hostname built from it)
   never changes.
5. Optional guardrail: **Billing → Budgets → Create budget → Zero spend budget** (an email if anything is ever charged).
6. Log in as `ubuntu` and run [server_setup.sh](server_setup.sh).

Deployed this way on 2026-10-25 (Ubuntu 26.04, t3.small, Mumbai): https://16-178-103-126.sslip.io. Cold start 118 s, Let's Encrypt certificate on the first try, ~1.1 GB of RAM in use; 22/22 external checks and 14/14 scenarios ([evidence](../test_pack/batch_2026-10-25_aws_live_demo/external_checks.md)).

### Azure for Students (no credit card)

For full-time university students: sign in with the university email, no card. The offer is $100 of credit for 12
months; when it runs out, the resources stop and nothing is charged. Prices below are from Microsoft's retail price
list for Central India (2026-10-25):

| Item | Price | Per month |
|---|---|---|
| VM `Standard_B2s` (2 vCPU, 4 GB) | $0.0448 / hour | ~$32.70 |
| OS disk, Standard SSD 32 GB (E4) | $2.64 / month | ~$2.64 |
| Standard static public IPv4 | $0.005 / hour | ~$3.65 |
| **Total** | | **~$39, so the credit lasts about 2.5 months** |

A cheaper fallback is `Standard_B1ms` (1 vCPU, 2 GB, ~$16 / month, ~$23 in total): `server_setup.sh` adds 2 GB of swap
on servers under 3.5 GB.

1. Sign up at azure.microsoft.com/free/students with the university email. The credit balance is shown in the Azure
   Education Hub.
2. **Virtual machines → Create → Azure virtual machine:**
   - Region: one near you (for example Central India). If the student subscription refuses it, choose a region from
     the list the error message gives.
   - Availability: no redundancy. **Security type: Standard.** **Azure Spot: off.**
   - Image: **Ubuntu Server 24.04 LTS – x64 Gen2**. Size: **Standard_B2s**.
   - Authentication: **SSH public key**, username `azureuser`, **Use existing public key**, paste the deploy public key.
   - Inbound ports: **SSH (22), HTTP (80), HTTPS (443)**.
   - Disks: OS disk type **Standard SSD**, default size.
   - Management: **Auto-shutdown off** (otherwise the demo stops every night).
   - Review + create.
3. Copy the VM's **public IP address**. Hostname: `<IP-with-dashes>.sslip.io` (or a DNS name label on the public IP,
   `<label>.<region>.cloudapp.azure.com`; neither is on the Public Suffix List, so see the certificate note below).
4. On the server, run [server_setup.sh](server_setup.sh).

### Oracle Cloud Always Free

Checked on 2026-10-25 against Oracle's [Always Free resources](https://docs.oracle.com/en-us/iaas/Content/FreeTier/resourceref.htm)
page. Every image used here has an ARM64 build (`docker manifest inspect`), and every Python dependency has a
ready-built Linux ARM64 package (`pip download --platform manylinux_2_17_aarch64 ...`), so nothing compiles on the
server. The stack uses about 0.75 GB of memory when idle (`docker stats`).

| Resource | Always Free allowance | This deployment |
|---|---|---|
| Ampere A1 compute (home region only) | 2 OCPU and 12 GB in total (1,500 OCPU-hours, 9,000 GB-hours a month) | **1 instance, VM.Standard.A1.Flex, 2 OCPU, 4 GB** |
| Boot + block volumes | 200 GB in total | the default boot volume (about 47–50 GB), no extra volumes |
| Outbound data | 10 TB a month | a few GB |
| Networking (VCN, public subnet, internet gateway, security list, ephemeral public IP) | free | one of each |
| Load balancer, object storage, DNS | — | **not used** (Caddy, SeaweedFS and sslip.io/DuckDNS run instead) |

**Why 4 GB and not the full 12 GB:** Oracle may stop an Always Free instance it considers idle, meaning CPU, network
*and* memory all below 20% over 7 days (memory counts for A1 only). A demo server is idle most of the time; at about
1–1.2 GB used of 4 GB, memory stays above 20%, so the instance does not meet the idle rule. A stopped instance is not
deleted and can be started again, and everything restarts with it.

Steps:

1. Sign up at cloud.oracle.com for the **Free Tier**. Oracle asks for a card to verify identity; Always Free resources
   are not charged. Choose the home region carefully: it cannot be changed, and Always Free compute only runs there.
   **Never choose "Upgrade to Pay As You Go".** Optional guardrail: Billing → Budgets → a budget of $1 with an email
   alert.
2. **Compute → Instances → Create instance:** image **Canonical Ubuntu 24.04**; shape **Ampere → VM.Standard.A1.Flex**,
   **2 OCPU, 4 GB** (check the "Always Free-eligible" label); networking: a new VCN with a public subnet and **assign a
   public IPv4 address**; SSH keys: paste the deploy public key; boot volume: leave the default. If Oracle reports
   "out of capacity", try another availability domain or try again later.
3. **Open the ports:** the instance's subnet → its **security list** → Add ingress rules: source `0.0.0.0/0`,
   TCP, destination ports `80` and `443`.
4. On the server, run [server_setup.sh](server_setup.sh): it installs Docker, opens 80/443 in the server's own
   iptables (Oracle's Ubuntu images close every port except 22 by default, so a security-list rule alone is not
   enough), and turns on log rotation so the disk cannot fill up during the review.

**Hostname and certificate:** `<IP-with-dashes>.sslip.io` needs no sign-up, but sslip.io is not on the Public Suffix
List, so every sslip.io user shares one Let's Encrypt rate-limit bucket. If `docker compose logs caddy` shows a rate
limit error, use a free DuckDNS name instead (duckdns.org is on the list, so it gets its own limit): create it at
duckdns.org pointing at the server's IP, set `DEMO_DOMAIN` to it, and restart Caddy.

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
