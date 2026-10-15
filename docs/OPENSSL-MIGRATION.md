# GmSSL → OpenSSL 知识库迁移

## 检查结论与实施范围

2026-10-15 检查 `cloud-rag` 与相邻 `openssl`。两个目录未发现额外 AGENTS.md；沿用会话提供的项目约束。项目未声明 pip/Node/CMake 密码库依赖，README 指定 Python 3.11+、标准库、SQLite FTS5，Docker 使用 Python 3.12-slim。

原 GmSSL 的所有活跃使用是：`index.py` 的源码识别及读取、`catalog.py` 的证据路径、CLI 默认路径、demo、发布包、Docker/systemd 和测试。`server.py` 的令牌比较使用 Python `hmac.compare_digest`；引用、游标和下载完整性使用 `hashlib.sha256`。没有导入 gmssl、加载 GmSSL 动态库或调用其加解密命令，没有业务字段密文/签名/私钥存储，也没有数据库 TLS 连接。SQLite 表为 metadata、algorithm_descriptions、chunks 和 FTS5 辅助表。

因此这次替换的是数据库的**知识来源及算法文档**，不重构无人机密码服务、不改变算法套件、令牌格式或摘要格式。没有应删除的 GmSSL 运行依赖；保留 `legacy_catalog.py` 和历史库，专用于旧库只读兼容。旧报告、原 GmSSL 源码及原发布包作为历史材料保留。

## 提供的 OpenSSL 内容

- 版本：`VERSION.dat` 为 4.2.0-dev，SHLIB_VERSION=4；Git HEAD 为 `ed57129cd43b77e077c68fd5f7ea951976766f38`，检查时工作树干净。
- 目录：`crypto/` 为算法核心，`providers/` 为 provider 实现和注册，`include/openssl/` 为公开接口，`ssl/` 为 TLS 等协议，`apps/` 为 CLI，`doc/man3` 和 `doc/man7` 为接口/算法说明，`test/` 为上游测试。并列第三方子模块不代表内建 provider。
- 本地没有生成的配置、顶层 Makefile 或编译库。PATH 的 `C:\msys64\mingw64\bin\openssl.exe` 为 3.3.0；Python 报告 OpenSSL 1.1.1u，均不属于提供源码。
- 当前 PATH 未发现 Perl、nmake、CMake；OpenSSL 不使用 CMake 主构建。Windows 官方方式为 Perl Configure + MSVC/nmake（汇编通常还需 NASM）；Unix 为 Perl Configure + make + make test，目标/选项必须与目标系统相符。
- 本次无需构建 libcrypto：RAG 直接索引提供源码及 POD 文档，不执行密码运算。**没有运行此 OpenSSL 的编译、上游算法测试或 runtime list/fetch 测试**；运行时启用能力明确记为 unverified。
- 以后若确需密码运算集成，按 `openssl/INSTALL.md`、`NOTES-WINDOWS.md`、`NOTES-UNIX.md` 在隔离构建目录编译；先确定生产可用版本和目标架构，不能将此开发分支当作稳定发布二进制。

在取得这份源码构建出的可执行文件后，用其**绝对路径**执行以下命令并保存版本、配置和完整输出，再确认运行时能力；不要用 PATH 中另一版本代替：

```text
<built-openssl> version -a
<built-openssl> list -providers -verbose
<built-openssl> list -disabled
<built-openssl> list -cipher-algorithms -provider default
<built-openssl> list -digest-algorithms -provider default
<built-openssl> list -mac-algorithms -provider default
<built-openssl> list -kdf-algorithms -provider default
<built-openssl> list -signature-algorithms -provider default
<built-openssl> list -key-exchange-algorithms -provider default
<built-openssl> list -kem-algorithms -provider default
```

需要审计 legacy 时单独同时指定 `-provider default -provider legacy`，不默认在生产服务启用弱算法。源码注册表扫描不能替代这些实测。

## 代码、数据库和 API 变化

- `catalog.py` 采用 EVP 的 OpenSSL 文档及 provider 实现作为八项服务的证据。RSA-PSS/Ed25519 有真实源码，证据状态由原来的 insufficient/not_found 变为 available；这仅表示资料存在，不表示服务已部署。
- 服务 ID、SM2/RSA/Ed25519/SM4-GCM/AES-256-GCM 别名与 binding 不变。历史 `gmssl-*` ID 保留，不做破坏性重命名。
- `index.py` 新建 **schema 3**：原 chunks/search/metadata/algorithm_descriptions 表结构保留；新增 service_catalog（持久化证据映射）、knowledge_documents（独立 Markdown 文档）及 algorithm_inventory（default/legacy 静态注册列表）。源码 `.pod` 和 `.h.in` 等也入库。
- 默认新库为 `data/openssl.sqlite3`。构建使用临时库完成事务，再用不覆盖已存在目标的方式发布；若目标已存在，明确拒绝。每次更新使用新文件名，不能静默覆盖真实数据库。
- `KnowledgeBase` 支持新 OpenSSL schema 3 和旧 GmSSL schema 2 只读查询。旧库使用冻结的旧证据路径，防止 OpenSSL 映射配到 GmSSL 片段；其他 schema/corpus 组合拒绝。
- 原 enrich、分页 query、MCP 与 Guard 接口保持。游标已绑定完整 corpus 元数据，迁移后旧游标必须失效并从第一页重新查询。
- 新增认证保护下的 `GET /v1/documents`、`GET /v1/documents/openssl-algorithms` 和本地 `document` 命令。正文从数据库读取，验证内容哈希；与源码引用分开存储。哈希检查是完整性检查，不是防止有数据库写权限者重算所有哈希的真实性保证。
- 文档覆盖范围及未验证能力详见 `OPENSSL-ALGORITHMS.md`。源码注册项的每种模式和别名可通过 SQLite 的 algorithm_inventory 查阅；`runtime_enabled` 不伪装成已启用。
- 发布包改为 `uav-rag-openssl-release.zip`，包含新库、说明、源码语料及 OpenSSL 许可证。`sources/openssl` 是**可复现知识索引的语料快照**，不保证是可编译的完整 OpenSSL 源码发行包。

## 数据兼容与真实环境切换

原 `data/gmssl.sqlite3` 保留，不重写其内容。SQLite 文本、UTF-8 描述、JSON 数据及 SHA-256 片段引用格式保持。源码变更后 revision、文档数量和片段 ID 合理变化；旧引用不能假装指向新源码，需保留对应旧库用于审计。

本项目不具备历史业务密文/签名/私钥，因此下列事项**不适用且未测试**：GmSSL→OpenSSL 解密、反向解密、错误加密密钥、篡改密文、历史签名互验、私钥口令格式互换。没有“同名算法已兼容”的结论。已在范围内测试的是错误访问令牌、下载篡改、异常输入、文档篡改、旧库读取和游标隔离。

如后续在其他真实密码服务发现数据，则必须另做隔离兼容测试：固定算法、密钥位数、工作模式、填充、IV/nonce 长度与布局、AAD、标签长度、字节编码；明确 PEM/DER/PKCS#8/SPKI/raw key，以及 SM2 ID、DER 签名、C1C3C2/C1C2C3；RSA-PSS 的摘要/MGF1/盐长。建立固定向量，双方生成/验证，测试错密钥、错标签、截断、非法编码和空输入，再设计带版本号的记录格式及双读过渡。未通过前不允许改算法、批量重加密或覆盖原数据。SM9/ZUC/XMSS/SM4-FF1 等不能视为这个 OpenSSL 内建能力的等价替换。

本地准备（在 cloud-rag 目录）：

```powershell
python -X utf8 -m uav_rag index --source ..\openssl --db data\openssl.sqlite3
python -X utf8 -m uav_rag document --db data\openssl.sqlite3
python -X utf8 -m unittest discover -s tests -v
python -X utf8 demo.py
python -X utf8 package_release.py
python -X utf8 verify_release.py
```

若目标文件已经存在，改用 `data/openssl-<revision>.sqlite3` 等新路径；不要删除在用数据库来绕过保护。demo 和打包脚本使用固定默认库，新修订发布应在独立发布目录将已验证的新库作为该目录的 `data/openssl.sqlite3`。

发布包解压后若要重建索引，使用 `--source sources/openssl --db data/openssl-rebuilt.sqlite3`；包内语料与开发目录的相邻 `../openssl` 路径不同。独立文档和来源文件也必须保留在包内对应目录。

真实环境迁移是**独立运维操作，本次没有执行**：

1. 记录现有应用版本、进程启动参数、只读数据库哈希、令牌路径及服务配置；对仍在写入的 SQLite 用 backup API 创建一致快照（本服务数据库通常只读）。备份不应覆盖任何原文件。
2. 将新应用与新库放在独立发布目录，校验发布哈希；不得解压覆盖当前运行目录。保留原库、原应用及配置，权限与令牌策略不变。
3. 在独立回环端口启动候选版本，挂载/指定新库；核对 `/health`、八项服务、分页查询和独立文档哈希；确认原 Guard、模型与目标网络验收。
4. 验收后修改服务的 `--db` 或容器挂载到新库，并重启相应 RAG 服务。不得在运行中替换同一数据库路径；新旧元数据和连接混用会破坏引用契约。客户端重启任务/清除旧游标。
5. 回滚时恢复原应用发布目录、原 `--db .../gmssl.sqlite3` 和原配置并重启；新版应用也可读取旧 schema 2 库，但旧版应用不能读 schema 3。重新从第一页查询，恢复旧引用版本；无需数据反向转换或重加密。

Docker/systemd 模板已切换新默认路径，但没有执行 Docker 构建、远程部署、k8s 调整或数据库上传。旧验收文档是历史记录，迁移以本说明与此次测试结果为准。
