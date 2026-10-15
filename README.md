# 无人机服务 RAG：本地实现与云端交付

当前知识来源已迁移为提供的 **OpenSSL 4.2.0-dev 源码**。迁移范围是 SQLite 的源码资料和算法说明；本模块没有 GmSSL 字段加解密调用。参见 [迁移与回滚说明](docs/OPENSSL-MIGRATION.md)、[独立算法文档](docs/OPENSSL-ALGORITHMS.md) 和 [本次测试报告](docs/OPENSSL-VALIDATION.md)。旧日期报告仍记录 GmSSL 部署，不能作为新版本验收依据。

本模块为现有 UAV 项目增加云端服务知识库。无人机 Agent 通过只读工具按需检索云端数据库，获取有长度预算的知识分页；原任务不追加数据库内容。旧批量补充接口保留作人工查询兼容。最终判断交给无人机 Agent，启动与切换继续使用原项目的 MCP Guard、TaskExecutor 和 SwitchCoordinator。

Python 3.11+，仅依赖标准库及 SQLite FTS5。当前是 **BM25 检索增强实现**，未使用 embedding 模型或向量数据库；中文通过双字分词和受控术语扩展匹配 C 接口。生成步骤由现有无人机 Agent 承担，检索服务本身不编造服务说明、不调用外部模型。若以后引入向量检索，应保留现有引用和服务 ID 约束，并先建立检索评测集。

## 本地启动

在 PowerShell 执行：

```powershell
cd E:\claudeCode\project\Intra-group-project\cloud-rag
python -X utf8 -m uav_rag index --source ..\openssl --db data\openssl.sqlite3
python -X utf8 -m uav_rag serve
```

另一个终端执行：

```powershell
cd E:\claudeCode\project\Intra-group-project\cloud-rag
python -X utf8 -m uav_rag query "无人机需要国密身份签名和 GCM 通信加密" --services SM2 SM4-GCM RSA Ed25519
```

服务默认只监听 `127.0.0.1:8787`，默认新库为 `data/openssl.sqlite3`。`index` 只创建新数据库，目标已存在会拒绝；更新时使用新文件名，再切换配置并重启服务，避免旧元数据与新连接混用。它不会修改 OpenSSL 源码或原 `data/gmssl.sqlite3`。

完整本地演示：`python -X utf8 demo.py`。该演示使用真实 OpenSSL 索引和 HTTP，Agent 为确定性替身。完整证据保存于 `data/openssl-demo-result.json`。真实模型与密码运算不属于此演示的验证范围。

## 数据与检索契约

SQLite 的 `chunks` 表保存原文、相对路径、起止行号、文件 SHA-256、片段 ID 和检索文本；`search` 是 FTS5 索引；`metadata` 保存语料版本、文件数量和片段数量。语料版本由全部文件路径与内容哈希确定。每片最多 60 行，相邻片段重叠 12 行。

索引涵盖 OpenSSL 主项目选定目录的 Markdown、POD、头文件、C 源码、`.in` 模板和许可证，排除第三方子模块、隐藏目录、构建目录、超大文件和大型查表文件。全文检索可查询所有入库文件；候选服务 API 限定到登记的 EVP 文档及实现路径，防止借用无关算法的资料。

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

八项服务描述由 `uav_rag/descriptions.py` 的通用知识和 `openssl_source.py` 的本地来源/限制组成，执行 `index` 后写入 `algorithm_descriptions`。查询从数据库读取。新库 schema 为 3，新增 `service_catalog`、独立 `knowledge_documents` 及静态 `algorithm_inventory`；旧 GmSSL schema 2 仍可只读查询，schema 1 不支持。元数据保存源码 revision、描述 revision、独立文档 SHA-256 和来源文件哈希。修改描述/文档后须建新库并切换服务。

运行 `python -X utf8 -m uav_rag document` 读取独立文档，也可在原有鉴权下访问 `GET /v1/documents` 和 `GET /v1/documents/openssl-algorithms`。实际运行算法能力标为 `unverified`：提供目录只有源码，未编译成库；有源码证据不等于目标设备可执行。

描述属于带参考来源的人工整理知识，不是部署证明。OpenSSL 中 RSA-PSS、Ed25519 有实际实现及接口文档，故新库的源码证据为 available；旧 GmSSL 库仍保持原结论。完整服务描述保存在云端，Agent 通过原分页工具按需读取；完整算法文档另由 documents 接口提供，未自动加入 Agent 提示词。

`GET /v1/services` 返回知识服务目录；`GET /health` 返回索引版本；`GET /v1/artifacts/{service_id}` 只提供登记的归档。

OpenSSL 的算法知识不代表无人机镜像使用了这份 4.2.0-dev 库。最终接口兼容性仍由原服务登记和执行器判断。`gmssl-sm3`、`gmssl-hkdf`、`gmssl-x509` 保留为历史公开 ID，避免调用方破坏性迁移。

## 按需查询工作流（当前接入方式）

```text
原任务 → 无人机 Agent（任务文本保持原样）
              ↕ uav_knowledge_query 只读工具
          云端 /v1/knowledge/query → SQLite 知识库
              ↓ 有预算的结果页 / next_cursor
          Agent 判断 → 原 MCP Guard → 原确定性执行器
```

不再运行“初选后批量拼接增强提示词”。云端保管完整知识，Agent 自己组织简短检索问题，按候选逐项查询，需要更多内容时翻页。工具返回的少量资料仍进入模型上下文，这是 Agent 使用检索知识的途径；它不会下载整库或将所有候选知识预填进任务。

`POST /v1/knowledge/query` 示例：

```json
{
  "query": "SM4-GCM 的使用限制与接口依据",
  "candidate_services": ["sm4-gcm-v1"],
  "top_k": 1,
  "max_bytes": 6000
}
```

返回 `index_revision`、`description_revision`、`items` 和 `next_cursor`。`items` 分为 `service`（映射和证据状态）、`description`（描述字段）及 `citation`（原文片段）。非空游标表示还有内容；下一次传相同 query、candidate_services、top_k，加上 cursor。游标绑定查询与索引版本，过期或换查询时返回 400。

- 查询文本 1～512 字符，单次候选 1～16 个，别名去重；不把长任务作为 query。
- HTTP 单页默认最多 6,000 UTF-8 字节，可设 4,096～12,000；计算包含 JSON 元数据和游标。
- Agent 工具单页最多 6,000 字节，每个 MCP 进程最多 8 次查询、累计最多 24,000 字节成功结果。失败请求也计次数；预算耗尽返回工具错误，不静默截断知识或任务。
- 引用保留原片段 ID、文件哈希及原行号范围；`text_offset` / `text_length` 表示原片段内的字符偏移和总长度。分页文本不是新的完整引用范围。
- 原 PiRunner 的 16,000 字符场景上限保持不变。该限制与模型 token 上限不同；完整系统提示、其他工具及历史对话仍由 Agent 运行时管理。

本地查询一页：

```powershell
python -X utf8 -m uav_rag lookup "SM4-GCM 的限制" --services sm4-gcm-v1
```

`uav_rag.workflow.run_workflow(task, agent, client)` 现在接收 `agent(scenario, tools)` 回调；回调按需调用 `tools.invoke`。验证最终引用确实出现在本次已读取页面中，返回 `planned` 而非执行成功。旧四参数回调、`augmented_scenario`、`retrieve_for_task` 以及 `workflow --agent-command` 已移除，避免继续走提示词拼接路径。`demo.py` 演示新契约。

## 接入原无人机 PiRunner / k8s

保留原 `python -m jetson.pi_runner` 入口；`python -m uav_rag.pi_runner` 作为兼容入口也调用原 runner，不再初选或改写 scenario。将 `uav_rag/` 放到原运行镜像的 Python 路径。

现有 `.mcp.json` 中 `uav-services` 的启动参数由 `-m jetson.edge_agent.mcp_guard` 改为 `-m uav_rag.mcp_guard`。这个覆盖层增加只读查询工具，并继续调用原 Guard 处理所有执行工具。提交单次组合或三阶段计划前，必须实际查询所选认证与加密服务；资料缺失不等于服务不存在，原服务验证规则仍生效。

连接地址和凭证路径必须放进 `pi-runner.json` 的 `environment`，并检查 MCP 配置的 `env` 没有覆盖成其他值；仅在容器外 export 无效，因为原 runner 过滤继承环境。必须每任务启动独立 MCP 进程，避免共用查询预算与已查记录。

完整配置片段、迁移步骤和验收说明见 [按需 RAG 接入说明](ON-DEMAND-RAG.md)。该文包含宿主机隧道与 Pod 回环地址的区别。现有 [旧接口联调记录](RETEST-JETSON-CLOUD.md) 可继续用于旧 `/enrich` 接口测试，不能证明新工具已接入。

其他支持 stdio MCP 的 Agent 可单独配置 `python -m uav_rag.mcp`，它只暴露知识工具，没有服务执行权限。已有 UAV 应使用组合 Guard 入口，以保留提交前的查询检查。

## 缺少服务时下载

OpenSSL 是源码资料，不自带兼容原 UAV HTTP 接口、身份材料和 Kubernetes 模板的 OCI 镜像，因此 `deploy/artifacts.json` 默认空登记。提供了真实可用的下载接口和客户端校验，但没有虚构可运行服务。

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

不设置 `UAV_PROJECT_ROOT` 时，跳过原 PiRunner 与 Guard 两项契约测试。其余测试使用真实 OpenSSL 语料和本地 HTTP。发布包附带 `sources/openssl` 索引语料快照；开发目录默认读取相邻 `openssl/`。旧库测试使用 schema 2 隔离夹具，若本地保留 `data/gmssl.sqlite3` 还会只读验证真实旧库。

## 云端发布

运行 `python package_release.py`，得到 `data/uav-rag-openssl-release.zip`、`openssl-release-sha256.txt`。包内包含代码、新 SQLite 索引、算法/迁移说明、入库源码、OpenSSL 许可证、测试、systemd 样例和逐文件哈希清单。原 GmSSL 库和发布包保留；新包没有凭据或无人机运行数据库。

运行 `python verify_release.py` 可逐项核对发布包内文件与清单哈希。哈希验证提供完整性检查，不代替对发布者的身份认证。

云端需要 Python 3.11+ 和 SQLite FTS5。将包上传到选定主机并解压，先按 `release-manifest.json` 验证文件哈希，再在解压目录执行 `python3 -m unittest discover -s tests -v` 和 `python3 demo.py`。

可用 `deploy/uav-rag.service` 运行回环服务；其中账号、安装目录、Python 版本需与实际环境一致。服务令牌由云端独立生成，至少 32 字符，保存到 `/etc/uav-rag/service.token` 并只授权服务账号读取。对外访问经已有 HTTPS 反向代理或 SSH 转发。该文件仅为部署模板，未安装为本机或云端服务。

Docker 模式：从当前目录构建 Dockerfile，挂载 `/data` 为只读数据库目录，通过 `UAV_RAG_TOKEN_FILE` 挂载只读令牌文件。对外绑定要求令牌，容器内用户为 1000。镜像尚未在本机构建或启动。

目前已完成本地数据库、分页 API、客户端、MCP 工具覆盖层和联调测试。本次改动未部署到云端或 Jetson，未运行真实模型、未更改 k8s 服务副本。发布前仍需明确目标云端主机、账号/SSH 配置和部署目录，真实 OCI 服务归档由现有发布链提供。
