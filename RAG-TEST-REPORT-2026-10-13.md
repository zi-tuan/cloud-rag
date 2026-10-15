# RAG 按需查询版本：本地测试流程与结果分析

记录日期：2026-10-13。项目：`E:\claudeCode\project\Intra-group-project\cloud-rag`。

本文记录本次会话上一轮实际执行的测试，不是远端运行证明。编写文档时未再次运行整套测试、未部署服务、未修改业务逻辑。测试对象是当前工作区代码，包含尚未提交的按需查询改动，不等同于 Git HEAD 或云端已有发布包。

## 1. 结论与验证边界

- 自动化回归：30 项通过，0 失败，0 跳过；测试框架报告耗时 6.101 秒。
- 实际数据库：完整性正常；401 个文件、3986 个片段、8 项算法描述。
- 实际数据库经本地 HTTP 查询：全部描述与当前维护内容一致。
- 4096 字节分页：15 页可完整还原全部描述及本次检索的源码，无丢失。
- 16000 字符原任务：新路径保持原样，工作流及原 PiRunner 接入测试通过。
- 单任务预算：正常拦截超预算读取；不保证一次读完所有候选。
- 演示：查询 2 页，返回 SM2 与 SM4-GCM，状态 `planned`。

真实组件包括 SQLite、GmSSL 源码、本地 HTTP、stdio MCP 子进程、原项目 PiRunner/Guard 部分逻辑。Agent 决策和最终回执使用测试替身。没有验证真实大模型、云端网络、Jetson 实机、Pod 网络、密码服务启动或飞控执行。

## 2. 当前实现与旧版区别

```text
原任务 → Agent（scenario 不追加知识）
          ↓ uav_knowledge_query：短问题、候选 ID、游标
        MCP / RagTools → HTTP /v1/knowledge/query
          ↓
        SQLite：算法描述 + FTS5/BM25 源码检索
          ↓ 分页响应
        Agent 判断 → 原 Guard / 执行器
```

| 文件 | 当前作用 |
|---|---|
| `uav_rag/index.py` | 建库、按候选读取描述、检索源码 |
| `uav_rag/descriptions.py` | 8 项能力的一般用途、优点、限制及参考来源 |
| `uav_rag/retrieval.py` | 知识条目分页、引用分段、游标版本绑定 |
| `uav_rag/client.py` | HTTP 调用及页面基本校验 |
| `uav_rag/tools.py` | 只读工具、查询长度与累计预算 |
| `uav_rag/mcp.py` | stdio MCP 协议入口 |
| `uav_rag/mcp_guard.py` | 组合原 Guard，检查提交前是否查询过所选算法 |
| `uav_rag/pi_runner.py` | 兼容原 Runner，不再初选或改写 scenario |
| `uav_rag/workflow.py` | 本地 Agent 回调测试契约与已读引用校验 |

旧的“候选初选 → 批量 enrich → 拼接增强提示词 → 第二次模型调用”已移除。旧 `/v1/services/enrich` 仍用于兼容和人工检查，但新 Agent 走分页工具。旧联调文档中的 21 项测试、两次决策等描述不能代表当前版本。

## 3. 环境与数据库身份

| 项目 | 实测值 |
|---|---|
| 操作系统 | Windows |
| Python | 3.11.4 |
| SQLite | 3.42.0，FTS5 可用 |
| 数据库 | `data/gmssl.sqlite3` |
| schema | 2 |
| documents / chunks | 401 / 3986 |
| description_count | 8 |
| 完整性 | `PRAGMA integrity_check` 返回 `ok` |

源码语料版本：

```text
eea0294c300203072799024c233d82671b1cc91976ae7f159ce4c66323bd4021
```

描述版本：

```text
8b88418ef366a3d8a29e85d5ef4b10ad41b88821497c5d485b7ab92a13298ce2
```

描述版本与当前 `DESCRIPTIONS` 的规范化 JSON 哈希一致。源码版本不包含描述版本，因此部署核对需要检查两者。

## 4. 完整回归流程

### 4.1 执行方式

在 Windows PowerShell 执行：

```powershell
cd E:\claudeCode\project\Intra-group-project\cloud-rag
$env:UAV_PROJECT_ROOT='C:\Users\86187\Desktop\pi_uav_k3-codex-service-layout'
python -X utf8 -m unittest discover -s tests -v
```

测试先从 GmSSL 源码构建临时数据库，再启动随机回环端口的 HTTP 服务，以真实客户端调用。测试结束会关闭服务并清理临时目录，不覆盖业务数据库。临时下载归档只是校验传输用的字节，不是可运行镜像。

`UAV_PROJECT_ROOT` 使两个原项目接入测试实际运行；未配置时预期为 30 项中跳过 2 项，不能写成 30 项全部执行通过。新环境应按真实源码路径设置，不应盲目复制本机路径。

### 4.2 已执行的 30 项用例

以下名称均省略 `test_` 前缀，本次均为通过。

| 分组 | 用例 |
|---|---|
| 数据库与描述 | `algorithm_descriptions_persisted_and_returned`、`descriptions_read_from_database_not_runtime_catalog`、`index_rebuild_is_reproducible`、`read_only_index` |
| 检索内容 | `corpus_and_citations_match_source`、`retrieval_ranking`、`chinese_query`、`all_evidence_classes`、`unknown_is_not_filled_with_unrelated_results` |
| HTTP 输入及权限 | `authentication`、`validation`、`bounded_request`、`remote_http_and_unprotected_bind_rejected` |
| 分页与预算 | `all_candidates_pagination_is_bounded_and_lossless`、`knowledge_query_validation_and_cursor_binding`、`tool_total_budget_and_failed_attempts`、`tool_rejects_revision_change` |
| 工作流 | `tool_workflow_long_task_real_http`、`fabricated_selection_rejected`、`abstention`、`disconnect_prevents_evidence_selection` |
| MCP / 原项目接入 | `guard_requires_lookup_and_preserves_original_dispatch`、`stdio_mcp_real_http`、`original_guard_with_retrieval`、`original_pi_runner_overlay` |
| 下载及路径边界 | `no_path_traversal`、`download_checksum_and_cache`、`download_missing_artifact`、`manifest_escape_rejected`、`mutated_artifact_rejected` |

最后输出：

```text
Ran 30 tests in 6.101s
OK
```

主要断言包括：描述确实来自 SQLite；引用内容与文件对应行一致；非法请求被拒绝；分页可还原；游标不能跨查询或版本混用；失败调用消耗次数；断网不能获得证据；模型不能提交伪造或未读引用；原 Guard 工具继续保留。

耗时是本机这一轮测试总耗时，包含建库和测试准备，不是检索延迟或并发吞吐量指标。

## 5. 对实际数据库的额外检查

自动化测试使用临时数据库，因此另外对实际 `data/gmssl.sqlite3` 做了只读检查，并启动独立本地 HTTP 服务查询。

### 5.1 全部算法描述

查询 `签名 加密 优缺点`，候选为全部 8 项，`top_k=1`。每项描述均与维护内容相等：1 段用途、2 条优点、3 条限制，加参考来源。

| 服务 ID | 源码证据状态 | 本次原始引用数 |
|---|---|---:|
| sm4-gcm-v1 | available | 1 |
| aes-gcm-v1 | available | 1 |
| auth-sm2 | available | 1 |
| auth-rsa | insufficient | 1 |
| auth-ed25519 | not_found | 0 |
| gmssl-sm3 | available | 1 |
| gmssl-hkdf | available | 1 |
| gmssl-x509 | available | 1 |

RSA 的引用存在不代表 RSA-PSS 实现充分；Ed25519 的描述存在不代表 GmSSL 中有实现。SM3、HKDF、X.509 分别属于摘要/认证、密钥派生、证书体系，不应统称为数据加密。

### 5.2 全量分页还原

条件：同一查询，8 项候选重复一遍形成 16 个输入，`top_k=1`，HTTP `max_bytes=4096`。逐次传回 `next_cursor`，直到游标为空。

| 指标 | 实测值 |
|---|---:|
| 去重后服务数 | 8 |
| 页面数 | 15 |
| 最小 / 最大页面字节数 | 1442 / 4067 |
| 累计页面字节数 | 54577 |
| service 记录 | 8 |
| description 记录 | 60 |
| citation 分段记录 | 56 |
| 还原并校验的原始引用 | 7 |

校验方法：按服务、字段与 index 还原描述；按引用 ID 和字符偏移拼接源码；与旧 enrich 同查询结果逐项比较；再对照 GmSSL 原文件的 SHA-256 和行号。全部相等。56 个分段不是 56 份独立证据。

这里使用底层 HTTP 客户端读完整语料，不应用 Agent 的 24000 字节总预算。它证明分页无损，不代表真实 Agent 可以在单任务中读取全部页面。

### 5.3 Agent 总预算

同一查询、全部 8 项候选，换成 `RagTools` 逐页读取，采用默认单页上限 6000 字节、最多 8 次、总预算 24000 字节。

- 成功调用 4 次；累计 22754 字节；剩余 1246 字节。
- 因剩余量不足最低 4096 字节，下一次调用被拒绝。
- 返回 `RAG retrieval budget exhausted; report insufficient knowledge`。
- 只见到 SM4-GCM、AES-GCM、SM2 的部分或全部记录，尚未读完全部候选。

这是预算机制按设计工作，同时暴露全量比较的限制：前序算法源码会消耗预算，后序算法可能根本未读到。

再以 `用途 优点 缺点与限制` 为 query，对每项分别建立独立 `RagTools`，读到游标为空：

| 服务 | 完整读取页数 | 累计字节 |
|---|---:|---:|
| SM4-GCM | 3 | 12893 |
| AES-GCM | 2 | 8559 |
| SM2 | 2 | 7891 |
| RSA | 1 | 4696 |
| Ed25519 | 1 | 2024 |
| SM3 | 1 | 5869 |
| HKDF | 1 | 4429 |
| X.509 | 2 | 7359 |

所有单项查询均完整返回描述。以上是 8 个独立预算实验，不能把各项相加后宣称一个任务也可完成。字节数会随 query、top_k、语料及描述版本变化。

## 6. 长任务、MCP 与演示

### 6.1 长任务

测试用 16000 字符 scenario，断言 Agent 收到原文；Agent 另外发出不超过 512 字符的检索问题。原 PiRunner 接入测试也使用 16000 字符任务及真实子进程。

结果：通过。旧版由于将资料追加到 scenario 而超过 16000 字符的问题，不再出现在新路径。但模型上下文还包含系统提示、工具定义、历史与工具结果，未做真实模型总 token 测量。

### 6.2 MCP 和原 Guard

stdio MCP 测试实际启动 `python -m uav_rag.mcp`，发送 initialize、tools/list、tools/call，并通过真实 HTTP 取数据。合法调用返回页面，过长查询返回工具错误，测试令牌未出现在输出。

原 Guard 测试检查原有工具仍存在、查询前置条件有效、任意 shell 工具不能被调用。原 PiRunner 测试中的回执由替身写入；不是实际模型通过完整生产 Guard 提交，也没有执行服务切换。

### 6.3 演示

```powershell
python -X utf8 demo.py
```

结果：2 页，`selected_services=[auth-sm2, sm4-gcm-v1]`，`state=planned`，仍需执行验证。输出保存在 [data/demo-result.json](data/demo-result.json)。再次运行会更新该文件。

演示 Agent 预设要查询和选择的服务。它证明工具查询和已读引用校验可工作，不证明模型自行比较后得出同样结论。

## 7. 结果分析与未覆盖事项

1. 描述入库和传递链路正常。描述按服务 ID 获取，源码使用 BM25 检索；描述不是针对当前问题自动生成的分析。
2. 分页传输无损。Agent 是否继续翻页，是另一个需要真实模型验收的问题。
3. 预算能控制返回量，但不保证所有候选被公平覆盖。适合先缩小候选，再针对一项或少量算法查询。
4. 证据状态来自服务目录与是否命中引用，不是自动评估源码实现的完备性，更不证明部署就绪。
5. 本地 workflow 要求引用出现在已读页面且状态充分；生产组合 Guard 主要检查查询覆盖，允许资料缺失但继续交原 Guard 判断服务。两者判定条件不同。
6. Guard 的“查过”只表示返回记录包含该服务，不能证明模型读完所有限制、认真比较或采用了这些知识。
7. 测试未建立大规模检索相关性评测集，不能由两类检索断言推导召回率、选型准确率或跨场景鲁棒性。
8. 未测真实模型选型、云端和 Pod 网络、目标硬件性能、并发负载或真实密码服务运行。

后续验收参见 [云端、Agent 与 Jetson 验证指南](RAG-CLOUD-AGENT-JETSON-VALIDATION.md)。其中步骤是待执行指南，不能计入本地已通过结果。
