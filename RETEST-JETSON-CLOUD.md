# Jetson 调用云端 RAG：非首次启动、测试与结果判读

更新日期：2026-10-13。

本文适用于已经完成首次解压、文件传输和基础测试之后的重复联调。用户反馈本轮测试可能已成功，但尚未提供实际接口响应供复核；本文中的返回值是依据当前代码和发布库给出的预期，不是本次远端运行记录。

## 1. 固定路径与测试范围

云端地址：`47.109.49.98`，当前测试使用 SSH 账号 `root`。

| 所在机器 | 路径或地址 | 用途 |
|---|---|---|
| 云端 | `/root/cloud-rag/app` | 已解压的程序目录 |
| 云端 | `/root/cloud-rag/app/data/gmssl.sqlite3` | 知识库数据库 |
| 云端 | `/root/cloud-rag/uav-rag-test.token` | 服务端凭证 |
| 云端 | `127.0.0.1:8787` | RAG 监听地址 |
| Jetson | `/home/jetson/rag-link-test/uav_rag` | 客户端代码目录 |
| Jetson | `/home/jetson/rag-link-test/service.token` | 与云端内容一致的凭证 |
| Jetson | `127.0.0.1:18787` | SSH 隧道入口 |

本文假定 Jetson 用户是 `jetson`、目录与上表一致；如实际不同，请修改对应路径。若云端没有 `app` 目录，先找回实际解压目录，不要直接执行启动命令，也不必重复上传。

测试链路：

```text
Jetson 查询客户端 / curl
    → Jetson 127.0.0.1:18787
    → SSH 加密隧道
    → 云端 127.0.0.1:8787
    → 凭证检查 → SQLite 检索 → 返回引用资料
```

这证明宿主机上的接口调用，不证明 k8s Pod 联通、真实 Agent 两次决策、服务下载、镜像导入、服务启动或无人机任务执行。普通 Pod 的 localhost 不等于宿主机 localhost。

## 2. 非首次测试不需要做什么

- 不需要重复上传 ZIP、解压、重建数据库或传输 GmSSL 源码。
- 不需要重新生成凭证；重新生成会导致 Jetson 旧凭证失效。
- 不需要每次运行全部单元测试；代码、数据库或运行环境更新后再做回归。
- 已有正常服务或隧道时不要再启动同端口实例。
- 不要替换云端系统默认 Python；继续使用已验证的 `/root/.local/bin/python3.11`。

需要恢复的是可能随终端关闭而结束的 RAG 进程、SSH 隧道，以及新终端里的环境变量。文件不会因为退出 SSH 自动消失。

## 3. 终端分工

| 终端 | 操作位置 | 是否保持打开 |
|---|---|---|
| A | 登录云端，检查并前台启动 RAG | 服务由本终端启动时，保持打开 |
| B | Jetson，建立 SSH 隧道 | 保持打开 |
| C | Jetson，执行接口测试和保存结果 | 用于发请求 |

以下代码块为 Linux shell 命令。多行末尾的 `\` 表示命令还没结束，反斜杠后不要添加空格。不要复制终端提示符。

## 4. 终端 A：检查并恢复云端 RAG

### 4.1 登录并确认文件

```bash
ssh root@47.109.49.98
```

目的：进入云端终端。首次出现主机指纹提示时需核对，指纹发生变化时不要直接忽略。以下命令在登录后的云端执行。

```bash
cd /root/cloud-rag/app
pwd
ls -ld uav_rag
ls -lh data/gmssl.sqlite3
```

逐条说明：

- `cd` 切换到程序目录，使 Python 能找到 `uav_rag` 包。若失败，停止，不要继续。
- `pwd` 显示当前路径，应为 `/root/cloud-rag/app`。
- `ls -ld` 查看代码目录本身是否存在。
- `ls -lh` 查看数据库是否存在和文件大小；文件存在本身不等于查询成功。

```bash
test -s /root/cloud-rag/uav-rag-test.token && echo "Token file OK"
chmod 600 /root/cloud-rag/uav-rag-test.token
```

- `test -s` 检查凭证存在且非空；`&&` 表示检查通过才打印提示。没有提示时先停止并排查。
- `chmod 600` 让凭证只有文件所有者可读写，不会改变内容。
- 不要用 `cat` 打印凭证，不要把它提交到仓库或聊天。

### 4.2 检查是否已经运行

```bash
ss -ltnp 'sport = :8787'
```

- `-l`：仅显示监听中的 socket。
- `-t`：TCP。
- `-n`：直接显示数字端口。
- `-p`：显示关联进程信息（权限允许时）。
- 过滤条件限定本机监听端口 `8787`。

只有表头、没有记录：端口未监听，可以进入 4.3。

如果已有监听记录，先执行：

```bash
curl --noproxy '*' --fail --show-error \
  --connect-timeout 5 --max-time 10 \
  http://127.0.0.1:8787/health
```

这是云端自测，尚未经过 Jetson。参数说明见第 6 节。如果返回预期健康信息，先复用现有服务，继续 Jetson 测试；后面还必须验证鉴权和真实检索。若不是预期服务，不要直接结束未知进程。

### 4.3 仅在没有服务监听时启动

```bash
export UAV_RAG_TOKEN_FILE=/root/cloud-rag/uav-rag-test.token
/root/.local/bin/python3.11 -m uav_rag serve \
  --host 127.0.0.1 \
  --port 8787 \
  --db /root/cloud-rag/app/data/gmssl.sqlite3
```

| 语句/参数 | 目的 |
|---|---|
| `export UAV_RAG_TOKEN_FILE=...` | 告诉随后启动的程序从哪个文件读取凭证；新 SSH 会话需要重新设置 |
| `/root/.local/bin/python3.11` | 使用已验证的解释器，不使用系统 Python 3.8 |
| `-m uav_rag serve` | 运行包的命令入口，启动检索 HTTP 服务 |
| `--host 127.0.0.1` | 只在云端回环地址监听，避免直接向公网暴露 |
| `--port 8787` | 指定服务端口 |
| `--db ...` | 指定已经构建的知识库，不执行重新建库 |

预期启动提示：

```text
RAG listening on 127.0.0.1:8787
```

此提示表明程序完成初始化并绑定端口，不代表 Jetson 已访问成功。终端不再返回命令提示符是正常现象。保持终端 A 打开，`Ctrl+C` 会停止服务；本流程没有配置后台守护或开机启动，不能依赖断开 SSH 后服务继续运行。

## 5. 终端 B：恢复 Jetson SSH 隧道

在 Jetson 上先检查：

```bash
ss -ltnp 'sport = :18787'
```

如果已经有监听，先通过第 6 节验证是否为可用的旧隧道，不要重复创建；若无法确认进程，先排查，不要强行结束。

如果没有监听，在 Jetson 终端 B 执行：

```bash
ssh -N \
  -o ExitOnForwardFailure=yes \
  -o ServerAliveInterval=30 \
  -o ServerAliveCountMax=3 \
  -L 127.0.0.1:18787:127.0.0.1:8787 \
  root@47.109.49.98
```

| 参数 | 目的 |
|---|---|
| `-N` | 不运行远程 shell 命令，仅建立转发连接 |
| `ExitOnForwardFailure=yes` | 初始转发监听无法建立时退出，避免误认为隧道正常；它不保证云端 RAG 一定可达 |
| `ServerAliveInterval=30` | SSH 在无数据接收时按此间隔发送存活探测 |
| `ServerAliveCountMax=3` | 连续探测无响应达到限制后结束连接，不是自动重连 |
| `-L ...` | 将 Jetson 的回环端口转发到云端的回环端口 |
| `root@47.109.49.98` | SSH 目标主机和身份 |

`-L` 的完整含义：`Jetson监听地址:Jetson监听端口:从云端访问的目标地址:目标端口`。

完成验证身份后，没有输出且进程持续运行是正常现象，但不是接口成功证据。保持终端 B 打开。无需开放公网 `8787`，客户端继续使用 `http://127.0.0.1:18787`，不要改为公网明文 HTTP。

## 6. 终端 C：健康检查

在 Jetson 执行：

```bash
curl --noproxy '*' --fail --show-error \
  --connect-timeout 10 --max-time 30 \
  http://127.0.0.1:18787/health
```

| 参数 | 目的 |
|---|---|
| `--noproxy '*'` | 绕过环境变量里的代理，直接访问本机隧道 |
| `--fail` | HTTP 4xx/5xx 时让 curl 返回失败；可能不显示错误响应正文 |
| `--show-error` | 显示错误信息 |
| `--connect-timeout 10` | 建立连接最多等待 10 秒 |
| `--max-time 30` | 整个请求最多等待 30 秒 |
| `/health` | 查询服务健康和当前加载的索引版本 |

当前发布库的预期响应：

```json
{
  "status": "ok",
  "index_revision": "eea0294c300203072799024c233d82671b1cc91976ae7f159ce4c66323bd4021"
}
```

判读：

- `status: ok`：通过这个地址确实获得了 RAG 健康接口响应。
- `index_revision`：当前服务加载的语料版本，可与检索响应中的 `corpus.revision` 对照。
- 此接口只有上述两个字段，不返回文件数或片段数；它们在检索响应中。
- `/health` 不要求凭证，也不执行完整检索，所以单凭它不能证明鉴权和数据库检索都正常。
- 更新资料并重新建库后版本可能变化，不应机械要求永远等于这里的值；重建后应重启服务。

## 7. 鉴权反向检查：不带凭证应该失败

在 Jetson 执行：

```bash
curl --noproxy '*' --silent --show-error \
  --connect-timeout 10 --max-time 30 \
  -w '\nHTTP_STATUS=%{http_code}\n' \
  http://127.0.0.1:18787/v1/services
```

这里故意不传凭证、不使用 `--fail`，以便看到预期的拒绝正文：

```text
{"error": "unauthorized"}
HTTP_STATUS=401
```

- `--silent` 隐藏进度条，配合 `--show-error` 保留错误信息。
- `-w` 在响应后打印 HTTP 状态码。
- `/v1/services` 是需要鉴权的目录接口，读取它不会启动或改变服务。
- `401` 是这项反向测试的成功结果：无凭证请求被拒绝。
- 如果返回 `200`，当前服务很可能未启用凭证检查，应先确认运行实例和启动配置，不能声称鉴权验收通过。
- 因为没有 `--fail`，curl 的进程退出码为 0 不代表 HTTP 为 200；这里以打印的状态码为准。

## 8. 带凭证检索，并保存本次证据

### 8.1 推荐：curl 测试（不依赖 Jetson Python 版本）

在 Jetson 终端 C 执行：

```bash
cd /home/jetson/rag-link-test
test -s service.token && echo "Token file OK"
chmod 600 service.token
umask 077
mkdir -p results
RUN_ID="jetson-rag-$(date -u +%Y%m%dT%H%M%S)-$$"
echo "$RUN_ID"
```

逐条说明：

- `cd`：进入已有客户端目录；失败时停止。
- `test -s`：确认凭证非空；没有成功提示时停止。
- `chmod 600`：限制凭证权限。
- `umask 077`：本会话后续新建结果文件默认不向其他用户开放，避免任务资料外泄。
- `mkdir -p results`：创建结果目录，已存在时保留其中内容。
- `RUN_ID=...`：使用 UTC 时间和当前 shell 进程号生成本次标识，避免多次结果混淆；变量仅在当前终端有效。
- `echo`：打印非敏感任务编号，方便核对，不打印凭证。

发送查询：

```bash
printf 'Authorization: Bearer %s\n' "$(tr -d '\r\n' < service.token)" |
curl --noproxy '*' --silent --show-error \
  --connect-timeout 10 --max-time 30 \
  -H @- \
  -H 'Content-Type: application/json' \
  --data "{\"task_id\":\"$RUN_ID\",\"query\":\"无人机需要国密身份签名和 GCM 通信加密\",\"candidate_services\":[\"SM2\",\"SM4-GCM\",\"RSA\",\"Ed25519\"],\"top_k\":2}" \
  -o "results/$RUN_ID.json" \
  -w '\nHTTP_STATUS=%{http_code}\n' \
  http://127.0.0.1:18787/v1/services/enrich
```

| 语句/参数 | 目的 |
|---|---|
| `tr -d '\r\n' < service.token` | 读取凭证并去掉文件末尾换行，兼容 Windows 换行 |
| `printf ...` 和管道 `\|` | 生成 Authorization 请求头，通过标准输入交给 curl，不主动打印到终端 |
| `-H @-` | 从标准输入读取请求头；凭证不直接写在 curl 参数中 |
| `Content-Type: application/json` | 告诉服务端请求正文是 JSON |
| `--data` | 发送 JSON 正文，并使请求使用 POST |
| `task_id` | 本次请求标识，返回时必须一致 |
| `query` | 场景描述，参与资料检索 |
| `candidate_services` | 指定要补充资料的候选服务，不是启动指令 |
| `top_k: 2` | 每个服务最多返回 2 条引用，不保证每项都有 2 条 |
| `-o ...` | 把响应正文保存为本次 JSON 文件；HTTP 错误正文也可能写入，应先看状态码 |
| `-w ...` | 在终端打印 HTTP 状态，成功应为 200 |

不要启用 `set -x` 或带敏感请求头的调试日志。此命令不把凭证保存到结果文件。若 curl 不支持 `-H @-`，改用下面的 Python 客户端，不要把凭证直接粘贴到命令中。

预期终端提示：

```text
HTTP_STATUS=200
```

随后查看响应文件（这里只读取文件，不执行 Python 代码，常见 Python 3 版本即可）：

```bash
python3 -m json.tool "results/$RUN_ID.json"
```

如需更易读的中文输出且当前 Python 支持该选项：

```bash
python3 -m json.tool --no-ensure-ascii "results/$RUN_ID.json"
```

`json.tool` 负责检查 JSON 格式并缩进显示，不会验证业务字段是否正确；中文显示成 `\u...` 也不是接口失败。若网络失败、状态码非 200 或文件不完整，不要只看文件存在就判定成功。

### 8.2 可选：再验证项目 Python 客户端

先确认解释器：

```bash
python3 --version
```

项目命令按 Python 3.11+ 交付；低于此版本时可继续使用 8.1，不必替换系统 Python。如果解释器名称为 `python3.11`，相应替换下列命令。

```bash
cd /home/jetson/rag-link-test
export UAV_RAG_TOKEN_FILE=/home/jetson/rag-link-test/service.token
python3 -m uav_rag query \
  "无人机需要国密身份签名和 GCM 通信加密" \
  --services SM2 SM4-GCM RSA Ed25519 \
  --task-id jetson-client-retest-001 \
  --url http://127.0.0.1:18787
```

- `export` 指定客户端读取的凭证文件，每个新终端需要重新设置。
- `query` 发出检索请求，不启动服务、不下载镜像、不调用真实 Agent。
- `--services` 是候选服务列表，服务端会转成标准服务 ID。
- `--task-id` 指定请求编号，客户端会校验响应编号和服务列表是否匹配。
- `--url` 指向 Jetson 的隧道入口，而非云端公网明文 HTTP。
- 该命令默认每项最多取 3 条引用，与 curl 的 `top_k:2` 不同，引用条数不同不代表故障。

curl 测试成功证明接口链路可用；项目客户端也成功，则额外证明当前 Jetson 的 Python 客户端可以按协议调用。

## 9. 返回结果中哪些数据证明成功？

### 9.1 请求与响应对应

响应顶层应有：

```json
{
  "task_id": "与本次 RUN_ID 完全一致",
  "corpus": {
    "schema": 1,
    "corpus": "GmSSL",
    "revision": "eea0294c300203072799024c233d82671b1cc91976ae7f159ce4c66323bd4021",
    "documents": 401,
    "chunks": 3986,
    "retrieval": "SQLite FTS5 BM25; Chinese bigrams and controlled query expansion"
  }
}
```

这是摘录示意，实际响应还包含 `services`、`context_policy` 等字段。

| 字段 | 可以说明什么 | 不能说明什么 |
|---|---|---|
| `task_id` | 响应关联了本次查询；应与终端打印的 RUN_ID 相同 | 不表示无人机业务任务已经执行 |
| `corpus.corpus` | 使用名为 GmSSL 的知识资料库 | 不表示所有算法都具备足够资料 |
| `revision` | 与 `/health` 版本相同，可核对加载的语料版本 | 不是响应数字签名，也不是凭证 |
| `documents:401` | 当前发布库入库文件数 | 健康接口本身不返回此数 |
| `chunks:3986` | 当前发布库的检索片段数 | 不表示这次返回了 3986 条结果 |
| `retrieval` | 当前采用 SQLite FTS5/BM25 检索 | 不是向量模型或大模型生成结果 |

更新语料后数量和版本可以合理变化，应与所部署版本对照，不要把旧值当成永久标准。

### 9.2 候选服务状态

当前请求应返回以下标准 ID：

| 输入别名 | 响应 `service_id` | 预期 `evidence_status` | 含义 |
|---|---|---|---|
| SM2 | `auth-sm2` | `available` | 当前库有对应技术资料 |
| SM4-GCM | `sm4-gcm-v1` | `available` | 当前库有对应技术资料 |
| RSA | `auth-rsa` | `insufficient` | 当前 GmSSL 资料不足以证明所需 RSA 认证实现 |
| Ed25519 | `auth-ed25519` | `not_found` | 当前库没有该服务的对应证据 |

RSA 与 Ed25519 的状态是预期知识边界，不是网络错误，也不表示无人机原服务不存在。`available` 只说明知识证据可用，不代表镜像存在、Pod 就绪或服务已经启动。

还可检查 `binding`：`auth-sm2` 的 `auth_policy` 应为 `SM2`；`sm4-gcm-v1` 的 `suite` 应为 `SM4-GCM`。这些是与无人机服务配置对应的映射，不是执行回执。

### 9.3 引用是否具有实际内容

SM2 和 SM4 的 `citations` 应为非空列表，逐项检查：

| 引用字段 | 解释 |
|---|---|
| `path` | GmSSL 资料库内的相对路径，例如 `include/gmssl/sm2.h`；不是 Jetson 上必须存在的文件 |
| `start_line` / `end_line` | 引用原文的行号范围，用于定位复核 |
| `text` | 实际检索到的原文，如 SM2 签名/验签声明、SM4-GCM 接口或实现 |
| `sha256` | 整个源文件的 SHA-256，不是该段文字的哈希，也不是镜像摘要 |
| `id` | 由源文件路径、哈希和行号等生成的片段标识 |
| `score` | 排序相关性分值，不是准确率、概率或可信百分比，无统一“成功阈值” |

HTTP 200、正确任务编号、正确服务列表和相关的非空引用一起，才能支持“此次真实检索成功”的判断。仅返回 `available` 字样不够。片段顺序、行号和分数可能随查询变化，不必与示例完全一致。

哈希字段提供复核线索，不表示客户端自动验证了引用原文；若要更严格验证，应与云端同版本 `sources/GmSSL` 中的文件及行号比对。

### 9.4 为什么 `artifact.available: false` 不是检索失败？

当前没有登记可下载服务归档时，预期可能看到：

```json
{
  "artifact": {
    "available": false,
    "reason": "no_registered_service_artifact"
  }
}
```

它表示云端没有登记该服务的可下载包。检索资料与分发镜像是两件事；本轮不测试下载，因此这不影响检索验收。本文启动命令也没有加载额外的归档登记文件。

## 10. 本轮验收清单

- [ ] Jetson `/health` 返回 `status: ok` 和预期索引版本。
- [ ] 不带凭证访问 `/v1/services` 返回 HTTP 401。
- [ ] 带正确凭证 POST `/v1/services/enrich` 返回 HTTP 200。
- [ ] 返回 `task_id` 等于本次 RUN_ID。
- [ ] `corpus.revision` 与健康接口版本一致，库名称和规模符合当前部署。
- [ ] 返回四个预期标准服务 ID，没有遗漏或混入其他服务。
- [ ] SM2、SM4 有相关、非空的引用原文及出处。
- [ ] RSA 证据不足、Ed25519 未找到证据，符合当前知识范围。
- [ ] 已保留 `results/<RUN_ID>.json`，记录时间和测试位置为 Jetson 宿主机。
- [ ] 如需验收项目客户端，Python 客户端命令也已成功。

满足前九项可记录为：“Jetson 宿主机经 SSH 隧道访问云端 RAG 的健康检查、无凭证拒绝、带凭证检索及响应关联验证通过。”不要写成“整个无人机 Agent 到 k8s 服务启动链路通过”。

## 11. 测试结束与下次恢复

保存结果后，如果本轮专门启动的进程不需要继续使用：

1. 在 Jetson 隧道终端 B 按 `Ctrl+C`，停止该隧道。
2. 在云端 RAG 终端 A 按 `Ctrl+C`，停止本轮前台服务。
3. 不删除数据库、凭证或结果文件。若复用的是他人或其他任务的进程，不要为结束本轮测试而停止它。

下次顺序仍是：检查云端服务 → 必要时启动 → 检查 Jetson 隧道 → 必要时建立 → 健康检查 → 鉴权检查 → 带凭证检索 → 保存和判读结果。

## 12. 故障定位

| 现象 | 检查方向 |
|---|---|
| SSH 无法登录 | 网络、SSH 身份验证、主机指纹；不是 RAG 数据库问题 |
| `Address already in use` | 对应机器上的端口已占用，先用 `ss` 确认，不要重复启动或直接杀进程 |
| Jetson 连接 `18787` 被拒绝 | 隧道未启动、已退出或监听地址不对 |
| 隧道提示 `connect failed: Connection refused` | SSH 本身可能已通，但云端 `8787` 没有 RAG 监听 |
| `/health` 成功但检索 401 | 凭证不一致、客户端文件路径错误或访问了旧服务实例 |
| 无凭证请求返回 200 | 可能未启用鉴权；检查服务实际启动配置 |
| HTTP 400 | 请求字段、JSON、服务名称格式、top_k 等不合法 |
| HTTP 404 | 接口路径错误或访问到其他服务 |
| HTTP 415 | 缺少或错误设置 `Content-Type: application/json` |
| HTTP 503 | 服务内部资源不可用，检查数据库路径、权限及运行实例 |
| HTTP_STATUS=000 | 没有获得 HTTP 响应，应读 curl 错误，不能按成功处理 |
| `No module named uav_rag` | 当前目录不对、包未传完整或解释器环境不正确 |
| `index missing` | 云端数据库路径错误或文件不存在 |
| 中文出现 `\u...` | JSON 转义显示，通常不是数据损坏 |
| 有 JSON 文件但状态非 200 | 文件可能保存了错误正文，不是成功证据 |

注意：`demo.py` 会启动它自己的本地演示服务，不能用来证明 Jetson 到云端的链路；本次应以这里明确指向 `127.0.0.1:18787` 的远端隧道请求为准。
