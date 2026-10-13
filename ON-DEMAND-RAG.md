# 云端知识库按需查询接入

## 改动原因

旧 `workflow.py` 将候选描述与引用序列化到任务后面；旧 `pi_runner.py` 只截短每项第一条源码引用，保留全部描述、元数据，最后用 16,000 字符硬限制拒绝任务。候选增加会放大提示词；原任务接近上限时，任何追加都会失败。另一个边界是 `/enrich` 只接受 8,000 字符查询，却收到原任务全文。

当前改为 Agent 主动调用只读工具。数据库和源码只放云端；任务原文留在无人机 Agent，工具发送简短查询、服务 ID 和游标。少量工具结果进入模型上下文供判断，不再预先追加候选资料。不是云端代替 Agent 做决策，也没有引入云端模型。

## 云端更新

更新整个 `uav_rag/` 包，重启现有 RAG 服务。schema 仍为 2；已有 schema 2 数据库无需重建，schema 1 必须按 README 重建。保留当前数据库、令牌、HTTPS/SSH 转发设置。

新增 `/v1/knowledge/query` 与旧接口使用同一鉴权。旧 `/v1/services/enrich` 和 `query` CLI 保留用于兼容和人工检查，但新 Agent 路径不调用它。服务端内部复用原检索逻辑，响应通过分页器输出。

## 无人机运行镜像与配置

将完整 `uav_rag/` 放入 PiRunner 与 MCP 进程可导入的位置，例如 `/opt/uav/app/uav_rag/`。无需将 SQLite、GmSSL 源码或完整云端发布包放入无人机镜像。

原 PiRunner 启动入口继续使用 `python -m jetson.pi_runner`。旧 `python -m uav_rag.pi_runner` 也兼容，行为是直接启动原 runner。删除过时的 `rag_candidate_command` 配置，不再有初选子进程和 `--no-tools` 阶段。

在现有 `.mcp.json` 的 `mcpServers.uav-services` 内修改启动模块，保留其他原配置：

```json
{
  "command": "/usr/local/bin/python",
  "args": ["-m", "uav_rag.mcp_guard"],
  "env": {
    "PYTHONPATH": "/opt/uav/vendor:/opt/uav/app",
    "UAV_MCP_GUARD_CONFIG": "/opt/uav/config/mcp-guard.json",
    "UAV_RAG_URL": "http://127.0.0.1:18787",
    "UAV_RAG_TOKEN_FILE": "/rag-client/service.token"
  },
  "lifecycle": "eager"
}
```

这是单个 server 的配置片段，不是覆盖整个 `.mcp.json` 的文件。Python 路径以实际镜像为准。保留 `UAV_SELECTION_FILE` 的原任务传递机制，不能改为固定跨任务回执文件。

同时在 `pi-runner.json` 的 `environment` 中添加 `UAV_RAG_URL`、`UAV_RAG_TOKEN_FILE`，保留已有 PATH、PYTHONPATH、HOME 和 Guard 配置。通过现有 Secret 只读挂载令牌到 `/rag-client/service.token`；令牌值不放配置、提示词或工具参数。

原快照的配置来源是 `deploy/jetson/edge-control-k8s.yaml` 中的 `pi-runner.json` 与 `.mcp.json`，文件标注为渲染输入，应通过原项目渲染发布流程更新。本项目未修改该快照或应用集群配置。

`127.0.0.1:18787` 仅适用于隧道入口与 Agent 同网络命名空间。例如同 Pod 隧道 sidecar 可通过 localhost 访问；宿主机 SSH 隧道并不会自动出现在普通 Pod 内。也可使用 Pod 可访问的受信 HTTPS 地址。不可把公网明文 HTTP 当成替代；客户端会拒绝。RAG 地址不会替换任务 API、身份认证或飞控地址。

现有 Agent 要允许扩展 MCP 工具；保留禁止任意 shell/文件操作的原策略。单独设置 `--no-tools` 会禁用所需查询和执行工具。工具白名单中加入 `uav_knowledge_query`，不要开放整个 Kubernetes API。

## 工具行为及预算

Agent 根据原任务调用：

```json
{
  "query": "SM4-GCM 的用途和使用限制",
  "candidate_services": ["sm4-gcm-v1"],
  "top_k": 1
}
```

工具会返回 `items` 和 `next_cursor`。非空游标表示内容尚未读完；续页保持查询、候选和 top_k 不变，只追加 cursor。建议一次查询一个或少量候选，以免预算被无关内容占满。无需总是读完所有候选；不能声称看过尚未返回的页面。

| 边界 | 行为 |
|---|---|
| 原任务 1～16,000 字符 | 原样交原 Runner；不追加数据库资料 |
| 查询超过 512 字符 | 工具拒绝，并要求组织简短问题，不截断任务 |
| 候选 1～16 个 | 别名规范化、去重；当前目录共 8 项 |
| HTTP 页面 | 默认 6,000 UTF-8 字节，包含完整 JSON；允许 4,096～12,000 |
| Agent 页面 | 最多 6,000 UTF-8 字节 |
| 单个任务 MCP 进程 | 最多 8 次检索，成功数据累计最多 24,000 字节；失败请求消耗次数 |
| 预算耗尽 / 断网 | 显式工具错误；不产生资料或成功证明 |
| 翻页期间索引变化 | 游标失效；工具也拒绝跨版本混合结果 |
| 单个知识项过大 | 显式拒绝该页，不静默丢弃项目；源码文本按 256 字符分段 |

预算按 JSON UTF-8 字节计量，不等同于模型 token。MCP 外层包装、工具定义、原系统提示、其他工具和对话历史不在此预算内。部署者仍需按实际模型配置上下文窗口、最大轮数和超时。原 PiRunner 每个任务启动一个 Agent 子进程；应使 MCP 同生命周期，禁止跨任务复用 Guard 已查记录。若改用长期 Agent 会话，需要在宿主增加任务级重置和总上下文管理。

`description` 是人工整理的算法知识；`evidence_status` 仍描述 GmSSL 实现证据。查询 Ed25519 得到用途说明，不会把 `not_found` 提升为 `available`。源码引用的 ID、哈希、起止行仍标识原片段，分段的字符范围由 `text_offset` 和 `text_length` 指明。

组合 Guard 会在提交 `uav_combination_select` / `uav_protection_plan` 前检查所选算法是否实际查过。这个检查只证明已查询，不能证明模型认真比较、部署可用或资料充分。查询后仍由原 Guard、执行器和实际回执约束服务执行；RAG 不能直接启动 Pod。

## 验收

1. 在 Agent 所在 Pod 的相同网络路径下，用新代码执行 `python -m uav_rag lookup "SM4-GCM 限制" --services sm4-gcm-v1 --url <可达地址>`，令牌通过环境中的文件路径读取。
2. 检查响应页面非空、版本正确、UTF-8 字节数不超过预算，按游标获取下一页。不要只验证 `/health` 或旧 `/enrich`。
3. 查看实际 MCP `tools/list`，确认有原服务工具及 `uav_knowledge_query`。提交组合前未查询应收到工具错误。
4. 实际模型任务中确认发生查询调用，然后才有原组合或计划工具回执；输入任务中没有追加整批资料。
5. 用接近 16,000 字符的任务验证，使用简短 query。断网、预算耗尽时应明确说明知识不足，不声称执行成功。

本地已覆盖真实 SQLite / HTTP、分页还原、stdio MCP、原 PiRunner 和原 Guard 契约。Agent 与回执使用测试替身；真实模型调用、Pod 网络与集群部署仍须在目标环境验收。

旧四参数 `run_workflow` 改为 `run_workflow(task, agent, client)`，其中 `agent(scenario, tools)` 自行调用工具。旧命令 `workflow --agent-command` 不再支持。仅支持 stdin 一次输入的命令不能自动具备工具调用能力；实际 Agent 应接入 MCP。其他 MCP Agent 可使用只读入口 `python -m uav_rag.mcp`，但 UAV 应使用组合 Guard。
