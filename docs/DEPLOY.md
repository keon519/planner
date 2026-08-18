# Deployment

Tested on Ubuntu 24.04 on an Intel NUC. Any always-on x86 box works.

## Service

```bash
sudo tee /etc/systemd/system/planner.service > /dev/null << UNIT
[Unit]
Description=Study Planner
After=network-online.target
Wants=network-online.target

[Service]
User=$USER
WorkingDirectory=$HOME/planner
ExecStart=$HOME/planner/.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
UNIT

sudo systemctl daemon-reload
sudo systemctl enable --now planner
```

Set the BIOS to **power on after AC loss**, so an outage doesn't leave the
server off until you notice.

## Kiosk display

```bash
sudo systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target
gsettings set org.gnome.desktop.session idle-delay 0
gsettings set org.gnome.desktop.screensaver lock-enabled false

mkdir -p ~/.config/autostart
cat > ~/.config/autostart/planner-kiosk.desktop << 'DESK'
[Desktop Entry]
Type=Application
Name=Planner Kiosk
Exec=/bin/bash -c 'sleep 8; chromium-browser --kiosk --app=http://localhost:8000/display --noerrdialogs --disable-infobars --disable-session-crashed-bubble'
X-GNOME-Autostart-enabled=true
DESK
```

`--disable-session-crashed-bubble` matters: without it, a "Chromium didn't shut
down correctly" banner covers the display after every power cut — exactly when
you're least likely to be watching.

Enable auto-login under Settings → Users, or the kiosk waits at a password prompt.

Display options: `/display?layout=h`, `?back=240&fwd=300`, `?nightdim=0.05`.

## Backups

```bash
sudo apt install -y rclone sqlite3
rclone config                                   # add a remote
echo "PLANNER_REMOTE=myremote:planner-backups" > backup.env
./backup.sh
```

Nightly via a systemd timer — `Persistent=true` so a run missed while the
machine was off fires at next boot:

```bash
sudo tee /etc/systemd/system/planner-backup.timer > /dev/null << 'T'
[Unit]
Description=Nightly planner backup
[Timer]
OnCalendar=*-*-* 03:30:00
Persistent=true
RandomizedDelaySec=300
[Install]
WantedBy=timers.target
T
sudo systemctl enable --now planner-backup.timer
```

**Test the restore.** An untested backup is not a backup:

```bash
./restore.sh --list
./restore.sh Tuesday
```

`backup.sh` uses `sqlite3 .backup` rather than `cp` (copying a live WAL database
can produce a file that looks valid and isn't), verifies with
`PRAGMA integrity_check` before uploading, and refuses to overwrite a good
backup with one containing zero assignments.

## Remote access

**Do not port-forward.** There is no authentication — anyone finding port 8000
could read your coursework and spend your API credit.

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
sudo tailscale set --hostname=planner
sudo tailscale serve --bg 8000        # HTTPS, needed for PWA install
```

Then disable key expiry in the Tailscale admin console. Keys expire after about
six months by default, and when that happens the server silently drops off the
network — the most common way people lose access months later.
