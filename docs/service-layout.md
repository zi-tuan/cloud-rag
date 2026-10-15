# 服务目录整理与验证

## 分支来源

新分支 `codex/service-layout` 基于 `fda06c2`，完整继承原航线分支。该基点已包含 `master` 的 `0ed31e5`、FastAPI 分支的 `2ba0cfc`，以及认证组合、SQLAlchemy 持久化、航线守卫、风险等待、往返航点和服务回收实现。

2026-10-11 经 HTTPS 拉取远端后，核查 `origin/codex/runtime-only` 到 `4163b73` 的五个独有提交，未更换另一个 worktree 的分支：

- `410ebb8`：精简部署与测试。未采用删除操作；这些内容是当前回归和部署追溯所需。
- `ff98dae`：环境和 Pi 源码说明。吸收固定 Pi 提交、外部源码挂载、Aivo/MCP 依赖及持久目录边界；历史核对结果仍视为当时证据。
- `1004adc`：云边职责分组。吸收按部署位置和进程组织的方向；共享认证、加密和协议只保留一套源码，避免两端复制。
- `b13f908`：选择性移植严格的 `ProofRequest`/`VerifyRequest` 和统一的 OpenAPI 400 校验描述，放入 `shared/communication/`，并补充 HTTP/客户端契约回归。旧工具定义仅覆盖当时的服务选择，缺少当前防护计划、保护等级和最新参数约束，未直接替换现有 MCP 工具实现。
- `4163b73`：三层加密服务和 ORM。当前基点已有相应分层与 ORM，并额外支持会话租约、有效期及定时退役；旧分支实现缺少这些新功能，未覆盖现有实现。

参考项目 `D:/Graduate Programs/cloud-net-end/server/crypto_service` 将入口、配置、依赖和 Dockerfile 聚合在模块内。本次采用该组织方式，保持现有项目的共享实现及独立进程边界，没有拷贝该项目或基础设施的第三方源码。

## 配置注入与持久目录

- 云端 API：`UAV_CLOUD_CONFIG` 指向 `/config/api.json`；配置来自 ConfigMap 或只读配置卷。`/state` 保存任务、切换、认证、配额、航线和飞行记录；不要删除后配合旧密钥重新初始化。身份客户端仅挂载三个认证服务的调用令牌。
- Edge Agent：`UAV_EDGE_CONFIG` 指向 `/opt/uav/config/edge.json`；`/state` 保存执行日志、切换和执行数据库。`/run/uav-pi` 与 PiRunner 共享 Unix socket。MAVLink 数据和操作员仿真控制地址由配置注入。
- PiRunner：`UAV_PI_RUNNER_CONFIG` 指向 `/opt/uav/config/pi-runner.json`；`/pi-state` 保存执行日志、回执与会话证据；`/home/node`、`/workspace/project`、`/workspace/pi` 以及 `/opt/uav/bin` 继续由持久卷/主机目录提供。Pi、Aivo、MCP 与模型凭据不打进本项目镜像。
- 受控 MCP：由 Pi 配置以 `python -m jetson.edge_agent.mcp_guard` 启动，`UAV_MCP_GUARD_CONFIG` 指向挂载配置。登记目录和激活模板由渲染器生成并只读挂载到 `/opt/uav/catalog`，认证登记嵌入同一份 guard 配置；构建源码内的历史登记不会覆盖本次渲染结果。
- 密码服务：`UAV_CRYPTO_CONFIG` 默认 `/config/crypto.json`；云端配置设置 `role=cloud`，边端设置 `role=edge`。AES/SM4 分别挂载自己的 `/state/messages.db`，共用代码而不共用 nonce 计数器或接收窗口。根密钥和服务调用令牌从 `/keys` 只读挂载。配置示例 `initialize=false`，首次部署必须显式置备新密钥、新 epoch 和状态，恢复部署保留原数据库。
- 认证服务：`UAV_AUTH_CONFIG` 默认 `/config/auth.json`；配置按 `role` 和 `policy` 分开，私钥、注册对端材料及令牌从 `/identity` 只读挂载。RSA/SM2 的 CA、CRL 与证书路径也在独立配置中；临时验签数据使用 `/tmp` 内存卷。示例的有效期和版本必须替换为实际签发结果。

所有镜像使用 UID/GID `1000:1000`。宿主持久目录需预先建立并赋予该身份读写权限；只读密钥与令牌卷使用适当组权限。控制进程的首次数据库初始化使用 `python -m deploy.scripts.initialize_control <配置> --role cloud|edge`，工具拒绝覆盖既有状态。PiRunner 的执行数据库也需显式初始化；可在已准备 `/pi-state` 的容器中运行 `python -c "from jetson.edge_agent.executor import ExecutionJournal; ExecutionJournal('/pi-state/executions.db', initialize=True)"`，前提是确认该目录属于全新实例。

渲染清单引用以下 Secret，内容由原主机既有材料注入：云端 `uav-cloud-identity`，Jetson `crypto-identity`；两端各自的 `uav-auth-{ed25519,rsa,sm2}-identity` 与 `uav-auth-{ed25519,rsa,sm2}-client`。`*-client` 只保存 `service.token`。渲染器移除旧的单策略 `uav-auth-client` 挂载，改为三个策略各自的调用令牌路径。命名空间分别为云端 `uav`、边端 `uav-mvp`。

渲染器保留原部署的资源、探针、只读根文件系统、主机目录和账号约束；将镜像、架构与入口绑定到独立模块，同时让登记模板与实际 Pod 配置一致。它默认不会声称新镜像已经通过 Harbor 核验。云端配置仍需按实际控制台地址调整登录验证 URL；Jetson 的数据/仿真地址和云端 URL 同样属于环境配置。

## 构建来源与历史资料

模块 Dockerfile 是当前镜像的源码、入口和依赖输入。统一离线构建器读取这些输入，在本机不能运行 Docker/ARM64 时组装 OCI；它只支持本项目使用的固定 Dockerfile 指令形式，新增阶段、动态 COPY 或构建步骤时必须同步扩展并验证构建器。离线装配不等于执行了 Dockerfile，也不替代 Linux 容器运行验收。

原四套手工镜像构建器、依赖旧包布局的差量发布脚本和旧镜像验收任务归档到 `docs/archive/deploy/`，保留原始内容以便审计。它们不能作为当前布局的构建或发布入口。历史镜像锁仍保留原摘要和路径描述，不把新入口套到旧镜像上；原验收 JSON/JSONL/截图只移动位置，不改写当时记录。

`pi-runtime` 与业务 `pi-runner` 分开：前者使用固定上游 Node 镜像挂载 Pi 源码进行构建/交互，后者提供持久执行日志和本机 socket 服务。保存的 `deployed-roles-audit-20261010.json` 显示 `uav-core-service` 仅为空闲 Node 容器；本仓库不制造对应业务代码或镜像。Harbor、NPS/隧道和 K3s 只维护确属本项目的部署工具；Apollo/new-api 未找到本项目管理的独立部署输入，因此没有添加第三方源码或虚构配置。

## 本次验证

- 整理前全套 182 项 unittest 通过。
- 整理后全套 186 项 unittest 通过，其中新增四项验证独立模块输入、清单/登记/隔离模板一致性、严格认证请求与 OpenAPI、客户端网络调用前校验。
- 五个模块的七个目标 OCI 均已实际生成；所有 blob 大小/摘要、目标 Linux 架构、解压层摘要、入口、当前源码哈希及仓库内导入依赖通过检查。
- 基础镜像、wheel 版本及新镜像摘要保存在忽略目录 `deploy/.cache/service-layout/`，可交给后续原生 Linux 验收。紧凑验收记录见 `docs/evidence/service-layout/validated-images.json`。
- 本地缺少 Docker CLI，没有执行 Docker Buildx 构建、启动 Linux/ARM64 容器、推送 Harbor 或应用 Kubernetes 清单。
- 用户启动仿真后，只读 TCP 连接 `192.168.8.110:5763` 成功；没有下发飞行指令或修改线上工作负载。

以上为本地整理阶段的验证记录；随后经用户授权完成的现场发布见下节。

## 2026-10-11 现场发布

发布以 `5cd0b94` 为基础，并包含 `83aaf9b` 的 PiRunner 依赖修复。实际 MCP 读取发现 `GuardedOrchestrator.resource()` 延迟导入 `yaml`，因此在 PiRunner requirements 中补充固定的 `PyYAML==6.0.3`，重新构建并验证 PiRunner 和包含该目录的 Edge Agent 镜像。

- 保留现场部署方式：云端六个 Docker 容器、Jetson 七个业务 Deployment。仓库 `render_layout.py` 输出的云端 Kubernetes 清单仍是模板，本次没有应用，也没有迁移云端到 Kubernetes。
- 发布七个目标架构的不可变 OCI 到现有私有 Harbor 项目，并核验原拉取 robot 能读取各 manifest。Jetson 在原生 ARM64 上实际拉取并运行新镜像；云端原有 Docker daemon 代理不可用，因此将同一 OCI 转为 Docker 归档导入，核对 Docker image ID 与 OCI config digest 后运行，没有修改全局代理设置。
- 云端沿用原配置、网络、端口、安全约束、资源限制、应用环境变量和数据挂载。边端从实际 Deployment 与 ConfigMap 生成差异，保留副本数：SM4/SM2 各 1，AES/Ed25519/RSA 各 0，PiRunner/Edge Agent 各 1；更新登记中的镜像与受控 MCP 模块入口，并使用版本化 ConfigMap 挂载登记文件。
- 在两个实际架构上各通过 16 项加密/持久化测试与 4 项 SM2 证书测试；原生模块加载、PiRunner Node/YAML 运行时检查、实际受控 MCP 读取、Unix socket 查询及生产容器源码哈希核对通过。依赖修复后本地 8 项 PiRunner 回归与 4 项服务布局回归通过。
- 第一次云端发布因验收脚本错误使用 API 未提供的 `/health` 路径而自动回滚；修正为带认证的 `/api/overview` 后重新发布成功。
- 发布前没有活动任务或航线，设备通信已离线，边端留有未确认的旧轮换事务。发布后通过现有 `/api/recovery` 流程恢复同等级的 SM2/SM4-GCM/lightweight 会话，双向认证和真实加密业务回执确认，设备在线、遥测恢复刷新。没有提交飞行任务或飞控指令。
- 原身份材料、持久数据库和 nonce/重放窗口继续复用。云端停止的原容器与两端 SQLite 备份保留用于恢复；代码回滚不能回退计数器数据库。发布目录为 `/home/uav/uav-agent-mvp/deploy/service-layout-5cd0b94`。验收 Pod、临时验证 ConfigMap 和临时拉取 Secret 已清理，`pi-runtime` 与 `uav-core-service` 沿用原部署。

结构化发布记录见 [deployed-20261011.json](evidence/service-layout/deployed-20261011.json)。原本地镜像验证记录保留原样，最终部署镜像摘要以本次发布记录为准。
