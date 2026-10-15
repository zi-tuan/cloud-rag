# 本地融合与联调报告

日期：2026-10-15。交付根目录：`uav-cloud`。原 `cloud-rag` 与桌面业务项目未修改，目标目录在导入前不存在；导入工具逐文件拒绝覆盖差异文件。未复制 Git 元数据、原数据库运行状态、私钥、令牌、缓存或历史现场截图。公开 OpenSSL 示例中含私钥标记的文件也排除，因此重建知识版本与原项目不同；入库所需目录及来源保存在本项目。

## 实际环境与总结果

最终环境为 Python 3.12.14、SQLite 3.53.1（FTS5 可用）、原业务锁定依赖，虚拟环境 `.venv312`。密码测试调用 PATH 中实际 OpenSSL 3.3.0；它与知识语料 OpenSSL 4.2.0-dev 是不同对象。不能把源码知识版本当成已运行的密码库版本。

完整回归 **227 项通过、无跳过**，耗时 111.428 秒，日志 `data/regression-final.log`。之后新增的部署清单约束测试 **1 项通过**，日志 `data/deployment-tests.log`。合计 228 项；新增测试之后没有声称重新跑过全套 228 项。RAG 子集早期独立执行 37 项通过，包含于完整回归，不重复计数。

另外执行完整 `manage.py integration` 和独立发布包启动探测，均通过。详细证据分别是 `data/local-integration.json`、`data/release-validation.json`；数据库检查为 `data/doctor.json`。

| 验证层 | 实际结果 | 边界 |
|---|---|---|
| RAG / 数据库 | 重建 3,371 文档、24,798 片段；schema 3；FTS5、完整性、目录/描述/文档版本检查通过 | 未在 Linux 容器运行 |
| HTTP 客户端 | 真正本地 HTTP + 融合客户端查询、分页、鉴权成功/401、断连、超时通过 | 云端 HTTPS 和真实网络尚未验证 |
| MCP / Guard | 真实 stdio 子进程工具发现、查询、翻页；未查就提交、未查就激活、伪造和跨任务引用被拒绝；预算及游标测试通过 | 生产 Kubernetes MCP 上游为模拟，未接触集群 |
| Agent 决策 | 原 PiRunner 启动确定性替身 Agent，显式配置经环境过滤传递，原任务完整保留，取得引用后提交原协议目标 | 无真实模型调用；未运行外部 Pi/Aivo 适配器 |
| 执行器 | 原 TaskExecutor/SwitchCoordinator 处理同一任务的 Guard 回执，模拟外部就绪和本地云业务传输后得到 succeeded；原业务恢复、轮换、航线及认证回归通过 | readiness、集群、飞行条件为测试夹具；不是实机成功 |
| 容器 / Kubernetes/K3s | Dockerfile 模块隔离、原清单渲染、新 RAG 清单约束通过本地代码测试 | 本机没有 Docker/kubectl；镜像未构建、Pod 未运行 |

## 一个完整任务

本地云端 TaskStore 生成任务 `3e594d018d594246ac0c1fa5426e1099`，scenario 为：

> 将仿真巡检通信保护切换为 SM4

同一 task_id 贯穿 PiRunner 和执行器。替身 Agent 发现原服务登记工具及 `uav_knowledge_query`，读取认证策略与登记服务。尚未查询就提交时被拒绝。

查询请求（两类候选分别查询，不发送完整任务）：

```json
{"query":"签名 加密 用途 限制","candidate_services":["auth-ed25519"],"top_k":1}
```

第二页使用完全相同字段加服务实际返回的 `cursor`；随后以 `candidate_services=["sm4-gcm-v1"]` 查询。共 3 次真实 HTTP 查询，3 页分别含 13、5、14 项。第一项候选翻页读完；SM4 页仍有 next_cursor，未声称读完所有资料。返回项包含描述、限制、证据状态、知识 revision 和源码引用；不会把算法说明写成镜像可用证明。

最终选择：

```json
{
  "auth_policy": "Ed25519",
  "suite": "SM4-GCM",
  "protection_profile": "lightweight",
  "citation_ids": [
    "2dcfb4741e3c1fe2712131df582c72f5ea68e56fc8b5c5709f07e6a12d3a96d9",
    "e72e35b1273be42a5201dcb5c74f98f8bc9f575f3dd68c7eae8e397fa8cc36a7"
  ]
}
```

伪造引用提交先被拒绝，以上实际返回引用通过覆盖层校验后才进入原 Guard。Guard 返回 `state=selected, requires_execution_verification=true`，原回执仍是 `selection_kind=combination` 与原 `target`，知识记录另存 sidecar，不改变执行器协议。

执行器返回该 task_id 的 `state=succeeded`，**仅表示模拟外部条件下的本地执行器流程通过**。真实认证服务状态、设备、Pod 或飞行未由此次集成脚本确认。替身固定选择用于验证工具链，不能作为模型自主推理能力证明。

知识 revision：`268ed22e75536cdddb1ff1cb8af5672b7852c8ed4299e1891d2267fa1a728059`。完整响应、原始 prompt、引用 ID/哈希/行号及 MCP 请求在 JSON 报告中可审查；令牌只存在临时文件，未进入报告。

## 修改内容与原因

- 统一保留 `cloud`、`jetson`、`shared`、`services` 原业务结构；加入同根 `uav_rag`、公开语料、文档和测试。`manage.py` 提供 doctor/index/serve/integration/test/build/release 入口。
- `uav_rag/mcp_guard.py` 复用原 Guard，新增提交引用来源/所选服务覆盖校验及激活前证据检查；原 Guard 的登记和执行约束继续生效。
- `jetson/pi_runner/app.py` 补充按需查询、翻页、引用及异常停止说明，不改写原任务，不批量追加知识。环境白名单保持原安全约束，RAG 使用显式 environment 和令牌文件。
- `deploy/scripts/rag_config.py` 接入原 `render_layout`；删除 MCP env 对任务地址/回执的覆盖，增加只读 Secret 挂载和工具说明。配置模板同步修改。
- PiRunner Dockerfile 仅复制必需客户端模块；RAG 新增云端 Dockerfile，所有镜像构建上下文均为融合目录。没有引入边端数据库。
- 原 RAG 测试直接使用融合目录，不再依赖 `UAV_PROJECT_ROOT` 或相邻 OpenSSL 目录。两项原认证测试改用可配置 OpenSSL/PATH，移除本机安装位置假设。
- `tools/release.py` 用 SQLite backup 一致性快照、版本与 SHA256 校验打包。RAG Kubernetes 渲染器强制镜像 digest、明确 PVC、版本目录及非 root 只读配置。

## 启动与复现

从融合项目根目录执行（Python 3.12 和依赖准备见 README）：

```powershell
.venv312\Scripts\python manage.py doctor
.venv312\Scripts\python manage.py serve
# 第二终端：
$env:UAV_RAG_URL='http://127.0.0.1:8787'
.venv312\Scripts\python -m tools.probe_rag
# 自动链路自行启动服务，无需依赖上面的手工服务：
.venv312\Scripts\python manage.py integration
.venv312\Scripts\python manage.py test
```

`integration` 结束会关闭它创建的 HTTP/MCP/Agent 进程。需常驻本地服务时另行运行 `serve`，Ctrl+C 结束。正式启动配置在 `jetson/pi_runner/config`；生产配置来自 `render_layout --rag-url` 生成的 ConfigMap，不能直接 apply 基础输入清单。

## 故障记录与尚未验证项

最初 Python 3.11/pip 访问镜像站及 PyPI 出现 TLS 中断；换用 Python 3.12 和新版 pip 后按原锁定版本安装成功，未关闭证书校验。最初沙箱执行 Python 3.12 的临时目录遇到 WinError 5；获准在本机执行同样测试后正常。此前失败日志保留作诊断，不能拿来抵消或替代最终通过记录。卡住的旧测试进程已终止。

现场尚待：真实 Pi/Aivo/模型工具调用、Linux Unix socket 与进程组、ARM64 PiRunner 和 AMD64 RAG 容器、实际镜像仓库验证、PVC权限与持久化、云端 HTTPS 入口、树莓派/Jetson 宿主机与 Agent Pod 的网络、真实 MCP 上游/Guard/执行器及双端回执。基础网络通不等于这些层已通过。

当前发布包是 `dist/rag-6cb2ead7f4be6dfb.zip`（配套 `.sha256` 和解压目录）。包内 27 个文件校验通过；从该发布目录启动独立服务验证成功，有令牌查询通过、无令牌查询 401。这是 Windows 进程结果，不是镜像运行或云端部署结果。生产上传与逐层排错见 `CLOUD-DEPLOYMENT.md`，真实地址和存储信息仍待确认。
