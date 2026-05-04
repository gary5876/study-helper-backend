#!/usr/bin/env bash
# Run on EC2 via SSM. Argument: SHA-tagged image (e.g. abc123 -> :abc123 tag)
#
# Source of truth for app secrets: SSM Parameter Store at /study-helper/backend/*
# (synced from GitHub Secrets by the deploy workflow's sync-secrets job).
set -euo pipefail

SHA="${1:?SHA argument required}"
REGION="ap-northeast-2"
REGISTRY="250847881242.dkr.ecr.${REGION}.amazonaws.com"
REPO="study-helper-backend"
IMAGE="${REGISTRY}/${REPO}:${SHA}"
PARAM_PREFIX="/study-helper/backend"

cd "$(dirname "$0")"

if [ ! -f .env.prod ]; then
  echo "ERROR: .env.prod not found at $(pwd). Bootstrap it from .env.prod.example first." >&2
  exit 2
fi

echo "[1/5] Fetch secrets from Parameter Store"
SSM_NAMES=(
  SUPABASE_URL
  SUPABASE_SERVICE_ROLE_KEY
  SUPABASE_JWT_SECRET
  SUPABASE_DB_URL
  GEMINI_API_KEYS
)
TMP_ENV="$(mktemp)"
trap 'rm -f "$TMP_ENV"' EXIT
chmod 600 "$TMP_ENV"

# Fetch each parameter individually so a missing one yields a clear error.
for name in "${SSM_NAMES[@]}"; do
  if value=$(aws ssm get-parameter \
              --region "$REGION" \
              --name "${PARAM_PREFIX}/${name}" \
              --with-decryption \
              --query 'Parameter.Value' \
              --output text 2>/dev/null); then
    printf '%s=%s\n' "$name" "$value" >> "$TMP_ENV"
    echo "  fetched: $name"
  else
    echo "  WARN: ${PARAM_PREFIX}/${name} not found — leaving previous value in .env.prod"
  fi
done

echo "[2/5] Merge secrets + IMAGE into .env.prod"
# Replace each fetched key in .env.prod (or append if missing).
while IFS= read -r line; do
  key="${line%%=*}"
  if grep -q "^${key}=" .env.prod; then
    # Use a delimiter unlikely to appear in values; pipe is risky for URLs.
    sudo awk -v k="$key" -v repl="$line" 'BEGIN{done=0} { if($0 ~ "^"k"=") {print repl; done=1} else print } END{if(!done) print repl}' \
      .env.prod | sudo tee .env.prod.new >/dev/null
    sudo mv .env.prod.new .env.prod
  else
    echo "$line" | sudo tee -a .env.prod >/dev/null
  fi
done < "$TMP_ENV"

# Update IMAGE tag.
sudo sed -i "s|^IMAGE=.*|IMAGE=${IMAGE}|" .env.prod
sudo chmod 600 .env.prod

echo "[3/5] ECR login"
aws ecr get-login-password --region "$REGION" \
  | sudo docker login --username AWS --password-stdin "${REGISTRY}"

echo "[4/5] Pull + up"
sudo docker compose -f docker-compose.prod.yml --env-file .env.prod pull
sudo docker compose -f docker-compose.prod.yml --env-file .env.prod up -d --remove-orphans

echo "[5/5] Status"
sleep 5
sudo docker compose -f docker-compose.prod.yml --env-file .env.prod ps

sudo docker image prune -f

echo "=== Deploy complete: ${IMAGE} ==="
