# OpenSSL 知识库迁移验证记录

日期：2026-10-15，Windows 本地环境。结论：迁移相关应用测试 **37/37 通过，0 跳过**。提供的 OpenSSL 是源码资料，不是本次密码运算的运行库。

## 执行与结果

在 `cloud-rag` 执行：

```powershell
$env:UAV_PROJECT_ROOT='C:\Users\86187\Desktop\pi_uav_k3-codex-service-layout'
python -X utf8 -m unittest discover -s tests -v
python -X utf8 -m compileall -q uav_rag tests demo.py package_release.py verify_release.py
python -X utf8 demo.py
git diff --check
```

- unittest 最终输出：`Ran 37 tests in 22.861s`，`OK`。构建测试库使用临时目录，篡改测试只修改临时副本。
- compileall：退出码 0。diff 检查：退出码 0；Git 提示工作区换行将被转换为 CRLF，不是内容错误。
- demo：真实 SQLite + 本地 HTTP，2 页查询，选择 auth-sm2 / sm4-gcm-v1，状态 planned，保留 requires_execution_verification。Agent 为确定性替身，没有实际运行密码服务、模型或飞控。输出 `data/openssl-demo-result.json`。
- 新库 `PRAGMA integrity_check`：`ok`；独立文档与 Markdown 字节内容哈希相等。
- 93 个本地 POD 引用路径均存在。未使用其他版本的网页文档替代本地来源。
- 发布包生成成功，`verify_release.py` 校验 3415 个文件全部通过。将包解压至独立临时目录，仅使用包内 `sources/openssl` 语料再执行同一测试命令：`Ran 37 tests in 20.574s`，`OK`，0 跳过；该目录没有真实旧库，历史兼容使用 schema 2 隔离夹具。原项目目录测试另已覆盖真实旧库。
- 发布包路径为 `data/uav-rag-openssl-release.zip`，准确包哈希见同目录 `openssl-release-sha256.txt`（包内包含本报告，避免在报告中循环嵌入包哈希）。

## 覆盖

| 范围 | 已验证内容 |
|---|---|
| 原业务接口 | enrich、中文检索、八项服务、来源行号与 SHA-256、未知服务、全部候选分页还原、页大小和工具总预算 |
| 原执行链契约 | stdio MCP、原 Guard 保留派发与查询约束、原 PiRunner overlay、长任务、任务幂等性；仅契约/替身执行 |
| 新独立文档 | 单独 SQLite 记录、HTTP 列表及正文、与本地文件一致、版本/哈希、无权限/错误令牌拒绝、未知 ID、非法 ID |
| 历史兼容 | schema 2 隔离夹具及真实 data/gmssl.sqlite3 均可只读查询；旧映射及 Ed25519 not_found 保留；描述来自旧库；迁移前游标不能用于新库 |
| 数据保护 | 已存在输出拒绝覆盖；无效来源不生成数据库；SQLite 只读连接拒绝写入；原库哈希前后不变 |
| 完整性和异常 | 临时文档篡改检测、下载归档篡改/校验失败、目录逃逸、非法请求、错误令牌、伪造引用、断网及版本变化 |
| 来源边界 | OpenSSL 版本、378 条 default/legacy 静态注册、SM4-GCM/ASCON 存在、来源文件哈希；所有 runtime_enabled 为 unverified |

历史密文解密、错误加密密钥、篡改密文、签名互验：**不适用，未运行**。这个数据库没有相应业务数据或运算路径；错误访问令牌测试不能替代密码互操作测试。

## 数据库身份

| 项目 | 值 |
|---|---|
| 新数据库 | `data/openssl.sqlite3` |
| schema / corpus | 3 / OpenSSL |
| 源码版本 | 4.2.0-dev |
| 来源文件 / 片段数 | 3384 / 25171 |
| 服务描述 / 独立文档 / 静态注册数 | 8 / 1 / 378 |
| 源码 revision | `b4c1a8a4c2bbc4f2649116b2a41d8fef55daffc810158b3685339b14de2e5e92` |
| 服务描述 revision | `e72754cdb1d4a5fdb402bc5cfa70b3919716723e1cf54447feee3ba386d76922` |
| 独立文档 SHA-256 | `2c36ed165ea0850ffafd5a850fc4504f1b2380b2aa16dd5cb6563ec3f02c031e` |
| 原 gmssl.sqlite3 SHA-256（前后一致） | `4a1c0c501694a19b3d7238b0de4f29dd3a5ce6240a97339328956d5efc52c7b7` |

## 修改文件

- `uav_rag/catalog.py`、新增 `legacy_catalog.py`：新旧证据映射及稳定 ID。
- 新增 `uav_rag/openssl_source.py`：版本核对、静态注册清单及 OpenSSL 本地来源描述。
- `uav_rag/index.py`：schema 3 建库、独立文档、目录持久化、旧库只读兼容、禁止覆盖目标。
- `uav_rag/server.py`、`__main__.py`：文档读取接口/命令、新默认路径。
- `Dockerfile`、`deploy/uav-rag.service`：新数据库默认配置；无新增密码库运行依赖。
- `demo.py`、`package_release.py`、`verify_release.py`：新语料、独立发布包及许可证。
- `tests/test_rag.py`：迁移及原有功能回归。
- `README.md`、`ON-DEMAND-RAG.md`、`docs/OPENSSL-MIGRATION.md`、`docs/OPENSSL-ALGORITHMS.md`、本记录：使用、算法、切换及回滚说明。
- `data/openssl.sqlite3`、`data/openssl-demo-result.json` 等为生成产物；`data/` 由现有 .gitignore 排除，发布时由打包脚本显式携带。

## 未执行及限制

- 未编译提供的 OpenSSL 4.2.0-dev，也未执行其上游算法测试、provider runtime list/fetch；缺少该目录编译产物，当前 PATH 未提供 Perl/nmake，且本次实际应用无须链接 libcrypto。
- 系统 OpenSSL 3.3.0 与 Python OpenSSL 1.1.1u 不是提供源码的验证对象。
- 未构建/运行 Docker 镜像；未进行云端/Jetson/k8s 部署、真实模型或飞控验收。旧部署保持不变，真实切换和回滚步骤见迁移说明。
- 新版可读旧 schema 2；旧程序不可读新 schema 3。源码替换使引用 ID 和游标更新，不能复用旧游标或将旧引用改指向新源码。
