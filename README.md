# 无人机服务 RAG：本地实现与云端交付

本模块为现有 UAV 项目增加云端服务知识库。输入为无人机 Agent 初选的候选服务；输出为 GmSSL 原文片段、出处、证据状态及可选的服务归档下载信息。最终判断交给无人机 Agent，启动与切换继续使用原项目的 MCP Guard、TaskExecutor 和 SwitchCoordinator。

Python 3.11+，仅依赖标准库及 SQLite FTS5。当前是 **BM25 检索增强实现**，未使用 embedding 模型或向量数据库；中文通过双字分词和受控术语扩展匹配 C 接口。生成步骤由现有无人机 Agent 承担，检索服务本身不编造服务说明、不调用外部模型。若以后引入向量检索，应保留现有引用和服务 ID 约束，并先建立检索评测集。

## 本地启动

在 PowerShell 执行：

```powershell
cd E:\claudeCode\project\Intra-group-project\cloud-rag
python -X utf8 -m uav_rag index --source ..\GmSSL --db data\gmssl.sqlite3
python -X utf8 -m uav_rag serve
```

另一个终端执行：

```powershell
cd E:\claudeCode\project\Intra-group-project\cloud-rag
python -X utf8 -m uav_rag query "无人机需要国密身份签名和 GCM 通信加密" --services SM2 SM4-GCM RSA Ed25519
```

服务默认只监听 `127.0.0.1:8787`。默认数据库已从本机 GmSSL 建好，为 401 个文件、3986 个片段。`index` 完成后原子替换数据库；运行中的服务应重启后再查询新索引，避免把旧版本元数据与新文件混用。它不会修改 GmSSL 源码。

完整本地演示：`python -X utf8 demo.py`。该演示使用真实索引和 HTTP，Agent 是明确标注的确定性替身。完整证据保存于 `data/demo-result.json`。真实模型运行不属于此演示的验证范围。

## 数据与检索契约

SQLite 的 `chunks` 表保存原文、相对路径、起止行号、文件 SHA-256、片段 ID 和检索文本；`search` 是 FTS5 索引；`metadata` 保存语料版本、文件数量和片段数量。语料版本由全部文件路径与内容哈希确定。每片最多 60 行，相邻片段重叠 12 行。

索引涵盖 Markdown、头文件、C 源码、CMake 和许可证，跳过隐藏目录、构建目录、超大文件和生成的大型查表文件。全文检索可查询所有入库文件；候选服务补充 API 进一步限定到映射的实现、声明、工具或测试文件，防止借用无关算法的资料。

`POST /v1/services/enrich`：

```json
{
  "task_id": "mission-001",
  "query": "为无人机选择国密签名认证与 GCM 通信加密",
  "candidate_services": ["auth-sm2", "sm4-gcm-v1", "auth-rsa"],
  "top_k": 3
}
```

返回的每项 `services` 包含：

- `service_id`：与无人机目录一致的 ID；也接受 SM2、RSA、Ed25519、SM4-GCM、AES-256-GCM 等别名。
- `binding`：`auth_policy` 或 `suite` 的映射，供现有选择协议使用。
- `evidence_status`：`available`、`insufficient` 或 `not_found`。
- `citations`：原文及 `path/start_line/end_line/sha256/id`。
- `description`：算法的一般用途 `purpose`、优点与选择条件 `advantages`、缺点与限制 `limitations`，以及参考文献 `sources`。覆盖全部 8 项登记能力；其中签名、摘要、密钥派生和证书校验不等同于数据加密。
- `artifact`：云端是否实际登记了兼容的可下载归档；默认 unavailable。

算法描述维护在 `uav_rag/descriptions.py`，执行 `index` 后写入 SQLite 的 `algorithm_descriptions` 表；补充接口按候选服务 ID 从数据库读取，不依赖源码关键词是否命中。修改描述后需重建索引并重启服务。数据库 schema 为 2，旧 schema 1 必须用上面的 `index` 命令重建。元数据 `description_count` 和 `description_revision` 分别记录描述数量与内容哈希，原 `revision` 仍标识 GmSSL 源码语料。

描述属于带参考来源的人工整理知识，不是服务实现或部署证明。因此 RSA、Ed25519 即使有描述，其原有 `evidence_status` 和源码引用也不会自动提升。Agent 会收到完整描述（PiRunner 仅截短源码引用），并结合用途、优点和限制说明选型理由；通用性能判断不替代目标设备实测。

`GET /v1/services` 返回知识服务目录；`GET /health` 返回索引版本；`GET /v1/artifacts/{service_id}` 只提供登记的归档。

GmSSL 的 SM2、SM4、AES 等知识不代表无人机现有镜像使用了 GmSSL。原项目密码服务使用 cryptography，SM2 身份服务还依赖 OpenSSL。这里的检索结果是补充技术资料，最终接口兼容性由原服务登记和执行器判断。

本地 GmSSL 的 RSA 文件主要提供公钥打印，不能证明 RSA-PSS 认证实现；未找到 Ed25519 实现资料。这两项会如实返回证据不足。应补充现有认证服务的说明后再扩充其知识映射，不能因为 RAG 缺资料就判定原服务不存在。

## 两阶段工作流

```text
云端原任务 API → 无人机初选 Agent → candidate_services
                                    ↓
                           本模块 /v1/services/enrich
                                    ↓
原任务 + 带引用补充资料 → 无人机最终选择 Agent
                                    ↓
                      原 MCP 结构化回执和确定性执行器
```

`uav_rag.workflow.run_workflow` 可传入两个模型调用函数，验证最终选择属于候选且有引用；无证据时返回 `insufficient_evidence`。它返回 `planned`，不宣称已启动或完成通信切换。

真实模型命令模式：

```powershell
python -m uav_rag workflow --task task.json --agent-command '["path-to-readonly-agent", "--json"]'
```

`task.json` 包含 `task_id` 和 `scenario`。所配置命令从 stdin 读取提示，从 stdout 返回一个 JSON 对象。命令必须由部署者配置为只读规划，并使用其真实 JSON 输出模式；不要直接把示例路径当成已安装程序。

## 接入原无人机 PiRunner

已有源码位于 `C:\Users\86187\Desktop\pi_uav_k3-codex-service-layout`。这里提供 `uav_rag.pi_runner` 启动覆盖层，无需改动该快照。它继承原 PiRunner，复用任务幂等、执行日志、进程组清理和最终 MCP 回执。

部署时把 `uav_rag/` 放入 PiRunner 镜像 Python 路径，保留原项目代码及依赖，将原容器启动入口由 `python -m jetson.pi_runner` 调整为：

```bash
python -m uav_rag.pi_runner
```

保留原 `UAV_PI_RUNNER_CONFIG`，增加：

```text
UAV_RAG_URL=http://127.0.0.1:8787
UAV_RAG_TOKEN_FILE=/rag-client/service.token
```

第一次 Agent 调用附加 `--no-tools`，只产生候选 JSON；RAG 查询后，第二次调用原 PiRunner，由原 MCP 产生最终选择回执。可用配置字段 `rag_candidate_command` 单独设置初选 Pi 命令（argv 数组，仍附加 `--no-tools`）。检索失败直接保存失败记录，不继续无资料规划；重启行为仍遵循原任务对账语义，不自动重跑模型。

覆盖层也适用于带 `protection_context` 的三阶段任务：原最终规划模式保留，候选资料追加到场景。现有 Agent 的实际输出格式、MCP 权限及冷启动须在 Jetson 实测。当前契约测试使用原 PiRunner 和真实子进程，但模型/MCP 回执由测试替身产生。

如果 RAG 在电脑上、PiRunner 在 Jetson，则 Jetson 的 `127.0.0.1` 并不指向电脑。可在边缘建立到 RAG 主机的 SSH 本地转发，或使用有受信证书的 HTTPS 地址。RAG 客户端拒绝远端明文 HTTP。这里仅配置新的 RAG 地址，不把原任务、身份认证和飞控端点整体替换成本机。

## 缺少服务时下载

GmSSL 是源码资料，不自带兼容原 UAV HTTP 接口、身份材料和 Kubernetes 模板的 OCI 镜像，因此 `deploy/artifacts.json` 默认空登记。提供了真实可用的下载接口和客户端校验，但没有虚构可运行服务。

管理员已有经过验证的 ARM64 OCI archive 时，将文件放到登记表目录内并填写：

```json
{
  "services": {
    "sm4-gcm-v1": {
      "file": "artifacts/sm4-arm64.tar",
      "sha256": "替换为归档文件的64位SHA256",
      "architecture": "arm64",
      "format": "oci-archive"
    }
  }
}
```

然后用 `serve --artifacts deploy/artifacts.json` 启动。服务器拒绝目录逃逸、非登记服务和被篡改文件。调用 `RagClient.download(service_result, cache_directory)` 会验证架构、大小、SHA-256 并原子落盘，不执行归档内容。

归档 SHA-256 是文件哈希，不能充当 OCI manifest digest。镜像导入、Harbor 拉取、部署模板登记和 `resources_scale` 仍由原部署/执行组件负责。原环境若使用 `imagePullPolicy: Never`，必须先导入正确镜像；仅下载归档不能让 Pod 启动。认证服务还必须预置合法身份材料，不能通过下载自动生成或替换身份。此交付没有新增无人机自动导入镜像或任意安装工具。

## 测试

```powershell
$env:UAV_PROJECT_ROOT='C:\Users\86187\Desktop\pi_uav_k3-codex-service-layout'
python -X utf8 -m unittest discover -s tests -v
```

不设置 `UAV_PROJECT_ROOT` 时，只跳过原 PiRunner 契约测试。其余测试依然使用真实 GmSSL 和本地 HTTP。发布包附带 `sources/GmSSL`，解压后可复现语料测试；开发目录默认读取相邻的 `GmSSL/`。

## 云端发布

运行 `python package_release.py`，得到 `data/uav-rag-local-release.zip`、SHA-256 文件。包内包含代码、SQLite 索引、入库源码、许可证、测试、systemd 样例和逐文件哈希清单。没有凭据或无人机运行数据库。

运行 `python verify_release.py` 可逐项核对发布包内文件与清单哈希。哈希验证提供完整性检查，不代替对发布者的身份认证。

云端需要 Python 3.11+ 和 SQLite FTS5。将包上传到选定主机并解压，先按 `release-manifest.json` 验证文件哈希，再在解压目录执行 `python3 -m unittest discover -s tests -v` 和 `python3 demo.py`。

可用 `deploy/uav-rag.service` 运行回环服务；其中账号、安装目录、Python 版本需与实际环境一致。服务令牌由云端独立生成，至少 32 字符，保存到 `/etc/uav-rag/service.token` 并只授权服务账号读取。对外访问经已有 HTTPS 反向代理或 SSH 转发。该文件仅为部署模板，未安装为本机或云端服务。

Docker 模式：从当前目录构建 Dockerfile，挂载 `/data` 为只读数据库目录，通过 `UAV_RAG_TOKEN_FILE` 挂载只读令牌文件。对外绑定要求令牌，容器内用户为 1000。镜像尚未在本机构建或启动。

目前已完成本地数据库、API、客户端、覆盖层和联调测试。未连接云端、未运行真实模型、未更改 Jetson 的服务副本。发布前仍需明确目标云端主机、账号/SSH 配置和部署目录，真实 OCI 服务归档由现有发布链提供。
