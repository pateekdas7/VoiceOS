# Runbook: MongoDBDown
**Alert:** MongoDB connection refused or auth failure | **Severity:** critical

## What it means
MongoDB (Docker container voiceos-mongod, port 27017) is unreachable. Call recordings metadata, conversation logs, and campaign event storage are unavailable.

## Immediate steps
```bash
# Check container status
docker ps --filter name=voiceos-mongod

# If not running — check why it stopped
docker ps -a --filter name=voiceos-mongod
docker logs voiceos-mongod --tail 50
```

## Restart container
```bash
docker start voiceos-mongod
sleep 5
docker exec voiceos-mongod mongosh --eval "db.adminCommand('ping')"
```

## Check replica set
```bash
docker exec voiceos-mongod mongosh -u voiceos \
  -p "$(grep MONGODB_URI /opt/voiceos/.env | python3 -c 'import sys,urllib.parse; u=urllib.parse.urlparse(sys.stdin.read().strip().split("=",1)[1]); print(u.password)')" \
  --authenticationDatabase voiceos \
  --eval "rs.status().ok"
```

## If replica set not initialized
```bash
docker exec voiceos-mongod mongosh --eval "rs.initiate({_id:'rs0',members:[{_id:0,host:'127.0.0.1:27017'}]})"
```

## Escalate if
- Container keeps restarting (OOMKilled or disk full)
- Data directory `/opt/voiceos/mongo-data` shows corruption errors in logs
- `rs.status()` shows member in ROLLBACK or RECOVERING state >5 minutes

## Resolution
- OOMKilled: increase Docker memory limit in run command, or free host memory
- Disk full: follow disk-space-critical.md first
- Data corruption: restore from `/backup/voiceos_mongo/` using `mongodb_restore.sh`
