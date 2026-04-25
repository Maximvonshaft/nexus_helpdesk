# Worker Healthcheck Policy

## Worker is not an HTTP service

`worker` runs `python scripts/run_worker.py --worker-id ...`. It should not inherit the API container's `/healthz` Docker healthcheck because it does not serve HTTP.

## Recommended Docker Compose policy

For worker services using the same image as the API service, disable image healthcheck at service level:

```yaml
worker:
  healthcheck:
    disable: true
```

## Healthy worker signals

Use operational signals instead of HTTP probes:

- worker process is running
- worker logs contain `worker_cycle_complete`
- pending/retry/dead queue counts are stable or decreasing
- background jobs can be claimed and completed
- runtime health API exposes job and queue status

## Why not pgrep/ps inside the container?

Slim Python images may not include `pgrep` or `ps`. A shell-based process healthcheck can become unreliable and mark a healthy worker unhealthy. Round A documents this because the production server already observed `pgrep: not found` and `ps: not found` issues.

## Production operator commands

```bash
docker logs --tail 80 deploy-worker-1
docker compose -f deploy/docker-compose.server.yml ps
```

Worker health should be surfaced in NexusDesk runtime health through queue/job progress rather than an inherited app HTTP healthcheck.
