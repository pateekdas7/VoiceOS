storage "file" {
  path = "/opt/vault/data"
}

listener "tcp" {
  address     = "127.0.0.1:8200"
  tls_disable = "true"
}

disable_mlock = true
api_addr      = "http://127.0.0.1:8200"
ui            = false
