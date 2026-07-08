path "secret/data/voiceos/*" {
  capabilities = ["read", "create", "update"]
}

path "secret/metadata/voiceos/*" {
  capabilities = ["read", "list", "delete"]
}

path "transit/datakey/plaintext/*" {
  capabilities = ["create", "update"]
}

path "transit/decrypt/*" {
  capabilities = ["create", "update"]
}

path "transit/keys/*" {
  capabilities = ["create", "read", "update", "delete"]
}
