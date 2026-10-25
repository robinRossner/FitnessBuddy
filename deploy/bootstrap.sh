#!/usr/bin/env bash
# One-time (idempotent) server setup for a fresh Oracle Cloud Ubuntu 24.04 aarch64 VM.
# Run as the normal sudo user (ubuntu) on the server:
#   scp deploy/bootstrap.sh ubuntu@HOST:~ && ssh ubuntu@HOST 'bash ~/bootstrap.sh'
# Re-running is safe.
set -euo pipefail

TARGET_USER="${SUDO_USER:-$(id -un)}"
APP_DIR=/opt/fitnessbuddy
LOG=/var/log/fitnessbuddy-backup.log
TZ_NAME=Europe/Vienna
SWAP_SIZE=2G

log() { printf '\n==> %s\n' "$*"; }

if [[ "$(id -u)" -eq 0 && -z "${SUDO_USER:-}" ]]; then
  echo "error: run as the normal user (ubuntu) with sudo rights, not as root" >&2
  exit 1
fi
sudo -v || { echo "error: sudo not available" >&2; exit 1; }
# shellcheck disable=SC1091
. /etc/os-release
[[ "${ID:-}" == ubuntu ]] || { echo "error: expected Ubuntu, got ${ID:-?}" >&2; exit 1; }

apt_noninteractive() { sudo env DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a apt-get -y "$@"; }

# --- timezone (first, so the cron time below means Vienna time) ---
log "timezone $TZ_NAME"
sudo timedatectl set-timezone "$TZ_NAME"

# --- packages ---
log "apt update/upgrade"
apt_noninteractive update
apt_noninteractive -o Dpkg::Options::=--force-confold upgrade
apt_noninteractive install ca-certificates curl gnupg sqlite3 rsync unattended-upgrades cron logrotate

log "docker apt repository"
arch="$(dpkg --print-architecture)"
codename="${UBUNTU_CODENAME:-$VERSION_CODENAME}"
sudo install -m 0755 -d /etc/apt/keyrings
if [[ ! -s /etc/apt/keyrings/docker.asc ]]; then
  sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  sudo chmod a+r /etc/apt/keyrings/docker.asc
fi
echo "deb [arch=$arch signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $codename stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
apt_noninteractive update

log "install docker engine + compose plugin"
apt_noninteractive install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo systemctl enable --now docker containerd
sudo usermod -aG docker "$TARGET_USER"

# --- unattended security upgrades ---
log "unattended-upgrades"
sudo tee /etc/apt/apt.conf.d/20auto-upgrades >/dev/null <<'CONF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
CONF
sudo systemctl enable --now unattended-upgrades >/dev/null 2>&1 || true

# --- firewall: Oracle images ship a REJECT rule in INPUT; open 80/443 (+ UDP 443 for HTTP/3) ---
log "iptables: allow 80/tcp, 443/tcp, 443/udp"
RULES=(
  "-p tcp -m state --state NEW -m tcp --dport 80 -j ACCEPT"
  "-p tcp -m state --state NEW -m tcp --dport 443 -j ACCEPT"
  "-p udp -m udp --dport 443 -j ACCEPT"
)
# 1) live ruleset: insert before the first REJECT in INPUT (or append if there is none)
reject_pos="$(sudo iptables -S INPUT | grep '^-A INPUT' | grep -n -m1 -e '-j REJECT' | cut -d: -f1 || true)"
for r in "${RULES[@]}"; do
  # shellcheck disable=SC2086  # word splitting of the rule is intended
  if ! sudo iptables -C INPUT $r 2>/dev/null; then
    if [[ -n "$reject_pos" ]]; then
      # shellcheck disable=SC2086
      sudo iptables -I INPUT "$reject_pos" $r
      reject_pos=$((reject_pos + 1))
    else
      # shellcheck disable=SC2086
      sudo iptables -A INPUT $r
    fi
  fi
done
# 2) persistent file: edit /etc/iptables/rules.v4 directly. `netfilter-persistent save` would
#    also dump Docker's dynamic chains into the file, which then fight Docker at boot.
sudo env DEBIAN_FRONTEND=noninteractive apt-get -y install iptables-persistent netfilter-persistent >/dev/null
sudo systemctl enable netfilter-persistent >/dev/null 2>&1 || true
V4=/etc/iptables/rules.v4
if [[ -f "$V4" ]]; then
  if ! sudo grep -q -e '--dport 443 -j ACCEPT' "$V4" || ! sudo grep -q -e '-p udp -m udp --dport 443 -j ACCEPT' "$V4"; then
    tmp="$(mktemp)"
    # shellcheck disable=SC2024  # tmp is a user-owned temp file; only $V4 needs sudo to read
    sudo awk -v r1="-A INPUT ${RULES[0]}" -v r2="-A INPUT ${RULES[1]}" -v r3="-A INPUT ${RULES[2]}" '
      function emit() { if (!done) { print r1; print r2; print r3; done = 1 } }
      /^\*/ { intable = ($0 == "*filter") }
      intable && /^-A INPUT .*-j REJECT/ { emit() }
      intable && /^COMMIT/ { emit() }
      { print }
    ' "$V4" > "$tmp"
    sudo install -m 0644 -o root -g root "$tmp" "$V4"
    rm -f "$tmp"
  fi
else
  echo "warning: $V4 not found; rules are active now but not persisted" >&2
fi

# --- app dir, backup cron, log ---
log "app dir $APP_DIR"
sudo mkdir -p "$APP_DIR/backend" "$APP_DIR/deploy"
sudo chown -R "$TARGET_USER:$TARGET_USER" "$APP_DIR"

log "backup cron (03:15 daily) + log"
sudo touch "$LOG"
sudo chown "$TARGET_USER:$TARGET_USER" "$LOG"
sudo chmod 640 "$LOG"
sudo tee /etc/logrotate.d/fitnessbuddy-backup >/dev/null <<CONF
$LOG {
    monthly
    rotate 6
    compress
    missingok
    notifempty
    create 640 $TARGET_USER $TARGET_USER
}
CONF
cron_line="15 3 * * * $APP_DIR/deploy/backup.sh >> $LOG 2>&1"
current="$(sudo crontab -u "$TARGET_USER" -l 2>/dev/null || true)"
if ! grep -qF "$APP_DIR/deploy/backup.sh" <<<"$current"; then
  { [[ -n "$current" ]] && printf '%s\n' "$current"; printf '%s\n' "$cron_line"; } | sudo crontab -u "$TARGET_USER" -
fi
sudo systemctl enable --now cron >/dev/null 2>&1 || true

# --- swap ---
log "swap"
if [[ -z "$(swapon --show --noheadings)" ]]; then
  if [[ ! -e /swapfile ]]; then
    sudo fallocate -l "$SWAP_SIZE" /swapfile
    sudo chmod 600 /swapfile
    sudo mkswap /swapfile >/dev/null
  fi
  sudo swapon /swapfile
  grep -qE '^/swapfile\s' /etc/fstab || echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
  echo "swapfile $SWAP_SIZE enabled"
else
  echo "swap already present, skipping"
fi

# --- SSH hardening: key-only login, but only if that cannot lock us out ---
log "ssh hardening"
home="$(getent passwd "$TARGET_USER" | cut -d: -f6)"
if sudo grep -qE '^(ssh-|ecdsa-|sk-)' "$home/.ssh/authorized_keys" 2>/dev/null; then
  CONF=/etc/ssh/sshd_config.d/01-fitnessbuddy-hardening.conf   # 01-: first match wins in sshd
  sudo tee "$CONF" >/dev/null <<'CONF'
PasswordAuthentication no
KbdInteractiveAuthentication no
CONF
  if sudo sshd -t && [[ "$(sudo sshd -T | awk '$1=="passwordauthentication"{print $2}')" == no ]]; then
    sudo systemctl reload ssh
    echo "password logins disabled (existing sessions stay open)"
  else
    sudo rm -f "$CONF"
    echo "warning: could not verify sshd config; hardening reverted" >&2
  fi
else
  echo "no authorized_keys for $TARGET_USER: skipping SSH hardening (would risk lock-out)"
fi

log "done"
docker --version
docker compose version
echo "Log out and back in (or run: newgrp docker) so the docker group applies to your shell."
[[ -f /var/run/reboot-required ]] && echo "A reboot is recommended: sudo reboot" || true
