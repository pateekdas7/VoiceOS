output "system_key_arn" {
  value = aws_kms_key.system.arn
}

output "system_key_alias" {
  value = aws_kms_alias.system.name
}

output "tenant_kek_skeleton_policy_json" {
  value = data.aws_iam_policy_document.tenant_kek_skeleton.json
}
