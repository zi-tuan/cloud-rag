# 云端部署与逐层连通性验收（待现场执行）

仅上传 SQLite 文件不会产生接口。必须部署与知识版本匹配的 RAG 代码、Python 3.12/FTS5、数据库只读挂载和凭据。以下复用项目现有 Kubernetes/K3s 体系，不新建 systemd 服务；当前尚未访问真实云端或修改任何集群。

## 开始前集中确认

提供：云主机/SSH 别名与端口（不发送密码或私钥）；云端 kubeconfig 使用位置、context、namespace、CPU 架构；已有镜像仓库与拉取方式；可用 PVC/StorageClass、容量和上传方法；现有 HTTPS 入口及域名/证书终止位置；Jetson/树莓派各自实际运行组件、edge namespace、PiRunner Pod；隧道监听地址、宿主机和 Pod 的访问路径。

原仓库保存了历史地址/端点/镜像记录，仅用于理解部署工具，未经确认不会拿来执行部署。生产模型、Pi 0.87.1 对应固定外部构建、Aivo、Kubernetes MCP v0.0.67 仍需使用现场已有文件，并单独验证它们与当前配置兼容。

## 1. 生成与校验本地材料

执行：在融合目录运行 `python manage.py release`。输出 `dist/rag-<release_id>/`、同名 zip 和 `.sha256`。

预期：包内有 `uav_rag/`、`cloud/rag/Dockerfile`、`data/openssl.sqlite3`、文档、许可证、渲染器、探测脚本和 `release-manifest.json`。逐文件哈希及知识版本均通过校验。用 `python -m tools.release --verify dist/rag-<release_id>` 再检查。

失败检查：先执行 `manage.py doctor`；数据库 schema、文档版本、源码版本必须一致。发布使用 sqlite3 backup API，不能通过直接拷贝活跃写入数据库或只拷贝主文件遗漏 WAL 来发布。

## 2. 上传代码/镜像和数据库

执行：确认远端目录后 `scp dist/rag-<release_id>.zip <SSH_ALIAS>:<UPLOAD_DIR>/`，同样传 `.sha256`。云端 `sha256sum -c rag-<release_id>.sha256`，解压到新的独立版本目录，再执行 `python3 -m tools.release --verify .`。

预期：两次哈希检查通过。包不含凭据；哈希只证明完整性，SSH/可信传输负责来源身份。

失败检查：路径、磁盘容量、上传完成状态和文件校验，不覆盖旧版本。

执行：在具备 Linux 容器构建能力的机器，从项目根目录或解压包根目录运行：

```sh
docker buildx build --platform linux/amd64 -f cloud/rag/Dockerfile \
  -t <REGISTRY>/uav-rag:<RELEASE_ID> --push .
```

预期：得到实际 registry manifest digest。以 `<REGISTRY>/uav-rag@sha256:<DIGEST>` 渲染。云端若为 ARM64 必须相应调整，边端 PiRunner 为 ARM64；原密码/认证镜像分别按云边架构构建。RAG 镜像不包含数据库或令牌。

失败检查：buildx 平台、基础镜像可达性、registry 登录及 imagePullSecret。不要将文件压缩包 SHA256 当成 OCI digest。本次 Windows 无 Docker CLI，尚未构建/运行这些镜像。

数据库持久化：采用确认后的现有 PVC，目录布局为 `<release_id>/openssl.sqlite3`。先通过受控上传 Pod 将数据库写到新版本目录，校验 SHA256 后再切换工作负载；UID/GID 1000 须可读、fsGroup 1000，数据库及目录不对其他用户可写。RAG 的 `/data` 只读；不要向运行 Pod 的只读挂载直接复制。

可检查的上传 Pod 模板是 `deploy/rag-upload.example.yaml`。替换 namespace、PVC、已推送镜像 digest；它与 RAG 使用同一架构/节点可挂载的卷。执行：

```sh
kubectl --context <CLOUD_CONTEXT> apply -f <REVIEWED_UPLOAD_POD>
kubectl -n <NAMESPACE> wait --for=condition=Ready pod/rag-upload --timeout=120s
kubectl -n <NAMESPACE> exec rag-upload -- python -c "from pathlib import Path; Path('/upload/<RELEASE_ID>').mkdir(exist_ok=False)"
kubectl -n <NAMESPACE> cp data/openssl.sqlite3 rag-upload:/upload/<RELEASE_ID>/openssl.sqlite3
kubectl -n <NAMESPACE> exec rag-upload -- python -c "import hashlib; print(hashlib.file_digest(open('/upload/<RELEASE_ID>/openssl.sqlite3','rb'),'sha256').hexdigest())"
```

预期：远端数据库哈希与 manifest 的 `data/openssl.sqlite3` 相同。失败检查 PVC 绑定、RWO 跨节点限制、目录 UID/GID、上传 Pod 的 tar（kubectl cp 所需）；不要改为全局 777。上传成功且哈希确认后删除上传 Pod，保留卷与旧版本目录。

## 3. 凭据、清单与启动

执行：在受控主机生成至少 32 字符随机令牌文件，权限 600，不在聊天、参数或日志中显示内容。用文件创建 Secret：

```sh
kubectl -n <NAMESPACE> create secret generic uav-rag-token --from-file=service.token=<TOKEN_FILE>
python -m deploy.scripts.render_rag --namespace <NAMESPACE> \
  --image <REGISTRY>/uav-rag@sha256:<DIGEST> --claim <PVC> \
  --release <RELEASE_ID> --architecture amd64 --output rag-reviewed.json
kubectl --context <CLOUD_CONTEXT> apply -f rag-reviewed.json
kubectl -n <NAMESPACE> rollout status deployment/uav-rag --timeout=180s
```

预期：Deployment 一个副本，以非 root 用户启动；监听容器 `0.0.0.0:8787`；ClusterIP `uav-rag:8787`；Secret 0440 只读。HTTP 健康检查返回当前索引元数据，知识查询仍要求 Bearer 鉴权。health 是现有非敏感公开存活接口，健康通过不能证明鉴权查询通过。

失败检查：`kubectl describe pod` 的调度/挂载/拉取事件和应用日志；数据库 schema、版本、UID/GID 与 Secret 路径。不要绕过 Guard、关闭鉴权或重复盲重启。

已有私有仓库须给 Pod 绑定现场 imagePullSecrets；现有 NetworkPolicy 应只允许确认的 HTTPS 入口/受信调用路径访问 8787，并允许返回流量。先检查原策略再追加，不删除保护策略以求连通。

## 4. 按层验证云端和边端

每步保存命令、时间、版本与实际结果；下层失败就定位该层，不直接运行服务切换。

| 层 | 执行什么 | 预期 | 失败检查 |
|---|---|---|---|
| Pod/数据库 | rollout、describe、logs | 进程正常、schema 3、正确 revision | 挂载路径、权限、镜像架构、数据库版本 |
| 云端本机 | `kubectl port-forward svc/uav-rag 18787:8787 --address 127.0.0.1`，用令牌文件运行 probe | health + 带鉴权查询成功，无令牌查询 401 | 端口、Service selector、Endpoint、令牌 |
| 实际入口 | 为现有 HTTPS 反代配置根路径到 Service；令 `UAV_RAG_URL=https://<RAG_HOST>` 再 probe | TLS 有效、相同 revision、查询成功 | DNS、证书、反代路由、网络策略；客户端不接受 URL 子路径或远端明文 HTTP |
| 边端宿主机 | 树莓派/Jetson 实际宿主机用边端客户端 probe | 与云端相同版本 | 路由、隧道监听、主机访问规则 |
| Agent Pod | 进入实际 pi-runner Pod，用同样 URL/令牌路径调用客户端 | Pod 网络可达、凭据可读 | Pod DNS/路由、Secret 挂载、fsGroup、NetworkPolicy |
| MCP | 在实际 Agent 环境发现并调用 `uav_knowledge_query`，使用返回游标续页 | 有限分页、预算生效、引用可用 | PiRunner 环境过滤、MCP env 覆盖、进程生命周期 |
| 真实 Agent | 提交先前本地示例，只允许登记工具 | 取得资料后提交合法目标及本任务引用 | 工具白名单、模型上下文/超时、Aivo/Pi 兼容 |
| 原执行器 | 在授权测试任务下查看任务回执和切换状态 | 真实认证、双端回执和业务验证均通过才算成功 | 服务登记/镜像验证、Ready、身份配置、执行日志与协调状态 |

探测命令（解压包或完整融合目录中）：

```sh
export UAV_RAG_URL=http://127.0.0.1:18787
export UAV_RAG_TOKEN_FILE=<LOCAL_READONLY_TOKEN_PATH>
python3 -m tools.probe_rag
```

边端镜像只有客户端，可在真实 Pod 执行 `python -c "from uav_rag.tools import environment_client; print(environment_client().query('用途',['auth-ed25519'])['index_revision'])"`。注意 kubectl exec 的直接环境不会自动读取 PiRunner JSON；先显式设置与其 JSON 一致的 URL/令牌路径，这只验证客户端，随后必须验证真正 Agent 的过滤后环境。

## 5. 切换边端地址与真实 Agent 接入

执行：重新构建 ARM64 PiRunner 镜像并生成新镜像锁（原 `deploy.scripts.build_offline_images` 或已验证的 buildx 流程），然后：

```sh
python -m deploy.scripts.render_layout --lock-dir <NEW_LOCKS> \
  --rag-url https://<POD_REACHABLE_RAG_HOST> --output <REVIEW_DIRECTORY>
```

预期：`pi-runner.json.environment` 中有 UAV_RAG_URL 与 `/rag-client/service.token`；`.mcp.json` 运行 `uav_rag.mcp_guard`，其 env 不覆盖 RAG 配置或 UAV_SELECTION_FILE。新 `uav-rag-client` Secret 在 edge namespace 保存同一只读查询凭据，PiRunner Pod 挂载；每任务启动新 MCP。原模型凭据仍按现场 Pi/Aivo 的受限配置挂载，不能把 RAG token 当作模型凭据。

失败检查：渲染输入、最终 ConfigMap、镜像内客户端模块、真实 Agent 工具白名单。不要直接在容器外 export 后认为 PiRunner 会继承；它只继承白名单及显式 environment。ConfigMap 用 subPath 挂载，变更后要受控 rollout 并等待当前任务结束，不能随意重启运行任务。

宿主机 `127.0.0.1` 不等于 Pod 的 `127.0.0.1`。只有同 Pod 隧道 sidecar 或同网络命名空间转发才可用回环 URL；宿主机隧道应改为受信、Pod 可达的入口，且客户端要求 HTTPS。不要为了可达而开放公网明文端口。

## 6. 更新和回滚

执行：离线构建新数据库文件，完成 doctor/测试和 backup 发布；上传新 release 目录并校验；保持旧目录、旧镜像 digest、旧清单及 Secret；渲染新 Deployment 的版本 subPath，等待无活动 Agent 任务后应用。数据库挂载是整版本目录只读，禁止原地改写。

预期：新 Pod health 和查询返回新 revision。任务中途版本变化会被工具拒绝，应新建任务而非混用旧游标或旧引用。完整代码版本与数据库版本共同发布。

失败检查：新包校验、服务端加载与客户端游标错误。回滚执行 `kubectl apply -f <PREVIOUS_REVIEWED_MANIFEST>` 或确认历史完整配对后 `kubectl rollout undo deployment/uav-rag`，再验证旧 revision 和鉴权查询。不要删 PVC、旧数据库或业务运行状态。待窗口验证完成后再按保留策略清理旧版本。
