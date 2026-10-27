#!/usr/bin/env bash
# One-time setup on the cloud VM: writes deploy/.env with random secrets. Prints the values you need in the app ONCE.
# Usage: ./setup.sh jobs.yourdomain.com
set -euo pipefail
cd "$(dirname "$0")"

DOMAIN="${1:-}"
if [[ -z "$DOMAIN" ]]; then
    echo "usage: $0 <domain pointing at this VM>   e.g. $0 jobs.example.com" >&2
    exit 1
fi
if [[ -f .env ]]; then
    echo ".env already exists - refusing to overwrite (delete it yourself to regenerate)" >&2
    exit 1
fi
command -v docker >/dev/null || { echo "docker not installed - see README 'Deploy to a cloud VM'" >&2; exit 1; }

TOKEN="$(openssl rand -hex 32)"
VNC_PW="$(openssl rand -base64 12 | tr -dc 'A-Za-z0-9' | head -c 8)"   # VNC uses only the first 8 chars
BROWSER_PW="$(openssl rand -base64 18 | tr -dc 'A-Za-z0-9' | head -c 20)"
HASH="$(docker run --rm caddy:2.11.7-alpine caddy hash-password --plaintext "$BROWSER_PW")"

# bcrypt hashes contain "$"; Compose interpolates env_file values, so each "$" is written as "$$".
umask 077
cat > .env <<EOF
JOBHUB_DOMAIN=$DOMAIN
JOBHUB_API_TOKEN=$TOKEN
JOBHUB_VNC_PASSWORD=$VNC_PW
JOBHUB_BROWSER_USER=jobhub
JOBHUB_BROWSER_HASH=${HASH//\$/\$\$}
JOBHUB_DISABLE_DOCS=0
EOF

cat <<EOF

.env written (mode 600). Enter these in the Android app (Settings > Server) - they are not shown again:

  Server URL     : https://$DOMAIN
  API token      : $TOKEN

Remote browser (for logging in to LinkedIn/Naukri/Indeed):
  URL            : https://$DOMAIN/browser/vnc.html?path=browser/websockify&autoconnect=1&resize=scale
  Basic-auth     : jobhub / $BROWSER_PW
  VNC password   : $VNC_PW

Next: docker compose up -d --build
EOF
