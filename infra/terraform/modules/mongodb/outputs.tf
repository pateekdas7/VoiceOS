output "member_private_ips" {
  value = aws_instance.replica[*].private_ip
}

output "security_group_id" {
  value = aws_security_group.mongodb.id
}
