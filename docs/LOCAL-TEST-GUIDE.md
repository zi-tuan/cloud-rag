# Windows 本地测试操作手册

适用目录：`E:\claudeCode\project\Intra-group-project\uav-cloud`。以下使用 PowerShell，按顺序执行即可。当前阶段不需要云主机、SSH、镜像仓库、树莓派或 Jetson 信息，也不执行上传、镜像推送或 kubectl 操作。

这里测试的是实际本地 RAG HTTP、SQLite、客户端、MCP 和业务代码。完整自动联调使用**确定性替身 Agent、模拟 Kubernetes/就绪条件**；它能验证工具调用和协议流转，不能证明真实模型、设备、容器或云边网络已通过。

## 1. 进入项目并检查环境

打开 PowerShell，执行：

```powershell
Set-Location 'E:\claudeCode\project\Intra-group-project\uav-cloud'
$Python = Join-Path (Get-Location) '.venv312\Scripts\python.exe'
Test-Path $Python
& $Python --version
& $Python -X utf8 -c "import sqlite3, fastapi, httpx, cryptography, yaml, pymavlink; print('dependencies OK'); print('SQLite:', sqlite3.sqlite_version)"
```

预期：路径检查为 `True`，Python 为 3.12，输出 `dependencies OK`。当前机器已准备好 `.venv312`，通过后**不用重新创建或安装环境**。直接调用解释器即可，不需要执行 Activate.ps1 或修改 PowerShell 执行策略。

如果环境不存在，才在已安装标准 Python 3.12 的机器执行：

```powershell
py -0p
py -3.12 -m venv .venv312
$Python = Join-Path (Get-Location) '.venv312\Scripts\python.exe'
& $Python -m pip install -r requirements-dev.txt
```

若提示缺少 pip，标准 Python 环境可执行 `& $Python -m ensurepip --upgrade` 后重试。当前已准备的环境来自应用内 Python 运行时，可能没有 pip，但已安装的测试依赖可以直接使用；不要因此删除正常环境。若必须重新安装，优先使用标准 Python 3.12 新建另一个环境。

若安装失败，先记录 Python/pip 版本、具体错误和所用软件源；TLS 错误不应通过关闭证书校验解决。之前旧 Python/pip 的 TLS 失败在换用新版运行时后解决。

完整业务回归还需要 OpenSSL 3：

```powershell
Get-Command openssl -ErrorAction SilentlyContinue
openssl version
```

当前机器可用路径为 `C:\msys64\mingw64\bin\openssl.exe`。PATH 找不到时，可以针对本终端指定：

```powershell
$env:UAV_TEST_OPENSSL_BINARY = 'C:\msys64\mingw64\bin\openssl.exe'
Test-Path $env:UAV_TEST_OPENSSL_BINARY
& $env:UAV_TEST_OPENSSL_BINARY version
```

预期为 OpenSSL **3.x**。若你的安装路径不同，请改成真实路径。RAG 知识语料版本 4.2.0-dev 并不是这份已安装二进制，两者不要混淆。

## 2. 建立本次结果目录

在当前终端执行：

```powershell
$RunDir = Join-Path (Get-Location) ('data\manual-test-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
New-Item -ItemType Directory -Path $RunDir | Out-Null
$RunDir
```

后面的日志保存到这个目录。不同终端的变量不共享；打开新终端时需按步骤重新设置 `$Python`、地址和令牌路径。

## 3. 检查数据库、FTS5 和知识版本

```powershell
& $Python -X utf8 manage.py doctor | Tee-Object -FilePath (Join-Path $RunDir 'doctor.json')
if ($LASTEXITCODE -ne 0) { throw '数据库检查失败，请先处理错误再继续。' }
```

当前交付版本预期：

| 字段 | 预期值 |
|---|---|
| fts5 | true |
| knowledge.schema | 3 |
| knowledge.corpus | OpenSSL |
| knowledge.documents | 3371 |
| knowledge.chunks | 24798 |
| knowledge.description_count | 8 |
| knowledge.revision | `268ed22e75536cdddb1ff1cb8af5672b7852c8ed4299e1891d2267fa1a728059` |

`doctor` 还检查 SQLite 完整性、代码服务目录、描述和算法文档是否与数据库一致。未来正常更新知识后 revision/数量可能变化，应与新版本说明核对。

**已有数据库且检查通过时，不必重建。** 如果只想验证索引可以独立重建，使用新文件名：

```powershell
$RebuiltDb = Join-Path $RunDir 'openssl-rebuilt.sqlite3'
& $Python -X utf8 manage.py index --source sources/openssl --db $RebuiltDb
if ($LASTEXITCODE -ne 0) { throw '索引重建失败。' }
```

预期：生成新数据库并输出 schema、revision、文档数等；未修改语料时 revision 应与当前库一致。重复使用同一目标文件会被拒绝，这是防覆盖机制。

缺失默认数据库时，可执行 `manage.py index --db data/openssl.sqlite3` 首次创建。如果默认数据库已存在但校验失败，先保留该文件与错误日志，再构建新文件并单独启动检查，**不要直接覆盖正在使用的库**。`doctor`、完整联调和部分测试读取默认 `data/openssl.sqlite3`；手工使用 `--db` 启动另一文件不会自动改变这些入口。

## 4. 启动有鉴权的本地 RAG：终端 A

以下命令只监听本机。生成的是本地测试令牌，保存于被忽略的 data 目录，不打印内容。

```powershell
Set-Location 'E:\claudeCode\project\Intra-group-project\uav-cloud'
$Python = Join-Path (Get-Location) '.venv312\Scripts\python.exe'
New-Item -ItemType Directory -Path 'data\local-test' -Force | Out-Null
& $Python -c "from pathlib import Path; import secrets; p=Path('data/local-test/service.token'); p.write_text(secrets.token_urlsafe(32),encoding='utf-8') if not p.exists() else None"
$env:UAV_RAG_TOKEN_FILE = (Resolve-Path 'data\local-test\service.token').Path
$env:UAV_RAG_URL = 'http://127.0.0.1:8787'
& $Python -X utf8 manage.py serve --db data/openssl.sqlite3 --host 127.0.0.1 --port 8787
```

预期：输出 `RAG listening on 127.0.0.1:8787`，终端保持运行。**此时不要在终端 A 输入其他测试命令**，另开终端 B。

如果 8787 被占用：先用 `Get-NetTCPConnection -LocalPort 8787 -ErrorAction SilentlyContinue` 查看，不要盲目结束未知进程。可将服务端口改为 18787，同时将后续所有请求和 `UAV_RAG_URL` 改为同一端口。

若希望手工测试重建库，将 `--db` 后的路径替换成第 3 步生成的数据库路径。

## 5. 健康检查和真实 HTTP 查询：终端 B

先设置该终端的环境：

```powershell
Set-Location 'E:\claudeCode\project\Intra-group-project\uav-cloud'
$Python = Join-Path (Get-Location) '.venv312\Scripts\python.exe'
$env:UAV_RAG_URL = 'http://127.0.0.1:8787'
$env:UAV_RAG_TOKEN_FILE = (Resolve-Path 'data\local-test\service.token').Path
```

健康检查不需要令牌：

```powershell
Invoke-RestMethod -Uri "$env:UAV_RAG_URL/health" | ConvertTo-Json -Depth 10
```

预期返回当前知识版本。注意：**health 成功不等于查询鉴权或 Agent 接入成功**。

使用融合后的边端客户端：

```powershell
& $Python -X utf8 -m tools.probe_rag
& $Python -X utf8 -m uav_rag lookup 'SM4-GCM 的用途、限制和源码依据' --services sm4-gcm-v1 --url $env:UAV_RAG_URL
```

预期：probe 输出 `query_items > 0`、index_revision；lookup 返回 items、index_revision、description_revision、next_cursor。items 包括 `service`、`description` 或 `citation`；单页可能没有全部类型，需要按需翻页。源码可用不代表镜像已部署或设备 Ready。

### 直接观察 POST 请求与分页

```powershell
$Headers = @{ Authorization = 'Bearer ' + [IO.File]::ReadAllText($env:UAV_RAG_TOKEN_FILE).Trim() }
$Request = @{
    query = 'SM4-GCM 的用途和限制'
    candidate_services = @('sm4-gcm-v1')
    top_k = 1
    max_bytes = 6000
}
$Body = [Text.Encoding]::UTF8.GetBytes(($Request | ConvertTo-Json -Depth 10))
$Page1 = Invoke-RestMethod -Method Post -Uri "$env:UAV_RAG_URL/v1/knowledge/query" -Headers $Headers -ContentType 'application/json; charset=utf-8' -Body $Body
$Page1 | ConvertTo-Json -Depth 20

if ($Page1.next_cursor) {
    $Request.cursor = $Page1.next_cursor
    $Body = [Text.Encoding]::UTF8.GetBytes(($Request | ConvertTo-Json -Depth 10))
    $Page2 = Invoke-RestMethod -Method Post -Uri "$env:UAV_RAG_URL/v1/knowledge/query" -Headers $Headers -ContentType 'application/json; charset=utf-8' -Body $Body
    $Page2 | ConvertTo-Json -Depth 20
}
```

预期：续页有同一知识版本；非空 next_cursor 表示仍有后续页。续页必须保持 query、candidate_services 和 top_k 不变。这里的手工 HTTP 请求不经过 Agent 工具的任务预算；预算限制应按第 7、9 步验证。

不要打印 `$Headers` 或令牌文件内容，也不要将它们保存进报告。

## 6. 验证鉴权失败和游标错误

仍在终端 B，用以下命令检查无令牌查询：

```powershell
try {
    $null = Invoke-RestMethod -Method Post -Uri "$env:UAV_RAG_URL/v1/knowledge/query" -ContentType 'application/json' -Body '{"query":"SM4 GCM","candidate_services":["sm4-gcm-v1"]}' -ErrorAction Stop
    throw '异常：无令牌查询竟然成功，请检查服务是否按第 4 步启动。'
} catch {
    if ($_.Exception.Response) {
        $Status = [int]$_.Exception.Response.StatusCode
        "HTTP status: $Status"
        if ($Status -ne 401) { throw }
    } else { throw }
}
```

预期 `HTTP status: 401`。然后检查错误游标：

```powershell
try {
    $BadBody = '{"query":"SM4 GCM","candidate_services":["sm4-gcm-v1"],"cursor":"invalid-cursor"}'
    $null = Invoke-RestMethod -Method Post -Uri "$env:UAV_RAG_URL/v1/knowledge/query" -Headers $Headers -ContentType 'application/json' -Body $BadBody -ErrorAction Stop
    throw '异常：失效游标竟然成功。'
} catch {
    if ($_.Exception.Response) {
        $Status = [int]$_.Exception.Response.StatusCode
        "HTTP status: $Status"
        if ($Status -ne 400) { throw }
    } else { throw }
}
```

预期 `HTTP status: 400`，而不是伪造正常资料。断网、超时、未知服务、版本变化、空证据及预算耗尽通过第 9 步自动测试验证，不需要故意破坏现有配置。

## 7. 手工验证 MCP 工具发现与调用

终端 B 保持第 5 步的 URL 和令牌文件环境，执行：

```powershell
@(
    '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05"}}'
    '{"jsonrpc":"2.0","id":2,"method":"tools/list"}'
    '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"uav_knowledge_query","arguments":{"query":"SM4 GCM limits","candidate_services":["sm4-gcm-v1"],"top_k":1}}}'
) | & $Python -X utf8 -m uav_rag.mcp
```

预期依次返回 id 1、2、3 的 JSON-RPC 响应；tools/list 包含 `uav_knowledge_query`；tools/call 的 `result.content[0].text` 内是知识页 JSON。`isError=true` 表示工具失败，不是正常知识结果。

这里是**只读 MCP**，不含原 Guard 执行工具。组合 Guard、提交引用和执行器接入应继续验证下一步，不能只凭这一步判定 Agent 成功。

## 8. 完整自动联调：PiRunner → Agent → MCP/Guard → 执行器

回到第 2 步建立 `$RunDir` 的终端，或者在终端 B 重新建立一个时间戳目录。自动联调自行创建临时令牌和随机回环端口，不依赖手工启动的 8787 服务，可以并行保留终端 A。

```powershell
# 如果换了终端，先补设本次结果目录：
if (-not $RunDir) {
    $RunDir = Join-Path (Get-Location) ('data\manual-test-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
    New-Item -ItemType Directory -Path $RunDir | Out-Null
}
# 联调会更新固定报告名，先保留上一次证据：
if (Test-Path 'data\local-integration.json') {
    Copy-Item 'data\local-integration.json' (Join-Path $RunDir 'previous-integration.json')
}
& $Python -X utf8 manage.py integration
if ($LASTEXITCODE -ne 0) { throw '完整联调失败，请保留错误输出。' }
Copy-Item 'data\local-integration.json' (Join-Path $RunDir 'local-integration.json')
```

当前语料下预期摘要：

```json
{"query_pages":3,"runner":"completed","executor_simulation":"succeeded"}
```

检查完整任务及决策，不要只看摘要：

```powershell
$Report = Get-Content 'data\local-integration.json' -Raw -Encoding UTF8 | ConvertFrom-Json
$Report.scope
$Report.task | ConvertTo-Json -Depth 10
$Report.agent_trace.selection | ConvertTo-Json -Depth 10
$Report.agent_trace.citation_ids
$Report.executor | ConvertTo-Json -Depth 10
$Report.unverified
$Report.agent_trace.trace | ConvertTo-Json -Depth 30
```

预期：

- 任务原文为“将仿真巡检通信保护切换为 SM4”，每次运行 task_id 会重新生成。
- 实际调用服务登记和认证策略工具，再查询 Ed25519 与 SM4-GCM；包括一次真实 MCP 续页请求。
- 未查询就提交、伪造引用提交被拒绝；随后合法选择通过。
- 最终目标为 `Ed25519 + SM4-GCM + lightweight`，附本任务返回的两个 citation ID。
- Guard 返回 `selected` 和 `requires_execution_verification=true`。
- 执行器结果与原始任务的 task_id 相同；`executor_simulation=succeeded` 只表示模拟外部条件下处理通过。

该脚本中的 Agent 是固定行为测试程序，不是外部 Pi/Aivo/真实模型；它不会根据你编辑的自由任务自动推理。若需要测试其他目标，应另建有明确断言的测试用例，不能把固定示例当作通用任务聊天入口。

生产入口 `python -m jetson.pi_runner` 依赖 Linux Unix socket 和运行配置，**不要在 Windows 直接启动它来代替上述联调**。本地自动脚本已测试原 PiRunner 任务类与真实子进程。

## 9. 自动回归与分层定位

完整运行：

```powershell
& $Python -X utf8 manage.py test 2>&1 | Tee-Object -FilePath (Join-Path $RunDir 'tests.log')
$TestExit = $LASTEXITCODE
"Test exit code: $TestExit"
if ($TestExit -ne 0) { throw '测试未全部通过，请查看 tests.log 中的 FAIL/ERROR。' }
```

当前源码预计发现 **228 项测试**（先前已执行 227 项完整回归，再单独通过新增 1 项部署测试）。以你本次输出的 `Ran ... tests`、`OK` 和退出码 0 为验收依据；不能仅凭用例总数判定通过。通常需要数分钟，耗时随机器而变。`FAILED`、`ERROR` 或非预期 skip 均需要检查原因。

需要分层排查时，在项目根目录分别运行：

```powershell
# SQLite、检索、分页、游标、客户端、MCP、预算、版本变化等：
& $Python -X utf8 -m unittest discover -s tests -p test_rag.py -v
# 融合 Guard、引用隔离、真实本地超时/断连/401、配置与镜像隔离：
& $Python -X utf8 -m unittest discover -s tests -p test_fusion.py -v
# PiRunner 任务及子进程行为：
& $Python -X utf8 -m unittest discover -s tests -p test_pi_runner.py -v
# 原 Guard/登记、执行器和切换逻辑：
& $Python -X utf8 -m unittest discover -s tests -p test_catalog.py -v
& $Python -X utf8 -m unittest discover -s tests -p test_executor.py -v
# 原业务镜像/清单和新增 RAG 部署约束，仅检查本地文件与逻辑：
& $Python -X utf8 -m unittest discover -s tests -p test_service_layout.py -v
& $Python -X utf8 -m unittest discover -s tests -p test_rag_deployment.py -v
```

完整回归还覆盖认证、密码 HTTP 服务、任务恢复、轮换、航线及其他业务功能。上述分组是定位入口，不应替代最终完整回归。部分测试故意注入错误并捕获它们；**测试最终标为 ok** 才表示异常处理符合预期。

## 10. 可选：本地验证发布包，不上传

这一步仅检查可交付性，不是现在要求你部署云端。既有发布目录可直接校验：

```powershell
& $Python -X utf8 -m tools.release --verify dist/rag-6cb2ead7f4be6dfb
& $Python -X utf8 -m tools.verify_release_server
if ($LASTEXITCODE -ne 0) { throw '发布包启动验证失败。' }
Get-Content 'data\release-validation.json' -Raw -Encoding UTF8
```

预期：文件校验通过；`packaged_server=passed`、`authenticated_query=passed`、`without_token=401`。探测脚本根据 `data/release-result.json` 定位发布目录，启动独立本地进程并在结束时关闭。

如果源码/数据库更新后需要重新制作包：

```powershell
& $Python -X utf8 manage.py doctor
if ($LASTEXITCODE -ne 0) { throw '先修复代码与数据库版本不一致。' }
& $Python -X utf8 manage.py release | Out-File -FilePath data/release-result.json -Encoding utf8
if ($LASTEXITCODE -ne 0) { throw '发布包生成失败。' }
& $Python -X utf8 -m tools.verify_release_server
```

该过程使用 SQLite backup API，新建带版本目录和校验清单；不会上传，也不会修改 Kubernetes。既有包继续保留。

## 11. 结束测试与保存结果

1. 在终端 A 按 `Ctrl+C` 停止手工 RAG 服务；自动 integration/发布包探测创建的进程会自行关闭。
2. 保留本次 `$RunDir` 中的检查、联调和测试日志。不要把 `data/local-test/service.token` 当作测试报告分享。
3. 当前终端不再测试时清除环境变量及内存里的请求头：

```powershell
Remove-Item Env:UAV_RAG_URL -ErrorAction SilentlyContinue
Remove-Item Env:UAV_RAG_TOKEN_FILE -ErrorAction SilentlyContinue
Remove-Variable Headers -ErrorAction SilentlyContinue
```

本地测试令牌文件可保留给下次本地复测。不要将它用于生产部署；后续云端会单独配置生产凭据。

## 常见问题

| 现象 | 检查与处理 |
|---|---|
| 找不到 `.venv312` 或依赖 | 确认项目路径；按第 1 步准备 Python 3.12，不要误用其他项目的环境 |
| OpenSSL FileNotFoundError | 检查 `UAV_TEST_OPENSSL_BINARY` 或 PATH，要求实际存在的 OpenSSL 3 |
| `No module named pip` 但 import 检查成功 | 测试无需 pip；继续运行。只有安装/升级依赖时才需要标准环境或安装工具 |
| FTS5 不可用 | 换用带 SQLite FTS5 的 Python；不能把跳过检索视为测试通过 |
| code/document/database version mismatch | 说明库与资料不匹配，保留旧库并按第 3 步重建，不能只改版本字段 |
| HTTP 401 | 两终端使用同一个测试令牌文件；服务端启动时已设置 TOKEN_FILE；不是模型 API key |
| 连接拒绝 | 终端 A 是否仍运行，端口是否一致；HTTP 客户端成功不代表 MCP 或 Agent 已通过 |
| 游标 400 | 保持原 query/candidates/top_k，使用本次返回的游标；版本变化后重新开始任务 |
| `budget exhausted` | 该任务 MCP 预算已用完，应报告证据不足；不要重启 MCP 来规避同任务限制 |
| HTTP 成功但 MCP 失败 | 检查终端 B 的环境变量、解释器路径、工具名和 JSON-RPC 参数 |
| `runner=failed` | 保留完整输出与报告；检查子进程导入、令牌路径、Guard 拒绝及超时，不把模型 stdout 当作成功回执 |
| WinError 5 临时目录拒绝访问 | 检查运行终端的账号、TEMP 目录权限和安全软件；先在普通本机 PowerShell 复现，不要直接全盘放宽权限 |
| 报告中 task_id 与示例不同 | 正常，每次自动联调生成新任务；应核对本次报告内任务和执行器 task_id 一致 |

## 本轮验收记录

完成后可把下面内容连同不含凭据的错误摘要发回来：

```text
Python / SQLite / OpenSSL 版本：
doctor：通过 / 失败
手工 HTTP 查询及分页：通过 / 失败
无令牌 401、错误游标 400：通过 / 失败
MCP 工具发现与调用：通过 / 失败
完整联调：runner=...，executor_simulation=...
完整回归：Ran ... tests；OK / FAILED；退出码 ...
发布包本地启动（可选）：通过 / 未运行 / 失败
日志目录：
失败的命令及错误摘要（不含凭据）：
```

以上完成后，结论应是“本地软件与替身链路验收通过”。真实模型、Linux 容器、集群、树莓派/Jetson 与云端网络留到你后续提供部署环境信息后逐层验证。
