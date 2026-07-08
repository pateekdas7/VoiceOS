# kms module -- system KMS key + per-tenant key skeleton (Sprint-026.md, V1 Ch26)
#
# "KMS key per tenant skeleton + system keys": the *system* key is a real,
# always-present KMS CMK (envelope-encryption root for RDS/S3/EBS at-rest
# encryption). Per-tenant keys are provisioned on-demand at tenant-activation
# time (TenantProvisioner.ensure_kek(), Sprint-021) -- this module defines the
# *alias naming convention and IAM policy skeleton* per-tenant keys must
# follow, not a static list of them (the tenant set is dynamic, unknown to
# Terraform). This mirrors the CPU node's existing Vault Transit KEK pattern
# (`tenant-<uuid>`) at the AWS-KMS layer for the target cloud deployment.

resource "aws_kms_key" "system" {
  description             = "${var.name_prefix} system CMK -- RDS/S3/EBS at-rest encryption root."
  deletion_window_in_days = 30
  enable_key_rotation     = true

  tags = merge(var.tags, { Name = "${var.name_prefix}-system-key" })
}

resource "aws_kms_alias" "system" {
  name          = "alias/${var.name_prefix}-system"
  target_key_id = aws_kms_key.system.key_id
}

# Per-tenant KEK skeleton policy: a dedicated IAM policy document that
# TenantProvisioner's AWS-KMS adapter (AWSKMSAdapter, src/libs/encryption/)
# attaches when it creates `alias/${name_prefix}-tenant-<tenant_id>` keys at
# runtime via the KMS CreateKey API -- Terraform does not create these keys
# itself (see module docstring above).
data "aws_iam_policy_document" "tenant_kek_skeleton" {
  statement {
    sid    = "AllowTenantKeyAdministration"
    effect = "Allow"
    principals {
      type        = "AWS"
      identifiers = ["*"]
    }
    actions = [
      "kms:Create*", "kms:Describe*", "kms:Enable*", "kms:List*",
      "kms:Put*", "kms:Update*", "kms:Revoke*", "kms:Disable*",
      "kms:Get*", "kms:Delete*", "kms:TagResource", "kms:UntagResource",
      "kms:ScheduleKeyDeletion", "kms:CancelKeyDeletion",
    ]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "aws:PrincipalTag/voiceos-role"
      values   = ["tenant-kek-administrator"]
    }
  }
}
