# Deploy runbook: always-on host for Portfolio Guard

> **Unverified: written from memory, run it once and correct this file.** Every command below is
> unverified. No step has been executed on a real host, and the Docker image has not been built
> (Docker was not available when this was written). Adapted from the unexecuted Compass runbooks
> (see `docs/provenance.md`). When you run a step and it works or needs a fix, edit this file.

Guard is advice-only. The host only runs the scheduler, the data jobs and the dashboard; it never
places trades.

## 1. Choose a host

A small always-on Linux VM: 1 vCPU and 1-2 GB RAM is enough (the weekly job is the heaviest).
Oracle Cloud always-free Ampere (ARM) is what Compass planned; any VPS works. Pick Ubuntu LTS.
The image is multi-arch only if the base images are, so check `docker build` on ARM once.

Network: no public ingress at all. In the provider's firewall/security list, allow nothing inbound
(Tailscale does not need an inbound port). If you want SSH from the internet while setting up, close
it again afterwards.

## 2. Install Docker (unverified)

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker "$USER"   # log out and in
docker compose version
```

Set the clock zone (the scheduler uses Europe/London and handles DST itself; the container sets
`TZ=Europe/London` too). Keep the host on UTC if you prefer; backups are named in UTC.

```bash
timedatectl            # check NTP is synchronized
```

DST note: the weekly job fires Wednesday 12:00 London time, so it moves by an hour in UTC across the
last Sunday of March and October. Check the first run after each change (see `docs/runbook.md`).

## 3. Get the code

```bash
sudo mkdir -p /srv/guard && sudo chown "$USER" /srv/guard
git clone <your repo url> /srv/guard/app && cd /srv/guard/app
```

## 4. Copy `.env` securely (never into git)

From your laptop, over Tailscale or SSH:

```bash
scp .env guard-host:/srv/guard/app/.env
ssh guard-host 'chmod 600 /srv/guard/app/.env'
```

`.env` holds the Trading212 read-only keys and the push-notification credentials (see
`.env.example`). It is git-ignored, excluded by `.dockerignore`, and reaches the containers only as
runtime environment (`env_file`). It is never copied into an image or a backup. Keep a copy in your
password manager: backups will not restore it.

Lock the Trading212 API keys to the host's IP: in Trading212 (Settings > API) restrict each key to
the host's public egress IP (`curl -s https://ifconfig.me` on the host). Keep the keys read-only
(account data, portfolio, history; no orders).

## 5. First run

```bash
docker compose build
docker compose up -d
docker compose ps
docker compose logs -f worker
docker compose exec worker python -m guard.worker status
```

The data directory is the `guard-data` named volume (`/data` in the containers). It starts empty:
the first snapshot, history build and Phase 0 backtest outputs must exist before the weekly job can
advise. Copy the MSCI input and `manual_holdings.yaml` into the volume if you use them:

```bash
docker compose cp ./msci_acwi_nr.csv worker:/data/inputs/msci_acwi_nr.csv   # root fs is read-only; /data is writable
```

(Unverified: `docker compose cp` into a running read-only-root container may need `docker run --rm -v`
instead.)

## 6. Reach the dashboard only over Tailscale or an SSH tunnel

The dashboard is published on the host as `127.0.0.1:8501` only (a unit test enforces this).

Tailscale (preferred):

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
sudo tailscale serve --bg 8501      # tailnet-only HTTPS proxy to localhost:8501
tailscale serve status
```

Do NOT use `tailscale funnel` (it publishes to the internet).

SSH tunnel (alternative):

```bash
ssh -L 8501:127.0.0.1:8501 guard-host     # then open http://localhost:8501
```

## 7. Backups

```bash
# once, on the host (unverified)
mkdir -p /srv/guard/backups
docker compose exec worker python -m guard.storage.backup backup --dest /data/backups
```

A backup copies `ops.sqlite` (consistent snapshot), the DuckDB warehouse, `config/` and
`manual_holdings.yaml`, with a sha256 manifest. It never copies `.env`, `secrets.enc.yaml` or the
MSCI file. Backups inside the same volume do not survive losing the host: copy them off the host
regularly (for example `rsync` over Tailscale to your laptop, or an encrypted bucket).

Schedule it from the host's cron (unverified):

```cron
15 2 * * * cd /srv/guard/app && docker compose exec -T worker python -m guard.storage.backup backup --dest /data/backups && docker compose exec -T worker python -m guard.storage.backup prune --dest /data/backups
```

Retention: 14 daily, 8 weekly, 24 monthly.

## 8. Restore drill (do it before relying on the host, then quarterly)

On any machine with the repo and a copy of the backups:

```bash
GUARD_BACKUP_DIR=/path/to/backups make restore-drill
```

It restores the latest backup into a temp dir, verifies the manifest, runs SQLite
`integrity_check`, opens the warehouse read-only and prints `status`. To restore for real onto a new
host: stop the stack, then

```bash
docker compose run --rm worker python -m guard.storage.backup restore /data/backups/backup-<stamp> --data-dir /data --force
```

and put `.env` back from your password manager.

## 9. Updating

```bash
cd /srv/guard/app && git pull
docker compose build --pull
docker compose up -d
docker compose ps        # both services should become healthy
```

Take a backup first. Do not update in the minutes before Wednesday 12:00 London.

## 10. Port check

```bash
ops/portscan.sh local                    # on the host: only loopback listens
ops/portscan.sh remote <public-ip>       # from off-tailnet: no open TCP ports
```

## 11. Security checklist

- [ ] No inbound rule in the provider firewall; `ops/portscan.sh remote` passes
- [ ] `ops/portscan.sh local` passes; `docker compose ps` shows only `127.0.0.1:8501`
- [ ] Dashboard reached only through `tailscale serve` or an SSH tunnel; no funnel
- [ ] `.env` is `chmod 600`, not in git (`git ls-files | grep -E '^\.env'` prints nothing), copy kept in a password manager
- [ ] Trading212 keys are read-only and IP-restricted to this host
- [ ] SSH: key-only login, root login off, unattended security upgrades on
- [ ] Containers run as a non-root user with a read-only root filesystem (`docker compose exec worker id`)
- [ ] Backups copied off the host; restore drill passed and dated in `docs/paper-trading.md`
- [ ] `docker compose logs` contains no keys (spot check)
