output "cluster_endpoint" {
  value = module.voiceos.cluster_endpoint
}

output "cluster_name" {
  value = module.voiceos.cluster_name
}

output "postgres_endpoint" {
  value = module.voiceos.postgres_endpoint
}

output "redis_primary_endpoint" {
  value = module.voiceos.redis_primary_endpoint
}

output "object_storage_buckets" {
  value = module.voiceos.object_storage_buckets
}

output "ecr_repository_urls" {
  value = module.voiceos.ecr_repository_urls
}
