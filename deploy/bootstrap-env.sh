#!/usr/bin/env bash
# Builds deploy/.env from AWS Systems Manager Parameter Store. Parameter Store is the single source of truth:
#
#   <prefix>/secret/<NAME>   SecureString (KMS aws/ssm key, free)  -> environment variable NAME
#   <prefix>/env/<NAME>      String                                  -> environment variable NAME
#
# Generated automatically on first run if still "UNSET": JOBHUB_API_TOKEN, JOBHUB_VNC_PASSWORD, JOBHUB_BROWSER_PASSWORD
# (+ its bcrypt hash). Values are never printed and never written anywhere except deploy/.env (mode 600, encrypted disk).
# Needs: aws cli, docker, python3, openssl; the instance role from infra/terraform.
set -euo pipefail
cd "$(dirname "$0")"

CONF="${AJS_BOOTSTRAP_CONF:-/etc/ajs/bootstrap.conf}"
# shellcheck disable=SC1090
[ -f "$CONF" ] && . "$CONF"
: "${AJS_SSM_PREFIX:?AJS_SSM_PREFIX not set (expected in $CONF)}"
: "${AWS_DEFAULT_REGION:?AWS_DEFAULT_REGION not set (expected in $CONF)}"
export AWS_DEFAULT_REGION

get_secret() {  # name -> value or empty
    aws ssm get-parameter --name "$AJS_SSM_PREFIX/secret/$1" --with-decryption \
        --query Parameter.Value --output text 2>/dev/null || true
}
put_secret() {  # name value
    aws ssm put-parameter --name "$AJS_SSM_PREFIX/secret/$1" --type SecureString --overwrite --value "$2" >/dev/null
}
missing() { [ -z "$1" ] || [ "$1" = "UNSET" ] || [ "$1" = "None" ]; }

ensure_random() {  # name, length (hex chars via openssl, then trimmed)
    local cur; cur="$(get_secret "$1")"
    if missing "$cur"; then put_secret "$1" "$(openssl rand -hex 32 | head -c "$2")"; echo "generated $1"; fi
}

ensure_random JOBHUB_API_TOKEN 64
ensure_random JOBHUB_VNC_PASSWORD 8   # VNC only uses the first 8 characters

if missing "$(get_secret JOBHUB_BROWSER_HASH)" || missing "$(get_secret JOBHUB_BROWSER_PASSWORD)"; then
    PW="$(openssl rand -hex 32 | head -c 24)"
    HASH="$(docker run --rm caddy:2.11.7-alpine caddy hash-password --plaintext "$PW")"
    put_secret JOBHUB_BROWSER_PASSWORD "$PW"
    put_secret JOBHUB_BROWSER_HASH "$HASH"
    echo "generated JOBHUB_BROWSER_PASSWORD + hash"
fi

# Pull everything under <prefix>/env and <prefix>/secret into .env (Compose reads "$$" as a literal "$").
umask 077
python3 - "$AJS_SSM_PREFIX" <<'PY'
import json, os, re, subprocess, sys
prefix = sys.argv[1]
env = {}
for kind in ("env", "secret"):
    token = None
    while True:
        cmd = ["aws", "ssm", "get-parameters-by-path", "--path", f"{prefix}/{kind}", "--with-decryption", "--output", "json"]
        if token:
            cmd += ["--next-token", token]
        out = json.loads(subprocess.run(cmd, check=True, capture_output=True, text=True).stdout)
        for p in out.get("Parameters", []):
            name = p["Name"].rsplit("/", 1)[-1]
            val = p["Value"]
            if not re.fullmatch(r"[A-Z][A-Z0-9_]*", name) or val in ("", "UNSET") or "\n" in val:
                continue
            env[name] = val
        token = out.get("NextToken")
        if not token:
            break
tmp = ".env.tmp"
with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as fh:
    for k in sorted(env):
        fh.write(f"{k}={env[k].replace('$', '$$')}\n")
os.replace(tmp, ".env")
os.chmod(".env", 0o600)
print("wrote deploy/.env with:", ", ".join(sorted(env)))  # names only, never values
PY
