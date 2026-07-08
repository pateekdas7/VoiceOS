# Sprint-026: minimal container image shared by every Helm service chart's
# Deployment. Runs the existing Sprint-016 health app (src/libs/health) as a
# real process so K8s Deployment/PDB/NetworkPolicy/QoS/taint behavior can be
# validated against real Running pods. Contains no service business logic --
# every VoiceOS service remains a library class with no standalone business
# HTTP API (TT-006, unchanged by this image).
FROM python:3.12-slim

WORKDIR /app

COPY dist/*.whl /tmp/wheels/
RUN pip install --no-cache-dir --no-index --find-links=/tmp/wheels starlette uvicorn \
    && rm -rf /tmp/wheels

# Sprint-027: real /metrics exposition (prometheus_client, pure Python --
# staged from the already-installed venv rather than a wheel, since no
# prometheus_client wheel was pre-downloaded into dist/ for this image).
COPY deployment/k8s/health_stub/prometheus_client_pkg /usr/local/lib/python3.12/site-packages/prometheus_client
COPY deployment/k8s/health_stub/prometheus_client-0.25.0.dist-info /usr/local/lib/python3.12/site-packages/prometheus_client-0.25.0.dist-info

COPY src/libs/health /app/src/libs/health
COPY src/__init__.py /app/src/__init__.py
COPY src/libs/__init__.py /app/src/libs/__init__.py
COPY deployment/k8s/health_stub/serve.py /app/serve.py

# Every chart's securityContext sets runAsNonRoot: true (K8S-1 / Kyverno
# disallow-privileged-containers) -- python:3.12-slim's default user is
# root, which Kubernetes rejects at container-create time regardless of the
# image actually needing root. Run as a real non-root UID instead.
RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin voiceos \
    && chown -R voiceos:voiceos /app
USER 10001

ENV PORT=8080
EXPOSE 8080

CMD ["python", "serve.py"]
