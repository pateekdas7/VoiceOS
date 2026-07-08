output "endpoint" {
  value = aws_db_instance.this.endpoint
}

output "connection_string_no_password" {
  description = "Postgres DSN with the password omitted -- fetch the real credential from Secrets Manager (secret ARN below)."
  value       = "postgresql://${aws_db_instance.this.username}@${aws_db_instance.this.endpoint}/${aws_db_instance.this.db_name}"
}

output "secret_arn" {
  value = aws_secretsmanager_secret.postgres_master.arn
}

output "security_group_id" {
  value = aws_security_group.postgres.id
}
