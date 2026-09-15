#!/usr/bin/env bash
# One-time EC2 setup. Idempotent — safe to re-run.
#
# Run as a sudo-capable user (ubuntu on Ubuntu AMIs):
#   curl -fsSL https://raw.githubusercontent.com/limian2001/factline/main/deploy/bootstrap.sh | bash -s -- limian2001
set -euo pipefail

GH_USER="${1:-limian2001}"
REPO="https://github.com/${GH_USER}/factline.git"
APP=/opt/factline
SVC_USER=factline

echo "==> [1/7] system packages"
sudo apt-get update -qq
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
  git curl ca-certificates debian-keyring debian-archive-keyring apt-transport-https

echo "==> [2/7] UTC clock"
# The systemd timer is written in UTC so the schedule never shifts under DST.
sudo timedatectl set-timezone UTC

echo "==> [3/7] service user"
# No login shell and no password: nothing ever signs in as this account. It
# exists only for systemd to run the pipeline and the self-deploy as, which is
# why the deploy needs no SSH access from outside at all.
if ! id -u "$SVC_USER" >/dev/null 2>&1; then
  sudo useradd --system --create-home --home-dir /home/$SVC_USER \
    --shell /usr/sbin/nologin "$SVC_USER"
fi

echo "==> [4/7] caddy"
if ! command -v caddy >/dev/null 2>&1; then
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
    | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
    | sudo tee /etc/apt/sources.list.d/caddy-stable.list >/dev/null
  sudo apt-get update -qq
  sudo apt-get install -y -qq caddy
fi

echo "==> [5/7] checkout"
sudo mkdir -p "$APP" /var/log/caddy
sudo chown "$SVC_USER:$SVC_USER" "$APP"
if [ ! -d "$APP/.git" ]; then
  sudo -u "$SVC_USER" git clone --quiet "$REPO" "$APP"
else
  sudo -u "$SVC_USER" git -C "$APP" fetch --quiet origin
fi
sudo -u "$SVC_USER" mkdir -p "$APP/data/site"

echo "==> [6/7] uv (installed for the service user, which is what systemd runs as)"
if ! sudo -u "$SVC_USER" test -x /home/$SVC_USER/.local/bin/uv; then
  sudo -u "$SVC_USER" bash -c 'curl -LsSf https://astral.sh/uv/install.sh | sh' >/dev/null
fi
sudo ln -sf /home/$SVC_USER/.local/bin/uv /usr/local/bin/uv
sudo -u "$SVC_USER" bash -lc "cd $APP && uv sync --frozen --no-dev"

echo "==> [7/8] caddy site"
sudo cp "$APP/deploy/Caddyfile" /etc/caddy/Caddyfile
sudo systemctl reload caddy || sudo systemctl restart caddy

echo "==> [8/8] systemd units"
sudo install -m 644 "$APP/deploy/factline.service"        /etc/systemd/system/factline.service
sudo install -m 644 "$APP/deploy/factline.timer"          /etc/systemd/system/factline.timer
sudo install -m 644 "$APP/deploy/factline-deploy.service" /etc/systemd/system/factline-deploy.service
sudo install -m 644 "$APP/deploy/factline-deploy.timer"   /etc/systemd/system/factline-deploy.timer
sudo systemctl daemon-reload

cat <<'NEXT'

================================================================
 Bootstrap done. Two manual steps remain — both involve secrets,
 which is why they are deliberately not automated.

 1. Write the secrets file:

      sudo -u factline tee /opt/factline/.env >/dev/null <<'ENVEOF'
      SEC_USER_AGENT="Mian Li mianmianlife@gmail.com"
      SEC_RATE_LIMIT_RPS=8
      TIINGO_API_KEY=...
      ANTHROPIC_API_KEY=...
      DATA_DIR=/opt/factline/data
      ENVEOF
      sudo chmod 600 /opt/factline/.env

 2. Let the service account manage its own two timers, and nothing else:

      sudo tee /etc/sudoers.d/factline >/dev/null <<'SUDOEOF'
factline ALL=(root) NOPASSWD: /usr/bin/systemctl daemon-reload
factline ALL=(root) NOPASSWD: /usr/bin/systemctl enable factline.timer factline-deploy.timer
factline ALL=(root) NOPASSWD: /usr/bin/systemctl enable --now factline.timer
factline ALL=(root) NOPASSWD: /usr/bin/systemctl restart factline.timer
factline ALL=(root) NOPASSWD: /usr/bin/systemctl start factline.service
factline ALL=(root) NOPASSWD: /usr/bin/install -m 644 /opt/factline/deploy/factline.service /etc/systemd/system/factline.service
factline ALL=(root) NOPASSWD: /usr/bin/install -m 644 /opt/factline/deploy/factline.timer /etc/systemd/system/factline.timer
factline ALL=(root) NOPASSWD: /usr/bin/install -m 644 /opt/factline/deploy/factline-deploy.service /etc/systemd/system/factline-deploy.service
factline ALL=(root) NOPASSWD: /usr/bin/install -m 644 /opt/factline/deploy/factline-deploy.timer /etc/systemd/system/factline-deploy.timer
SUDOEOF
      sudo chmod 440 /etc/sudoers.d/factline
      sudo visudo -c -f /etc/sudoers.d/factline     # must say "parsed OK"

 Then start the two timers and watch the first self-deploy:

      sudo systemctl enable --now factline.timer factline-deploy.timer
      journalctl -u factline-deploy.service -f
================================================================
NEXT
