# Jetson 调用云端 RAG：首次部署、非首次测试与结果判读

更新日期：2026-10-13。

本文覆盖首次部署与之后的重复联调。用户反馈本轮测试可能已成功，但尚未提供实际接口响应供复核；本文中的返回值是依据当前代码和发布库给出的预期，不是本次远端运行记录。

阅读路线：

- **首次调用**：第 1 节确认路径 → 第 1A 节准备程序和凭证 → 第 3～8 节启动并调用 → 第 9～10 节判断结果。
- **非首次调用**：跳过第 1A 节，从第 2 节开始；不要重复解压或生成凭证。
- **测试结束与排错**：第 11～12 节。

文件名继续沿用 `RETEST-JETSON-CLOUD.md`，内容已包含首次指南。本文文件保存在本地 Windows 项目中，不会自动同步到云端或 Jetson。

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

本文假定 Jetson 用户是 `jetson`、目录与上表一致；如实际不同，请修改对应路径。首次部署按第 1A 节创建 `app`；若以前已部署但找不到此目录，先查找实际解压目录，不要盲目重复上传和解压。

测试链路：

```text
Jetson 查询客户端 / curl
    → Jetson 127.0.0.1:18787
    → SSH 加密隧道
    → 云端 127.0.0.1:8787
    → 凭证检查 → SQLite 检索 → 返回引用资料
```

这证明宿主机上的接口调用，不证明 k8s Pod 联通、真实 Agent 两次决策、服务下载、镜像导入、服务启动或无人机任务执行。普通 Pod 的 localhost 不等于宿主机 localhost。

## 1A. 首次调用测试指南：一次性准备

本节说明首次上传、解压、环境检查和文件传输。准备结束后继续第 3～10 节的共用启动和测试流程。已经完成的步骤可跳过。

### 1A.1 首次需要哪些文件？

| 机器 | 文件 | 用途 |
|---|---|---|
| 云端 | `uav-rag-local-release.zip` | 包含程序、预构建数据库和验收材料 |
| 云端 | `release-sha256.txt` | 检查发布包传输完整性 |
| 云端 | `uav-rag-test.token` | RAG 访问凭证，已有时必须复用 |
| Jetson | `uav_rag/` 整个目录 | 使用项目 Python 查询入口所需代码 |
| Jetson | `service.token` | 从云端复制的同一份凭证 |

仅用 curl 验证接口时，Jetson 只需要凭证，不需要 Python 客户端。Jetson 不需要复制知识库、GmSSL 源码、完整发布包或服务镜像。云端和 Jetson 的凭证文件名可以不同，但内容必须一致。

### 1A.2 登录云端并准备目录

从电脑或 Jetson 执行：

```bash
ssh root@47.109.49.98
```

目的：进入云端终端。主机指纹应核对，不能忽略指纹变化警告。

登录后在云端执行：

```bash
mkdir -p /root/cloud-rag
ls -la /root/cloud-rag
```

- `mkdir -p`：目录不存在时创建，已存在时保留内容。
- `ls -la`：查看已有文件，防止重复上传覆盖。

你当前已有发布包、校验文件和凭证，可跳过下一小节上传。

### 1A.3 发布包尚未上传时：在 Windows 上传

以下两条必须在本地 **Windows PowerShell** 执行，不在云端或 Jetson 执行：

```powershell
cd E:\claudeCode\project\Intra-group-project\cloud-rag
scp .\data\uav-rag-local-release.zip .\data\release-sha256.txt root@47.109.49.98:/root/cloud-rag/
```

- `cd`：进入本地项目目录。
- `scp`：通过 SSH 把两个文件传到云端。
- 这不会自动解压或启动服务；目标同名文件会被覆盖，已上传完成时不要重复执行。

### 1A.4 云端验证 Python 与 FTS5

回到云端 SSH 终端执行：

```bash
/root/.local/bin/python3.11 --version
/root/.local/bin/python3.11 -c "import sqlite3; c=sqlite3.connect(':memory:'); c.execute('CREATE VIRTUAL TABLE test USING fts5(text)'); print('Python 3.11: FTS5 OK')"
```

第一条验证解释器版本。第二条创建内存数据库并尝试创建 FTS5 全文检索表，不修改已有知识库。

预期第一条显示 Python 3.11 的具体版本，第二条显示：

```text
Python 3.11: FTS5 OK
```

当前云端已确认此解释器可用。换机器后若路径不存在或 FTS5 检查失败，先处理环境，不要继续启动，也不要替换系统默认 Python 3.8。

### 1A.5 校验发布包完整性

在云端执行：

```bash
cd /root/cloud-rag
tr -d '\r' < release-sha256.txt | sha256sum -c -
```

- `tr -d '\r'`：读取时删除 Windows 回车符，避免校验程序把回车误当成文件名的一部分；不修改原文件。
- `|`：将处理后的清单传给下一条命令。
- `sha256sum -c -`：从标准输入读取清单，核对 ZIP 哈希。

成功输出：

```text
uav-rag-local-release.zip: OK
```

这说明压缩包与校验清单一致，不是发布者身份的数字签名验证。出现 FAILED 或文件不存在时停止，检查路径和上传文件。

### 1A.6 首次解压程序

在云端执行：

```bash
mkdir /root/cloud-rag/app
```

故意不使用 `-p`：如果目录已存在会提示错误，此时停止本步骤并检查目录，不能继续覆盖解压，也不要删除已有文件。已解压并测试通过时直接复用。

仅当目录刚刚创建成功时，执行：

```bash
/root/.local/bin/python3.11 -m zipfile -e \
  /root/cloud-rag/uav-rag-local-release.zip \
  /root/cloud-rag/app
cd /root/cloud-rag/app
ls -ld uav_rag
ls -lh data/gmssl.sqlite3
```

- `-m zipfile`：使用 Python 自带 ZIP 工具。
- `-e`：解压，后面依次为发布包与目标目录。
- `cd`：切换到解压后的程序目录。
- `ls -ld`：检查程序包目录。
- `ls -lh`：检查数据库存在及文件大小。

预期目录：

```text
/root/cloud-rag/
├── release-sha256.txt
├── uav-rag-local-release.zip
├── uav-rag-test.token
└── app/
    ├── uav_rag/
    ├── data/gmssl.sqlite3
    ├── tests/
    └── 其他验收和部署资料
```

不需要重新构建数据库。解压本身不会启动 RAG 或无人机业务服务。

### 1A.7 首次运行发布包验证与测试

在云端程序目录执行：

```bash
cd /root/cloud-rag/app
/root/.local/bin/python3.11 verify_release.py /root/cloud-rag/uav-rag-local-release.zip
/root/.local/bin/python3.11 -m unittest discover -s tests -v
```

- `verify_release.py`：验证 ZIP 中的文件列表、路径和内容哈希，检查的是 ZIP 本身，不是实时网络服务。
- `unittest discover`：发现并运行测试。
- `-s tests`：指定测试目录。
- `-v`：显示各项测试结果。

云端没有配置原始无人机项目时，预期结尾：

```text
Ran 21 tests ...
OK (skipped=1)
```

表示 20 项通过、1 项跳过。跳过项应是依赖原始无人机项目的集成测试；若有其他跳过原因或失败，应查看具体测试名。此结果证明这套 RAG 测试通过，不代表 Jetson 网络、真实模型或无人机完整业务测试通过。

### 1A.8 云端准备凭证：不要替换已有凭证

先查看文件是否存在：

```bash
ls -l /root/cloud-rag/uav-rag-test.token
```

已有非空文件时直接复用。你当前已拥有此文件，正常情况下不需要执行生成命令。

仅在首次部署且凭证确实不存在时执行：

```bash
if [ ! -e /root/cloud-rag/uav-rag-test.token ]; then
  (umask 077; set -C; /root/.local/bin/python3.11 -c "import secrets; print(secrets.token_hex(32))" > /root/cloud-rag/uav-rag-test.token)
fi
```

- 条件判断保证已有路径不进入生成分支。
- `umask 077` 限制新建凭证文件权限。
- `set -C` 防止输出重定向覆盖已有普通文件。
- `secrets.token_hex(32)` 生成 32 随机字节并编码成 64 个十六进制字符。
- `>` 将内容写入文件，不显示到终端；括号将 shell 设置限制在子进程内。

随后检查：

```bash
test -s /root/cloud-rag/uav-rag-test.token && echo "Token file OK"
chmod 600 /root/cloud-rag/uav-rag-test.token
```

`test -s` 检查存在且非空；无成功提示时停止排查。`chmod 600` 仅允许所有者读写。不要打印、分享凭证，不要在 Jetson 已复制后重新生成，否则两端将不匹配。

### 1A.9 Jetson 首次接收客户端文件

以下在 **Jetson 终端**执行：

```bash
mkdir -p /home/jetson/rag-link-test
cd /home/jetson/rag-link-test
ls -la
```

目的：建立独立测试目录并检查已有内容，不改动 `uav01` 或主目录原有的 `client.py`。

若文件尚未传入，执行：

```bash
scp -r root@47.109.49.98:/root/cloud-rag/app/uav_rag /home/jetson/rag-link-test/
scp root@47.109.49.98:/root/cloud-rag/uav-rag-test.token /home/jetson/rag-link-test/service.token
chmod 600 /home/jetson/rag-link-test/service.token
```

- 第一条递归复制完整客户端包，现有命令入口会导入其他模块，因此不要只复制 `client.py`。
- 第二条复制同一份云端凭证，并在 Jetson 命名为 `service.token`。
- 第三条限制凭证权限。
- `scp` 可能覆盖同名文件，已完成传输时跳过，不要重复覆盖。

检查：

```bash
ls -ld /home/jetson/rag-link-test/uav_rag
test -s /home/jetson/rag-link-test/service.token && echo "Jetson token file OK"
python3 --version
```

Python 3.11+ 可使用项目客户端；版本较低时先采用第 8.1 节 curl 测试，不用替换系统 Python。`(base)` 可能表示正在使用 Conda 环境，版本以命令实际输出为准。

### 1A.10 首次启动到验收的完整顺序

一次性准备结束后，按后文共用章节执行：

| 顺序 | 章节 | 操作位置与动作 | 预期证据 |
|---|---|---|---|
| 1 | 第 3～4 节 | 云端检查端口、设置凭证路径并启动 RAG | 显示监听 `127.0.0.1:8787` |
| 2 | 第 5 节 | Jetson 建立 SSH 隧道 | 本地 `18787` 监听；还不能据此判定接口成功 |
| 3 | 第 6 节 | Jetson 请求健康接口 | `status: ok` 和索引版本 |
| 4 | 第 7 节 | 不带凭证访问目录接口 | HTTP 401，说明拒绝未授权请求 |
| 5 | 第 8 节 | 带凭证提交检索并保存 JSON | HTTP 200 |
| 6 | 第 9～10 节 | 核对结果内容并完成清单 | 任务号匹配、服务 ID 正确、相关引用非空 |
| 7 | 第 11 节 | 保存证据，按需停止本轮进程 | 文件保留，下次无需重新部署 |

首次与非首次使用相同的健康、鉴权、检索和结果判读方法，因此后文命令只维护一套。首次成功后，下次跳过本节所有一次性准备。

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

除第 1A.3 节明确标注的 Windows PowerShell 命令外，其余代码块为 Linux shell 命令。多行末尾的 `\` 表示命令还没结束，反斜杠后不要添加空格。不要复制终端提示符。

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

目前问题：
目前的rag补充是：SM2 检索到的是签名、验签函数声明，SM4-GCM 检索到的是参数校验测试代码，而不是算法介绍。
边界问题：候选算法较多、任务文本较长时，增强后的提示词可能超过长度限制。