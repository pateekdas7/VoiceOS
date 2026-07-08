# VoiceOS v2 -- Root Terraform outputs (Sprint-026)

output "cluster_endpoint" {
  description = "EKS API server endpoint."
  value       = module.kubernetes.cluster_endpoint
}

output "cluster_name" {
  description = "EKS cluster name."
  value       = module.kubernetes.cluster_name
}

output "cluster_certificate_authority_data" {
  description = "Base64-encoded EKS cluster CA certificate, for kubeconfig generation."
  value       = module.kubernetes.cluster_certificate_authority_data
  sensitive   = true
}

output "postgres_connection_string" {
  description = "Postgres DSN (password omitted -- see Vault/Secrets Manager for the credential itself, per CLAUDE.md Security)."
  value       = module.database.connection_string_no_password
  sensitive   = true
}

output "postgres_endpoint" {
  description = "RDS Postgres endpoint (host:port)."
  value       = module.database.endpoint
}

output "redis_primary_endpoint" {
  description = "ElastiCache Redis primary endpoint."
  value       = module.redis.primary_endpoint
}

output "mongodb_replica_set_endpoints" {
  description = "Self-hosted MongoDB replica set member private IPs (V7 Ch2: MongoDB Atlas or self-hosted replica set -- self-hosted here, no Atlas account exists for this project, same precedent as Vault/local-disk object storage elsewhere in this codebase)."
  value       = module.mongodb.member_private_ips
}

output "object_storage_buckets" {
  description = "S3 bucket names: audio recordings, exports, backups."
  value       = module.object_storage.bucket_names
}

output "ecr_repository_urls" {
  description = "ECR repository URL per VoiceOS service."
  value       = module.registry.repository_urls
}

output "system_kms_key_arn" {
  description = "System KMS key ARN (envelope-encryption root, mirrors the CPU node's Vault Transit KEK)."
  value       = module.kms.system_key_arn
}
