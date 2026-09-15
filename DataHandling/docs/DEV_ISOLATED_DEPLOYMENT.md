# Isolated Development Deployment on the Production Host

This configuration creates a third customer-agent stack without replacing or
joining the existing production or development stacks.

## Isolation contract

| Resource | Isolated development value |
|---|---|
| Compose project | `stance-customer-agent-dev-isolated` |
| Container | `customer-agent-dev-isolated` |
| Image | `stance-customer-agent-dev-isolated:local` |
| Host port | `127.0.0.1:8004` |
| Container port | `8000` |
| Network | `customer-agent-dev-isolated-network` |
| Environment file | `DataHandling/.env.dev-isolated` |
| Credentials directory | `DataHandling/config/dev-isolated/` |
| Persistent volumes | All prefixed `customer-agent-dev-isolated-` |

The configuration does not reference `customer-agent-prod`, `customer-agent-dev`,
their networks, their ports, or their volumes.

## 1. Use a separate checkout

Never deploy this from the production checkout. On the server, use a dedicated
directory such as:

```bash
/opt/stance/customer-agent-dev-isolated
```

Confirm the checkout before continuing:

```bash
pwd
git branch --show-current
git log -1 --oneline
git status --short
```

## 2. Confirm port 8004 is free

```bash
sudo ss -lntp | grep ':8004 '
docker ps --format 'table {{.Names}}\t{{.Ports}}'
```

The first command should return no listener. Do not change the existing mappings
on ports 8000–8003.

## 3. Create isolated environment inputs

From the isolated checkout root:

```bash
cp DataHandling/.env.example DataHandling/.env.dev-isolated
chmod 600 DataHandling/.env.dev-isolated
mkdir -p DataHandling/config/dev-isolated
```

Set only development credentials in `DataHandling/.env.dev-isolated`. At minimum,
use a separate development MongoDB database:

```dotenv
MONGO_URI=<development-cluster-uri>
MONGO_DB_NAME=stance-dashboard-dev-isolated
MONGO_USERS_COLLECTION=users
MONGO_REPORT_JOBS_COLLECTION=customer-agent-report-jobs
MONGO_CLINICAL_ESCALATIONS_COLLECTION=customer-agent-clinical-escalations
GEMINI_API_KEY=<development-key>
S3_BUCKET_NAME=<development-bucket>
```

Do not copy the production `.env`. Use a separate S3 bucket and separate or
restricted development cloud credentials. If Google Speech fallback is required,
place its development credential at:

```text
DataHandling/config/dev-isolated/google_key.json
```

The environment file and credential directory are ignored by Git.

## 4. Validate without touching running containers

Always include the isolated Compose filename:

```bash
docker compose -f docker-compose.dev-isolated.yml config --quiet
docker compose -f docker-compose.dev-isolated.yml build
```

Building creates the isolated image name and does not recreate production or the
existing port-8003 development container.

## 5. Start the isolated stack

```bash
docker compose -f docker-compose.dev-isolated.yml up -d
docker compose -f docker-compose.dev-isolated.yml ps
docker compose -f docker-compose.dev-isolated.yml logs --tail=200 customer-agent-dev-isolated
curl http://127.0.0.1:8004/health
```

Expected container name:

```text
customer-agent-dev-isolated
```

## 6. Expose through a dedicated reverse-proxy hostname

Point a development-only hostname to `http://127.0.0.1:8004`. The proxy must
support WebSocket upgrade headers and a request-body limit compatible with the
application's upload policy.

Example Nginx server block:

```nginx
server {
    listen 443 ssl;
    server_name dev-isolated-customeragent.example.com;

    client_max_body_size 30m;

    location / {
        proxy_pass http://127.0.0.1:8004;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 300s;
        proxy_send_timeout 300s;
    }
}
```

Replace the example hostname and use the server's approved TLS certificate
process. Validate Nginx with `sudo nginx -t` before reloading it.

If the frontend is on a different origin, that exact HTTPS origin must also be
present in `CORS_ALLOWED_ORIGINS` in `app/config.py` before building the image.

## 7. Frontend development environment

Build or deploy the frontend with environment-specific public values:

```dotenv
VITE_APP_ENV=development
VITE_API_URL=https://<isolated-backend-hostname>
VITE_WS_URL=wss://<isolated-backend-hostname>
VITE_GRAPHQL_URL=https://<development-graphql-host>/graphql
VITE_CONSENT_URL=https://<development-consent-host>
VITE_API_KEY=<development-browser-key>
VITE_ORGANIZATION_ID=<development-organization-id>
```

Do not append `/api` or `/ws` to the API and WebSocket base URLs.

## 8. Existing consent/OTP dependency

The customer-agent does not issue its own access token. The frontend checks the
consent state stored by the external OTP/consent flow. The synthetic development
patient and consent record must therefore exist in the isolated development
database, or the approved development consent service must write to that database.

Do not point the isolated container at the production patient database merely to
reuse an existing consent record.

## 9. Update only this stack

```bash
git pull --ff-only
docker compose -f docker-compose.dev-isolated.yml build
docker compose -f docker-compose.dev-isolated.yml up -d
curl http://127.0.0.1:8004/health
```

Review the branch and commit before every update. Never run these commands from
the production checkout.

## 10. Stop or roll back only this stack

Stop without deleting its data:

```bash
docker compose -f docker-compose.dev-isolated.yml stop
```

Remove only its containers/network while retaining named volumes:

```bash
docker compose -f docker-compose.dev-isolated.yml down
```

Do not add `--volumes` unless deletion of the isolated development data is
explicitly intended. Never run an unqualified `docker compose down` on the host.

## 11. Final verification

```bash
docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}'
curl http://127.0.0.1:8004/health
docker compose -f docker-compose.dev-isolated.yml logs --tail=200 customer-agent-dev-isolated
```

Confirm that:

- `customer-agent-prod` is still healthy on port 8000;
- `customer-agent-dev` is still healthy on port 8003;
- `customer-agent-dev-isolated` is healthy on loopback port 8004;
- the new frontend uses only the isolated backend hostname;
- MongoDB writes appear only in the isolated development database; and
- uploads appear only in the development S3 bucket.
