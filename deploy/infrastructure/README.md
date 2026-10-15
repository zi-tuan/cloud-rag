# 本项目管理的基础设施部署资料

`k3s/namespace-rbac.yaml` 是本项目命名空间与受限 Agent 权限。K3s 安装、cgroup、网络和访问检查工具位于 `deploy/scripts/install-k3s.sh`、`fix-cgroup-driver.sh`、`fix-k3s-network.sh` 及 `inspect-k3s-access.sh`，通过现有 SSH 运维入口按明确操作执行。

Harbor 的项目镜像访问和 HTTP 代理资料由 `deploy/scripts/setup_harbor_http.py`、`configure-registry-tunnel.sh` 与对应检查脚本维护。NPS/云端隧道由 `provision_cloud_tunnel.py`、`install-cloud-tunnel.sh` 和只读检查脚本维护；这些脚本仍含既有环境地址，执行前需按部署环境核对。相关故障与证书操作见 `docs/cloud-network-access-2026-10-02.md`、`docs/tls-certificate-renewal.md`。

没有复制 Harbor、Apollo、new-api、K3s 或 NPS 的第三方源码。历史发布/迁移脚本保存在 `docs/archive/deploy/`；当前独立模块构建与清单渲染使用根 README 指定的入口。
