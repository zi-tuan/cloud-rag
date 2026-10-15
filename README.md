# UAV 云边业务与按需 RAG 融合项目

所有运行、构建和测试从本目录开始，不依赖两个原项目目录。原项目保留未改动。这里保留原业务 Python 包及协议，将 `uav_rag` 接入 PiRunner 的 MCP Guard，并统一测试与清单渲染；不走批量拼接任务的旧流程。

| 模块 | 位置 | 目标部署位置 |
|---|---|---|
| RAG HTTP、SQLite FTS5/BM25 | `uav_rag`、`cloud/rag`、`data/openssl.sqlite3` | 阿里云，默认目标 Linux AMD64，实际架构需确认 |
| 云业务 API | `cloud/cloud_api` | 原云端 API 位置 |
| Edge Agent、执行器、SwitchCoordinator | `jetson/edge_agent` | 原 Jetson 边端 |
| PiRunner、Pi Agent MCP 接入 | `jetson/pi_runner` | 原 Jetson 的独立 PiRunner 容器 |
| 协议、客户端、飞控适配 | `shared` | 各进程按依赖携带 |
| 密码与认证服务 | `services` | 云端 AMD64、边端 ARM64，分别构建 |
| 可重建知识资料 | `sources/openssl`、`docs/OPENSSL-ALGORITHMS.md` | 开发/知识构建机；不进入边端镜像 |

Pi 是外部 Agent 运行时，不是 Raspberry Pi 的简称。原清单把 PiRunner 放在 Jetson。树莓派当前承担哪些进程需要现场确认，不能由名称推断。外部 Pi、Aivo、Kubernetes MCP、模型凭据、设备与集群不在本项目中伪造。外部版本和路径约定参见 `docs/business-original.md`；其现场地址及历史成功记录不代表本次验证。

## 本地复现

首次手工测试请按 [完整本地测试操作手册](docs/LOCAL-TEST-GUIDE.md) 执行，包含双终端启动、鉴权、分页、MCP、完整链路、回归命令及故障排查。

需要 Python 3.12、SQLite FTS5 和 PATH 中的 OpenSSL 3；OpenSSL 可用 `UAV_TEST_OPENSSL_BINARY` 指定。RAG 本身仅用标准库。已安装环境为 `.venv312`，原锁定依赖在 `requirements-dev.txt`。

```powershell
cd E:\claudeCode\project\Intra-group-project\uav-cloud
# 新机器首次准备：
py -3.12 -m venv .venv312
.venv312\Scripts\python -m pip install -r requirements-dev.txt
# 已交付数据库时直接检查；重建使用新文件名，禁止覆盖正在使用的数据库：
.venv312\Scripts\python manage.py doctor
.venv312\Scripts\python manage.py index --db data/openssl-new.sqlite3
.venv312\Scripts\python manage.py serve
```

`serve` 默认加载 `data/openssl.sqlite3` 并监听 `127.0.0.1:8787`。另一终端查询：

```powershell
$env:UAV_RAG_URL='http://127.0.0.1:8787'
.venv312\Scripts\python -m tools.probe_rag
.venv312\Scripts\python -m uav_rag lookup 'SM4-GCM 使用限制' --services sm4-gcm-v1
```

完整自动联调会自行启动随机回环端口 HTTP，生成临时令牌，退出时关闭进程；不需要先手工启动服务：

```powershell
.venv312\Scripts\python manage.py integration
.venv312\Scripts\python manage.py test
```

产物为 `data/local-integration.json`（完整任务、MCP 请求/响应、引用、选择及执行器结果）。Agent 是明确标注的确定性替身；Kubernetes、就绪条件和云业务传输使用测试夹具，不能作为真实部署验收。生产 PiRunner 的 Unix socket 入口必须在 Linux 验证，Windows 仅测试其任务类和真实子进程。

## 业务链路与约束

原始 scenario 完整交给 Agent，保持原 16,000 字符上限。Agent 先读业务登记，再查询 `uav_knowledge_query`，按需以 `next_cursor` 翻页。每任务独立 Agent/MCP 进程，最多 8 次请求、24,000 UTF-8 字节；失败请求也消耗次数。单页最多 6,000 字节。

组合/计划工具新增 `citation_ids` 字段：覆盖所有所选认证/加密算法，引用 ID 必须来自本任务实际返回的 citation 项。覆盖层验证后剥离此字段交给原 Guard，保留原任务回执结构，并另存 `selection.knowledge.json`。原 Guard 仍校验目标及登记；原执行器处理部署、认证、切换和回执。激活工具也必须先取得对应源码证据。

知识库包含 8 个描述条目。可执行业务契约仍是 3 个认证策略（Ed25519/RSA/SM2）和 2 个套件（AES-256-GCM/SM4-GCM）；SM3/HKDF/X509 是知识能力，不因入库而成为可部署服务。算法说明、源码证据、镜像验证、运行 Ready 各自独立，不能相互代替。知识中的指令不能扩展工具权限。

超时/断网可在剩余预算内重试；鉴权失败须修复凭据，不降级鉴权。失效游标重新开始同版本查询；任务中索引版本变化须终止并新建任务。未知服务、空证据可收窄候选；仍缺证据或预算耗尽则停止选择，保留当前运行服务。原业务认为服务存在不代表本次知识足以提交。

## 构建与部署入口

所有 Dockerfile 的上下文都是项目根目录。PiRunner Dockerfile 只复制 RAG 客户端、工具、目录和覆盖层；不复制数据库、语料或服务端模块。原业务镜像构建方式不变，`deploy.scripts.render_layout --rag-url ...` 将 RAG 配置和 Secret 挂载写入原 Jetson 清单。

```powershell
python manage.py build --module rag --architecture amd64 --tag <REGISTRY>/uav-rag:<RELEASE> --push
python -m deploy.scripts.render_layout --rag-url https://<RAG_HOST> --lock-dir <NEW_IMAGE_LOCKS> --output <REVIEW_DIRECTORY>
```

`<...>` 是必须替换的占位符。不能直接应用原基础清单或沿用旧 PiRunner 镜像锁冒充新版本。RAG 完整数据库和服务代码按 `manage.py release` 打包；SQLite 使用 backup API 一致性快照。

详见 [本地联调报告](docs/LOCAL-INTEGRATION.md) 和 [云端上传、验证、更新及回滚](docs/CLOUD-DEPLOYMENT.md)。文档中的生产操作尚未执行。
