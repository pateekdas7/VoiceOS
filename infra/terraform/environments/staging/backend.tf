# staging environment -- remote state backend (IaC-3: S3 + DynamoDB state lock)

terraform {
  backend "s3" {
    bucket         = "voiceos-terraform-state"
    key            = "staging/terraform.tfstate"
    region         = "ap-south-1"
    dynamodb_table = "voiceos-terraform-locks"
    encrypt        = true
  }
}
