#!/bin/bash
set -eu
hostname
docker image inspect --format '{{.RepoTags}} {{.Id}}' uav-cloud-control:c40be3fcd7e9
df -h /
