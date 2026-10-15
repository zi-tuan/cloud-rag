# 云端连通性、真实 Agent 工具调用与 Jetson 运行验证

更新：2026-10-13。适用于当前按需分页查询版本。

本文是操作指南，不是远端实测记录。编写时没有登录云端、运行真实模型或变更 Jetson/K8s。应按“云端本机 → Jetson 宿主机 → Agent 容器 → MCP → 真实模型 → 执行验收”依次验证，不用前一层成功替代后一层。

## 1. 路径、版本与执行位置

以下地址来自项目已有联调文档，本轮未重新确认。执行前核对真实环境；路径不一致时替换，不要盲目新建或覆盖已有服务。

| 位置 | 已有文档中的值 |
|---|---|
| 云端 SSH | `root@47.109.49.98` |
| 云端程序 | `/root/cloud-rag/app` |
| 云端数据库 | `/root/cloud-rag/app/data/gmssl.sqlite3` |
| 云端 Python | `/root/.local/bin/python3.11` |
| 云端凭证 | `/root/cloud-rag/uav-rag-test.token` |
| 云端监听 | `127.0.0.1:8787` |
| Jetson 客户端 | `/home/jetson/rag-link-test` |
| Jetson 凭证 | `/home/jetson/rag-link-test/service.token` |
| Jetson 隧道 | `127.0.0.1:18787` |
| 容器示例路径 | `/opt/uav/app`、`/rag-client/service.token`，须按实际挂载核对 |

必须使用含 `retrieval.py`、`tools.py`、`mcp.py`、`mcp_guard.py` 的当前完整包。旧发布包即使 `/health` 正常，也可能没有新接口。schema 2 数据库可继续使用；描述是否最新还须核对 `description_revision`。不要因为路径仍包含 `uav_rag` 就改 Python 包名。

旧 `RETEST-JETSON-CLOUD.md` 可辅助准备 SSH 和凭证，但其中旧测试数量、两阶段 Agent 与 enrich 验收不是新流程的验收标准。程序更新应遵循现有发布流程；本文不自动覆盖代码、重置令牌或重启业务。

## 2. 云端本机：验证代码、数据库和服务

在云端 SSH 终端执行：

```bash
cd /root/cloud-rag/app
/root/.local/bin/python3.11 --version
/root/.local/bin/python3.11 -c "import uav_rag.retrieval, uav_rag.mcp_guard; print(uav_rag.retrieval.__file__)"
/root/.local/bin/python3.11 -c "from uav_rag.index import KnowledgeBase; import json; print(json.dumps(KnowledgeBase('data/gmssl.sqlite3').metadata, ensure_ascii=False))"
ss -ltnp 'sport = :8787'
```

成功标准：Python 3.11+，导入路径为实际部署目录，schema 为 2，description_count 为 8；描述及源码版本与准备发布的数据库一致。最新本地版本值见 [测试报告](RAG-TEST-REPORT-2026-10-13.md)。

已有进程监听时先复用并检查，不启动同端口的第二个服务。仅当确认端口空闲且需要临时服务时，在这个终端执行：

```bash
export UAV_RAG_TOKEN_FILE=/root/cloud-rag/uav-rag-test.token
/root/.local/bin/python3.11 -m uav_rag serve \
  --host 127.0.0.1 --port 8787 \
  --db /root/cloud-rag/app/data/gmssl.sqlite3
```

此命令前台运行；保持终端打开。服务已由 systemd 管理时应使用已有管理流程，不要再开临时实例。沿用现有至少 32 字符的凭证，不重新生成。

另一个云端终端执行：

```bash
curl --noproxy '*' --fail --show-error --connect-timeout 5 --max-time 15 \
  http://127.0.0.1:8787/health

cd /root/cloud-rag/app
export UAV_RAG_TOKEN_FILE=/root/cloud-rag/uav-rag-test.token
/root/.local/bin/python3.11 -m uav_rag lookup "SM4-GCM 的用途和限制" \
  --services sm4-gcm-v1 --url http://127.0.0.1:8787
```

成功标准：health 返回 `status=ok`，lookup 返回 `items`、`index_revision`、`description_revision`、`next_cursor`；首项服务 ID 正确，能看到描述条目。404 常见于仍运行旧代码；401 表示服务与客户端凭证不匹配或未传入。

`/health` 不要求鉴权，也不验证一次真实检索，不能单独作为验收依据。

## 3. Jetson 宿主机：验证到云端的实际路径

### 3.1 建立或复用隧道

在 Jetson 终端 B 检查已有监听：

```bash
ss -ltnp 'sport = :18787'
```

如果没有监听，建立 SSH 转发：

```bash
ssh -N -o ExitOnForwardFailure=yes \
  -o ServerAliveInterval=30 -o ServerAliveCountMax=3 \
  -L 127.0.0.1:18787:127.0.0.1:8787 root@47.109.49.98
```

核对 SSH 主机指纹，保持此终端运行。已有监听先做查询确认，不结束未知进程。无需对公网开放 8787。若已有可信 HTTPS 入口，也可直接使用该入口；不改用公网明文 HTTP。

### 3.2 查询及反向鉴权

Jetson 另一个终端 C：

```bash
cd /home/jetson/rag-link-test
python3 --version
export UAV_RAG_URL=http://127.0.0.1:18787
export UAV_RAG_TOKEN_FILE=/home/jetson/rag-link-test/service.token
python3 -m uav_rag lookup "SM4-GCM 的用途和限制" \
  --services sm4-gcm-v1 --url "$UAV_RAG_URL"
```

这里的 python3 必须是已核对的 3.11+；否则替换为实际可用解释器，不能假设 Jetson 系统 Python 满足要求。凭证内容与云端一致，且当前执行用户可读。

接着用不带凭证的请求验证新接口确实受保护：

```bash
curl --noproxy '*' --silent --show-error --connect-timeout 5 --max-time 15 \
  -H 'Content-Type: application/json' \
  -d '{"query":"SM4","candidate_services":["sm4-gcm-v1"]}' \
  -w '\nHTTP_STATUS=%{http_code}\n' \
  http://127.0.0.1:18787/v1/knowledge/query
```

预期 401。若无凭证也成功，检查服务启动时是否确实加载令牌；不要把未配置鉴权算作验收通过。

### 3.3 完整翻页：确认能获取用途和全部优缺点

在同一 Jetson 终端执行下列只读检查。令牌从文件读取，不打印：

```bash
python3 - <<'PY'
from collections import Counter
from uav_rag.tools import RagTools, environment_client
from uav_rag.retrieval import encoded

tools = RagTools(environment_client())
args = {'query': 'SM4-GCM 的用途和限制',
        'candidate_services': ['sm4-gcm-v1'], 'top_k': 1}
fields = Counter()
seen = set()
while True:
    page = tools.invoke('uav_knowledge_query', args)
    size = len(encoded(page))
    assert size <= 6000
    print('page', tools.calls, 'bytes', size)
    print('revisions', page['index_revision'], page['description_revision'])
    for item in page['items']:
        if item['kind'] == 'description':
            fields[item['field']] += 1
            print(item['field'], item['value'])
    cursor = page['next_cursor']
    if cursor is None:
        break
    assert cursor not in seen
    seen.add(cursor)
    args['cursor'] = cursor
assert fields['purpose'] == 1
assert fields['advantages'] == 2
assert fields['limitations'] == 3
assert fields['sources'] >= 1
print('PASS: complete description', dict(fields))
PY
```

数量断言对应当前知识库，未来主动调整条目数时应同步更新预期。成功表示 Jetson 宿主机通过既定网络路径取得完整描述，尚不表示 Pod 或模型能访问。页数随 query 和知识版本改变，不要求固定为某个数字。

## 4. Jetson 上实际 Agent 容器：验证同一网络和 Python 环境

若 Agent 直接运行在宿主机，使用它的相同用户、解释器、工作目录和环境重做上一节。若 Agent 运行在 K8s Pod，必须从目标容器内部验证。

先在有集群访问配置的终端只读确认：

```bash
kubectl get nodes -o wide
kubectl get pods -A -o wide
```

根据实际输出设置，不直接照抄示例值：

```bash
RAG_NS='替换为命名空间'
RAG_POD='替换为实际PiRunner所在Pod'
RAG_CONTAINER='替换为Agent所在容器'
kubectl -n "$RAG_NS" describe pod "$RAG_POD"
kubectl -n "$RAG_NS" exec "$RAG_POD" -c "$RAG_CONTAINER" -- python3 --version
kubectl -n "$RAG_NS" exec "$RAG_POD" -c "$RAG_CONTAINER" -- \
  python3 -c 'import platform,uav_rag.tools; print(platform.machine()); print(uav_rag.tools.__file__)'
```

确认 Pod 所在节点确实是目标 Jetson，而非云端或其他 ARM 设备。`Running` 不等于 Ready，也不等于 RAG 可用；查看重启次数、事件、镜像和挂载。

普通 Pod 的 `127.0.0.1` 不指向 Jetson 宿主机。因此宿主机的 SSH 18787 隧道不会自动出现在 Pod 内。需采用已部署的同 Pod 隧道 sidecar，或容器可访问的受信 HTTPS 地址。不要为通过测试临时把整个服务暴露到公网。

进入目标容器并以核对后的值执行：

```bash
kubectl -n "$RAG_NS" exec -it "$RAG_POD" -c "$RAG_CONTAINER" -- sh
# 以下为容器内命令；路径和URL必须与实际部署一致。
cd /opt/uav/app
export UAV_RAG_URL=http://127.0.0.1:18787
export UAV_RAG_TOKEN_FILE=/rag-client/service.token
python3 -m uav_rag lookup "SM4-GCM 的用途和限制" \
  --services sm4-gcm-v1 --url "$UAV_RAG_URL"
```

只有同 Pod 隧道已存在时才使用上述回环 URL。成功标准与宿主机一致，并应在容器内复做完整翻页检查。

交互式 export 仅供诊断，不会修改已运行 PiRunner 的环境。生产还需核对 `pi-runner.json` 的 `environment` 与 `.mcp.json` 的 `env`；原 Runner 过滤继承环境，只在容器外 export 不够。只查看所需字段，不输出 API key 或令牌内容。

## 5. MCP 协议通路：与模型调用分开验收

在与 Agent 相同的解释器、工作目录和环境中执行：

```bash
python3 -m uav_rag.mcp <<'EOF'
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"rag-check","version":"1"}}}
{"jsonrpc":"2.0","method":"notifications/initialized"}
{"jsonrpc":"2.0","id":2,"method":"tools/list"}
{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"uav_knowledge_query","arguments":{"query":"SM4-GCM 的用途和限制","candidate_services":["sm4-gcm-v1"]}}}
EOF
```

预期响应 ID 为 1、2、3；通知没有响应。tools/list 包含查询工具，tools/call 的文本 JSON 包含非空 items，没有 `isError=true`。

这验证的是手动协议调用，不能写成“真实模型已调用工具”。此只读入口也不证明生产组合 Guard 配置成功。

生产 UAV 应配置 `python -m uav_rag.mcp_guard`，保留原 Guard 配置和按任务生成的回执路径。配置参考 [ON-DEMAND-RAG.md](ON-DEMAND-RAG.md)。在实际 Agent 运行会话中核对 tools/list 同时包含：

- `uav_knowledge_query`；
- 原 `uav_combination_select` / `uav_protection_plan` 等原有工具。

如果只有查询工具，说明用了只读入口；若只有原服务工具，通常是旧 Guard 未更新。不要通过复制固定 `UAV_SELECTION_FILE` 来绕过运行时任务隔离。

## 6. 真实 Agent 工具调用验收

### 6.1 使用现有真实模型运行入口

使用实际部署的 Agent 会话或现有任务入口，不能运行 `demo.py` 代替真实模型验收。入口命令、模型凭证、任务 API 和任务 ID 应以当前部署配置为准；本文不虚构任务 URL 或绕开原调度器直接写回执。

初次先在独立只读 Agent 会话中配置 `uav_rag.mcp`，只验收检索与回答，不接执行工具。可发送：

> 请调用 uav_knowledge_query 查询 SM4-GCM 的用途、优点和缺点与限制，只查询 sm4-gcm-v1。若当前页缺少所需描述，请继续翻页。根据实际返回内容回答，区分算法知识与本机实现证据；不要启动、停止或切换任何服务。

成功标准必须同时满足：

1. 真实模型运行记录中有 `uav_knowledge_query` 工具调用，而不是模型只在文字中声称查过。
2. query 不超过 512 字符，候选 ID 正确；没有把整段任务全文当 query。
3. 记录中有匹配该调用的成功响应、版本及描述字段。
4. 最终回答包含用途、优点、限制，并能与已返回字段对应。
5. 若所需字段尚未出现，发生续页，或明确承认资料尚未读全。
6. 没有把算法说明当成 Jetson 服务已运行、已完成加密或已部署的证据。

不能只检查最终回答“看起来正确”；模型可能凭已有知识回答。应保留同一任务的工具请求、工具响应和最终回复三者。

### 6.2 组合 Guard 的验收

只读验收通过后，在独立测试环境使用生产同款组合 Guard。若要触发原任务入口，它可能启动或切换服务，先确认该环境允许这些动作；不能依赖一句“只规划”覆盖原 Runner 的执行策略。

检查同一任务记录的时间顺序：

```text
查询所选认证算法、加密算法 → 收到结果
→ uav_combination_select 或 uav_protection_plan
→ 原 Guard 校验 → 结构化回执 → 执行器结果
```

查询覆盖需与最终选择对应，不能只查 SM4 却宣称也查过 SM2。原 Guard 的检查仅保证服务在已返回记录中出现，不保证描述全部读完。模型质量仍需人工对照评价。

每个任务使用独立 MCP 生命周期；重复使用长期 MCP 进程可能共享预算和已查记录。跨任务不能复用固定回执文件。

### 6.3 必测的异常场景

仅在独立测试会话或测试部署进行，不中断生产隧道。

| 场景 | 构造方法 | 通过标准 |
|---|---|---|
| 资料缺失 | 查询 auth-ed25519 | 有描述，状态仍为 not_found；不把缺资料等同于算法不安全或服务不存在 |
| 过长查询 | 工具传入 513 字符 query | 明确工具错误；模型改为简短问题，不声称检索成功 |
| 不可达地址 | 测试会话指向已确认未监听的回环端口 | 返回连接错误，不编造来源或成功回执 |
| 预算耗尽 | 独立会话连续翻页/查询 | 明确预算错误，承认剩余内容未读 |
| 长任务 | 通过允许的测试入口提交接近 16000 字符任务 | 原文保持，模型组织短 query；不因拼接资料而越过 scenario 上限 |
| 未查即选择 | 独立 Guard 测试尝试提交合法、未查过的算法组合 | 收到 query-before-selection 错误，未产生有效选择回执 |

工具错误验证与“模型收到错误后如何反应”是两项证据，都要记录。

## 7. Jetson 运行状态与验收层级

只读观察：

```bash
kubectl -n "$RAG_NS" get pod "$RAG_POD" -o wide
kubectl -n "$RAG_NS" logs "$RAG_POD" -c "$RAG_CONTAINER" --since=15m
```

这些日志不保证包含完整模型工具轨迹。当前 RAG HTTP 服务也不记录任务/请求正文，因此不能仅靠云端日志缺少查询文本判断模型未调用。若现有 Agent 没有记录工具请求和结果，先在测试环境开启其支持的会话记录方式，再验收；记录中不要包含密钥或令牌。

按真实 PiRunner 配置定位 `journal` 和 `receipts`，关联唯一任务 ID。原快照回执路径形式为 `receipts/<task_id>/selection.json`，实际根目录取配置值。不要修改日志数据库或人工写 selection.json 伪造通过。

| 层级 | 最低证据 | 尚不能证明 |
|---|---|---|
| 云端 RAG 可用 | 云端本机带凭证查询新接口成功 | Jetson 可访问 |
| Jetson 宿主机可达 | 宿主机经隧道获取完整描述 | Pod / Agent 可达 |
| Agent 容器可达 | 实际目标容器查询成功 | 模型使用了工具 |
| MCP 通路可用 | initialize/list/call 成功 | 模型自主调用和回答正确 |
| 真实 Agent 使用 RAG | 模型调用、响应、最终说明可关联 | 服务已经运行 |
| PiRunner 规划完成 | 同任务日志与合法选择回执 | 执行器已启动服务或切换成功 |
| 实际执行成功 | 原执行器回执、目标服务就绪、实际认证/通信检查 | 其他场景也能成功 |

`planned` 只是规划结果；原 PiRunner 的 `completed` 也需结合它所代表的选择阶段解释。不能仅凭这两个状态宣布整个无人机任务完成。

## 8. 常见失败定位

| 现象 | 优先检查 |
|---|---|
| health 成功，lookup 404 | 运行的是否旧包、是否重启到新代码、新 endpoint 是否存在 |
| 401 | 令牌文件是否可读、两端内容是否相同、Runner/MCP 是否收到环境变量 |
| Connection refused | 对应网络命名空间里的服务或隧道是否监听 |
| 宿主机成功，Pod 失败 | Pod localhost 与宿主机不同、隧道位置、网络策略 |
| ImportError | Python 版本、整个 uav_rag 是否入镜像、PYTHONPATH、加载的是不是旧包 |
| invalid/stale cursor | query、候选、top_k 是否变化；是否跨版本；新查询应去掉旧 cursor |
| budget exhausted | 候选过多或源码过长；缩小问题和候选，不能无限重试 |
| 模型没调用工具 | 实际 tools/list、白名单、是否错误配置 --no-tools、会话是否加载新 MCP |
| 查询成功但选型被拒绝 | 是否查过最终全部候选、原 Guard 参数或部署约束、回执路径 |
| 进程正常但没有最终回执 | 真实模型异常、工具失败、超时、任务上下文或原 Guard 验证失败 |

## 9. 验收记录模板

每一层完成后记录，没执行填“未执行”，不要直接复制本地成功值：

| 项目 | 实际记录 |
|---|---|
| 时间 / 操作者 | 待填写 |
| 云端代码版本或包哈希 | 待填写 |
| index_revision / description_revision | 待填写 |
| Jetson 型号、系统、Python | 待填写 |
| namespace / Pod / 容器 / 所在节点 / 镜像 | 待填写 |
| 实际 RAG URL（不含凭证） | 待填写 |
| 云端本机、宿主机、容器查询结果 | 分层填写 |
| MCP tools/list 和调用结果 | 待填写 |
| 真实模型 / 任务 ID | 待填写 |
| 工具调用次数、返回页数、预算错误 | 待填写 |
| 用途、优点、限制是否与返回内容一致 | 待填写 |
| Guard 回执 / 执行器结果 | 待填写或未执行 |
| 异常用例和处理结果 | 待填写 |
| 未验证项 | 待填写 |

本轮只生成此指南。云端、真实 Agent 和 Jetson 实机结果仍待按上述步骤取得。
