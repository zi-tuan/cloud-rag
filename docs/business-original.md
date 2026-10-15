# UAV 云边通信与航线防护

本仓库维护云端任务 API、Jetson Edge Agent、Pi 执行器、共享通信协议以及独立密码服务。Python 包从仓库根目录导入；源码只有一份，按进程分别构建镜像。

```text
cloud/cloud_api/             云端 API、任务、航线、认证会话和恢复
jetson/edge_agent/           边端控制、遥测、风险守卫、轮换、工作负载回收
jetson/pi_runner/            Unix socket 执行服务及 Pi/MCP 项目配置
services/crypto_service/    AES/SM4 共用的 Controller、Service、Repository
services/auth_service/      Ed25519/RSA/SM2 共用的认证 HTTP 服务
shared/communication/       消息信封、认证协议、客户端与公共状态组件
shared/adapters/            MAVLink、仿真飞控及可信状态适配器
deploy/cloud/               云端清单渲染输入与端点资料
deploy/jetson/              Jetson 清单渲染输入、Pi 构建环境清单
deploy/infrastructure/      本项目管理的 K3s、Harbor、隧道部署说明
deploy/scripts/             当前构建、渲染、初始化和运维工具
deploy/images/              整理前的现场镜像锁，供历史追溯
docs/evidence/deploy/       原始现场验收 JSON、JSONL 与截图
docs/archive/deploy/        依赖旧布局的一次性构建、差量发布与验收脚本
tests/                     全套回归测试
```

每个进程目录都包含 `Dockerfile`、`requirements.txt` 和 `config/`。AES 与 SM4 采用同一个 `crypto-service` 镜像，分别注入套件配置和状态目录；三种认证算法同样共用 `auth-service` 镜像，分别注入身份配置与密钥材料。没有旧导入路径兼容层。

## 开发与测试

以下命令均从仓库根目录运行，使用 Python 3.12：

```powershell
python -m pip install -r requirements-dev.txt
python -X utf8 -m unittest discover -s tests
git diff --check
```

回归覆盖 A→C→B→C→A 连续航点进度、两次接近风险点、首尾坐标相同、风险外稳定退出、会话恢复和切换确认后的旧容器回收。运行行为由确定性执行器验证，模型只提供受限的结构化规划。

## 模块与容器入口

- `cloud-api`：`cloud/cloud_api/Dockerfile`，入口 `python -m cloud.cloud_api.api`，云端容器 `uav-cloud-api`，目标 Linux AMD64。
- `edge-agent`：`jetson/edge_agent/Dockerfile`，入口 `python -m jetson.edge_agent.runtime`，Jetson 容器 `uav-edge-agent`，目标 Linux ARM64。
- `pi-runner`：`jetson/pi_runner/Dockerfile`，入口 `python -m jetson.pi_runner`，Jetson 容器 `pi-runner`，目标 Linux ARM64；包含固定 Node 22.23.2 可执行文件。
- `crypto-service`：`services/crypto_service/Dockerfile`，入口 `python -m services.crypto_service`，对应云端 `uav-cloud-aes`/`uav-cloud-sm4` 和边端 `aes-service`/`sm4-service`，支持 AMD64、ARM64。
- `auth-service`：`services/auth_service/Dockerfile`，入口 `python -m services.auth_service`，对应各端 `auth-ed25519`/`auth-rsa`/`auth-sm2`，支持 AMD64、ARM64；RSA/SM2 需要 OpenSSL 3。

`pi-runtime` 是挂载外部 Pi 源码的构建和交互环境，使用固定上游 Node 镜像及 `deploy/jetson/pi-runtime.yaml`，没有本项目独立业务入口。Pi 固定提交 `898ab804050730e9dcefb4443875d5a932aa6a32`，CLI 0.87.1；源码位于 Jetson `/home/uav/uav-agent-mvp/src/pi`，挂载到 `/workspace/pi`。模型快照补齐及 `build:offline` 属于外部 Pi 源码准备，详见历史部署记录。PiRunner 复用该构建产物以及外部 Aivo、Kubernetes MCP 二进制，不在这里复制第三方源码。

`uav-core-service` 在保存的现场审计中仅执行打印启动信息和 `tail -f /dev/null`，没有查到本仓库的业务源码或构建链；因此不为它新建空源码模块或 Dockerfile。此结论来自历史审计，未在本次重构中重启或删除该容器。

## Docker 构建

构建上下文统一为仓库根目录 `.`，使用根目录 `.dockerignore` 排除临时文件、凭据、数据库、验收证据和测试。以下命令在原生 Linux 目标架构或具备相应 Buildx 仿真的构建机执行：

```powershell
docker buildx build --platform linux/amd64 -f cloud/cloud_api/Dockerfile -t uav/cloud-api:layout --load .
docker buildx build --platform linux/arm64 -f jetson/edge_agent/Dockerfile -t uav/edge-agent:layout --load .
docker buildx build --platform linux/arm64 -f jetson/pi_runner/Dockerfile -t uav/pi-runner:layout --load .
docker buildx build --platform linux/amd64 -f services/crypto_service/Dockerfile -t uav/crypto-service:layout-amd64 --load .
docker buildx build --platform linux/arm64 -f services/crypto_service/Dockerfile -t uav/crypto-service:layout-arm64 --load .
docker buildx build --platform linux/amd64 -f services/auth_service/Dockerfile -t uav/auth-service:layout-amd64 --load .
docker buildx build --platform linux/arm64 -f services/auth_service/Dockerfile -t uav/auth-service:layout-arm64 --load .
```

所有 Dockerfile 支持 `--build-arg PYTHON_BASE=python@sha256:...`。本次离线构建使用的 Python 3.12 基础镜像摘要为 AMD64 `1aaa65a85fda306ffb8b910824d4e93bdce61e212c7e87168123ea3073b41a1a`、ARM64 `eb5be8e5b4d0a159c237946bbdd06356dda5d19c30fc4f7843e8046d3a590333`；复现本次基础环境时分别覆盖默认 tag。PiRunner 的 Node 基础镜像在 Dockerfile 中固定 ARM64 digest。

本机没有 Docker CLI。已实际生成并校验七个目标 OCI 镜像，未执行 Linux 容器启动或跨架构运行。具体验证记录见 [本次整理验收](docs/service-layout.md)。

## 离线构建与清单生成

统一离线构建器读取模块 Dockerfile 的源码 COPY、环境与入口，并依据相同 `requirements.txt` 用 pip 解析目标架构 wheel；不再维护多份人工复制的源码清单。它校验基础 OCI 的 blob、架构和解压层摘要，也核对 PiRunner 的固定 Node 镜像。输出及新镜像锁位于忽略目录 `deploy/.cache/service-layout/`。

已有基础 OCI 缓存时运行：

```powershell
python -m deploy.scripts.prepare_wheels
python -m deploy.scripts.build_offline_images
python -m deploy.scripts.verify_layout_images
python -m deploy.scripts.render_layout
```

首次准备基础缓存可使用 `deploy.scripts.fetch_oci`，将上述 Python digest 分别保存为 `deploy/.cache/python-amd64.tar`、`python-arm64.tar`，Node 固定 digest 保存为 `node-arm64.tar`。例如：

```powershell
python -m deploy.scripts.fetch_oci library/python sha256:1aaa65a85fda306ffb8b910824d4e93bdce61e212c7e87168123ea3073b41a1a deploy/.cache/python-amd64.tar --architecture amd64
python -m deploy.scripts.fetch_oci library/node sha256:f71fb9ca71b1b47d4d1a009af78147ed6cdc74f9c7cfc36cbde78ff985169051 deploy/.cache/node-arm64.tar --architecture arm64
```

`render_layout` 只生成本地 `cloud.yaml`、`jetson.yaml` 和登记表，分别绑定新的模块镜像与 digest，同时生成挂载的服务登记 ConfigMap。`deploy/cloud/`、`deploy/jetson/` 中控制服务的基础清单是渲染输入，不能直接下发。新登记默认 `registry_verified=false`；完成所有新镜像的 Harbor 推拉验证后，才可显式使用 `--registry-verified` 生成可激活登记。

身份配置默认使用示例公开元数据。正式发布应通过 `--auth-config-dir <目录>` 提供原主机生成的 `edge/ed25519.json`、`edge/rsa.json`、`edge/sm2.json` 和对应 `cloud/` 文件。私钥、CA 签发私钥和服务令牌不进入源码、构建上下文或镜像。发布前需准备文档列明的 Secret、既有数据库、Pi/Aivo/MCP 文件和主机目录；本次没有执行发布。

更多配置、状态目录及分支整合说明见 [目录整理说明](docs/service-layout.md)。
