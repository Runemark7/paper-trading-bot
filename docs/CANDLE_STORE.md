# Candle store runbook

BTC/USDT and ETH/USDT 5m bars live in Postgres (`candles`). Prod reads
them for the live book and for the shared tape workers download. Workers
do not get database credentials. If `PAPERBOT_DATABASE_URL` is unset, the
process keeps using `live_tape/*.npy` and `crypto_history_5m.json`.

The store becomes the tape only after **both** symbols have a bar at or
before the canonical start (2021-01-26 00:00 UTC) and a bar within 36
hours of the latest closed candle. Until that backfill finishes, prod
keeps serving the file tape.

ETH stays in the store. Gate v4 uses it as a cross-asset check.

## What ops already has

- Namespace `trading`.
- Secret `paperbot-db`, key `DATABASE_URL`. The URL contains
  `sslmode=require` and a host on the private WireGuard network.
  Do not print it, do not commit it.
- Postgres 18, `max_connections` 30, shared with other apps.

Paperbot keeps at most 2 connections per process and closes idle ones
after 30s. The manifests pin web at 2, the cycle at 1, and the CronJob
at 1 (the job exits and drops its connection). Do not raise
`PAPERBOT_DB_POOL_MAX` past 2; the code clamps it.

## Network

The backend pods and the CronJob must open TCP to the host in
`DATABASE_URL` (WireGuard address, port 5432). They use the cluster
network and leave via the node, so the node needs a route to that
WireGuard peer and Postgres must allow that peer. Public Binance egress
is the same path the live cycle already uses.

A timeout on the Job below is a route or `pg_hba` problem, not a bad
password. Confirm without printing the URL:

```bash
kubectl -n trading get secret paperbot-db
```

## Order

1. Merge this change and wait until Argo is running the image that
   contains `python -m hedge_fund.data.candle_backfill`. The CronJob
   uses that same image. An empty store makes the CronJob log
   "store empty" and exit 0; it does not download history by itself.
2. Optional: suspend the CronJob so it does not page Binance while the
   backfill does.

```bash
kubectl -n trading patch cronjob paperbot-candle-updater \
  -p '{"spec":{"suspend":true}}'
```

3. Run the backfill Job with the **deployed** backend image (the command
   has to be in that image):

```bash
IMAGE=$(kubectl -n trading get deploy paperbot-backend \
  -o jsonpath='{.spec.template.spec.containers[?(@.name=="web")].image}')
echo "$IMAGE"

kubectl -n trading delete job paperbot-candle-backfill --ignore-not-found
kubectl -n trading apply -f - <<EOF
apiVersion: batch/v1
kind: Job
metadata:
  name: paperbot-candle-backfill
  namespace: trading
spec:
  backoffLimit: 1
  activeDeadlineSeconds: 7200
  ttlSecondsAfterFinished: 86400
  template:
    spec:
      restartPolicy: Never
      imagePullSecrets:
        - name: ghcr-pull-secret
      securityContext:
        runAsUser: 1000
        runAsGroup: 1000
      containers:
        - name: backfill
          image: ${IMAGE}
          imagePullPolicy: IfNotPresent
          command: ["python", "-m", "hedge_fund.data.candle_backfill"]
          env:
            - name: PAPERBOT_DATABASE_URL
              valueFrom:
                secretKeyRef:
                  name: paperbot-db
                  key: DATABASE_URL
            - name: PAPERBOT_DB_APP_NAME
              value: paperbot-candle-backfill
            - name: PAPERBOT_DB_POOL_MAX
              value: "1"
            - name: PAPER_ONLY
              value: "1"
          resources:
            requests:
              cpu: 100m
              memory: 256Mi
            limits:
              cpu: "1"
              memory: 512Mi
EOF

kubectl -n trading logs -f job/paperbot-candle-backfill
```

The default start is the canonical tape start (2021-01-26), not the
Binance listing date. Re-running the Job resumes: each page is committed,
and the next run continues from the stored frontier and fills holes.
Expect on the order of 600 requests per symbol. The log line to read is:

```text
[candles] BTC/USDT: bars=... first=... last=... unexpected_gaps=... known_gaps=... monotonic=True
[candles] unexpected_gaps=0
```

`known_gaps` are documented Binance maintenance windows (no kline while
spot trading was suspended). `UNEXPECTED` lines are holes outside that
list. The command still exits 0 after printing them.

4. Resume the CronJob:

```bash
kubectl -n trading patch cronjob paperbot-candle-updater \
  -p '{"spec":{"suspend":false}}'
```

5. No restart is required for the cutover. The next read after both
   symbols are caught up selects the store. `/api/status` →
   `running_now.live_history` then reports `store: candles`,
   `last_candle_time`, and `gap_count`. `GET /health` includes the same
   candle fields and stays `ok: true` if Postgres is briefly unreachable.

   Restart only after a truncate, if a process had already latched onto
   the store:

```bash
kubectl -n trading rollout restart deploy/paperbot-backend
```

Discovery workers keep calling `GET /api/discovery/history` and the npz
route. They do not need `crypto_history_5m.json` in claim mode. The
`'{}'` placeholder on worker disks can be deleted.

## Rollback

Unsetting `PAPERBOT_DATABASE_URL` (and restarting) goes back to the file
tape immediately. After the store has been selected, the npy files stop
receiving new bars, so that file tape is frozen at cutover. The rows
already in Postgres stay until someone deletes them. Truncating `candles`
and restarting the backend also falls back to the files, because an
empty store is not selected.

Do not point workers at the database.
