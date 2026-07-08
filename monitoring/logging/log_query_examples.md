# VoiceOS v2 — Common LogQL Queries

Reference queries against the Loki datasource (`monitoring/logging/loki.yml`), using the indexed labels FluentBit attaches (`tenant_id`, `service`, `call_id`, `trace_id`, `level` -- Sprint-027.md's own list calls the last one `severity`; the real `StructuredLogger` JSON field, src/libs/observability/logger.py, is named `level`, so that's the label FluentBit actually indexes -- see `monitoring/logging/fluentbit.conf`'s own comment).

## Find every log line for a specific call

```logql
{call_id="test-call-001"}
```

Returns logs from every service that touched the call (Media Gateway, ASM, VAD, STT, LLM, TTS, ConversationEngine) — this is the query Sprint-027.md's own AC ("logs searchable by call_id") validates.

## All errors for a tenant

```logql
{tenant_id="<uuid>", level="error"}
```

## Errors across the whole platform in the last hour

```logql
{level="error"} |= "" [1h]
```

## A specific service's logs, tailing live

```logql
{service="conversation-engine"}
```

## Cross-reference a trace: pivot from a Jaeger trace_id back to logs

```logql
{trace_id="<trace-id-from-jaeger>"}
```

(The reverse direction — log line → Jaeger trace — is wired via `datasources.yml`'s `derivedFields` on the Loki datasource: any log line containing `trace_id=<hex>` renders a "TraceID" link straight into the Jaeger UI.)

## Policy denials for a domain, last 24h

```logql
{service="policy-engine"} | json | domain="RBI" | outcome="deny"
```

## Count of DLQ routing events by queue

```logql
sum by (queue_name) (count_over_time({service=~".*"} |= "routed to DLQ" [1h]))
```

## Confirm PII redaction (should return zero matches — used as a compliance check, not a normal query)

```logql
{level=~".+"} |~ `\d{4}\s?\d{4}\s?\d{4}` # Aadhaar-shaped digit sequence
```

Any hits here indicate an `PIIRedactor` gap (Sprint-020) and should be filed as a bug, not treated as expected output.
