#!/bin/bash
set -eu
k3s crictl pull 47.109.49.98:30881/uav-mvp/crypto-service@sha256:992bb941ccae1f8d81c6399d6d4eca738c809f4eeec0dc1e45b136d73efbb238
k3s kubectl get deployments -n uav-mvp
