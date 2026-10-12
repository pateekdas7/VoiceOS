# Prometheus configuration profiles

VoiceOS ships two Prometheus configs. The right one depends on whether the
Prometheus pod can reach the Kubernetes API server ClusterIP (`10.96.0.1`).

| Profile | File | Use when |
|---|---|---|
| `k8s-cluster` | `prometheus.yml` | Full K8s cluster where kube-proxy routes the apiserver ClusterIP correctly. Enables three Kubernetes SD jobs (`kubernetes-apiservers`, `kubernetes-nodes-cadvisor`, `voiceos-services` pod-annotation). |
| `single-host` | `prometheus-single-host.yml` | Laptop / single-host dev deployment where the apiserver ClusterIP is unroutable (TT-015: NAT'd `10.0.2.2`). Omits the three k8s_sd jobs; relies on `targets/dev.yml` file_sd. |

Both profiles share: `recording_rules.yml`, `alert_rules/*.yml`, the GPU-node
static job, infra exporters, and the Alertmanager target. They differ only in
whether K8s SD jobs are present.

## Selecting a profile

The systemd unit at `scripts/systemd/prometheus.service` reads the
`PROMETHEUS_CONFIG` environment variable (default: `prometheus-single-host.yml`).
Switch by editing the drop-in override and reloading:

```sh
sudo systemctl edit prometheus.service
# add:
# [Service]
# Environment=PROMETHEUS_CONFIG=prometheus.yml
sudo systemctl daemon-reload
sudo systemctl restart prometheus
```

Verify after restart:

```sh
curl -s localhost:9090/api/v1/status/config | jq -r .data.yaml | head -5
```

The returned YAML's `external_labels.sd_profile` label (`single-host` on the
profile without K8s SD) is the authoritative marker.

## Adding a new service target (single-host profile)

Edit `monitoring/prometheus/targets/dev.yml` and add the service entry:

```yaml
- targets: ["voiceos-platform-<svc>.voiceos-runtime.svc.cluster.local:<port>"]
  labels:
    service: <svc>
    namespace: voiceos-runtime
```

Prometheus auto-reloads `file_sd` targets within 30 s. No SIGHUP required.

## Switching back to the k8s-cluster profile

Once TT-015 is resolved (apiserver ClusterIP routable from inside the cluster),
re-enable the full profile via the systemd override above. The two profiles
produce identical metric streams for the shared jobs; only the pod/node/APIServer
discovery differs.

## Alert rules in both profiles

Both configs load the same rule files, so SLO burn-rate alerts, GPU fleet
degraded alerts, and all other application/business/AI alerts fire identically
regardless of profile. The only difference is which targets Prometheus scrapes.
