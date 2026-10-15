#!/bin/bash
set -u
curl -sSI --connect-timeout 8 https://getaivo.dev/dl/v0.49.9/aivo-linux-arm64 | head -5
curl -sSI --connect-timeout 8 https://registry-1.docker.io/v2/ | head -5
command -v node || true
command -v npm || true
command -v skopeo || true
command -v buildah || true
docker version --format '{{.Server.Version}}'
