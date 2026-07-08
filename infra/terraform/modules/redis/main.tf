# redis module -- managed Redis (ElastiCache), Multi-AZ (Sprint-026.md, V7 Ch2)
#
# Mirrors the CPU node's hardened native-Redis configuration (TT-002:
# appendonly persistence, volatile-ttl eviction) at the managed-service
# layer: ElastiCache's own snapshot/AOF-equivalent (`snapshot_retention_limit`)
# and `maxmemory-policy` parameter serve the same role here.

resource "random_password" "auth_token" {
  length  = 32
  special = false # ElastiCache AUTH tokens reject several punctuation characters.
}

resource "aws_secretsmanager_secret" "redis_auth_token" {
  name        = "${var.name_prefix}-redis-auth-token"
  description = "ElastiCache Redis AUTH token for ${var.name_prefix}."
  tags        = var.tags
}

resource "aws_secretsmanager_secret_version" "redis_auth_token" {
  secret_id     = aws_secretsmanager_secret.redis_auth_token.id
  secret_string = random_password.auth_token.result
}

resource "aws_elasticache_subnet_group" "this" {
  name       = "${var.name_prefix}-redis"
  subnet_ids = var.private_subnet_ids
  tags       = var.tags
}

resource "aws_security_group" "redis" {
  name_prefix = "${var.name_prefix}-redis-"
  description = "ElastiCache Redis -- ingress limited to the EKS node security group only (deny-all default + explicit allow-list)."
  vpc_id      = var.vpc_id

  dynamic "ingress" {
    for_each = var.allowed_security_group_ids
    content {
      description     = "Redis from EKS nodes"
      from_port       = 6379
      to_port         = 6379
      protocol        = "tcp"
      security_groups = [ingress.value]
    }
  }

  # No egress block: ElastiCache is a managed service that does not need to
  # initiate outbound connections through this ENI -- Terraform's default
  # (no egress block = no egress rules) is the correct, tightest posture
  # here, not a placeholder allow-all (found and fixed running a real
  # `tfsec` scan -- see CHANGELOG.md's Sprint-026 entry).

  tags = merge(var.tags, { Name = "${var.name_prefix}-redis-sg" })
}

resource "aws_elasticache_parameter_group" "this" {
  name   = "${var.name_prefix}-redis-params"
  family = "redis7"

  parameter {
    name  = "maxmemory-policy"
    value = "volatile-ttl" # matches CPU_NODE_STATE.md's TT-002-hardened native config.
  }

  tags = var.tags
}

resource "aws_elasticache_replication_group" "this" {
  replication_group_id = "${var.name_prefix}-redis"
  description          = "VoiceOS ${var.name_prefix} Redis -- hot state only, never authoritative (V3 Ch4)."

  engine         = "redis"
  engine_version = "7.1"
  node_type      = var.node_type
  port           = 6379

  parameter_group_name = aws_elasticache_parameter_group.this.name
  subnet_group_name    = aws_elasticache_subnet_group.this.name
  security_group_ids   = [aws_security_group.redis.id]

  num_cache_clusters         = var.multi_az ? 2 : 1
  automatic_failover_enabled = var.multi_az
  multi_az_enabled           = var.multi_az

  at_rest_encryption_enabled = true
  transit_encryption_enabled = true
  auth_token                 = random_password.auth_token.result

  snapshot_retention_limit = 5 # AOF-equivalent durability, mirroring TT-002's appendonly hardening.

  tags = var.tags
}
