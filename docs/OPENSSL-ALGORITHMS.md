# OpenSSL 密码算法说明（独立知识文档）

核对日期：2026-10-15。对象：本工作区提供的 OpenSSL **4.2.0-dev**，源码提交 `ed57129cd43b77e077c68fd5f7ea951976766f38`。这是开发版源码，不是已验证的生产二进制。

## 依据、可用性与覆盖范围

依据本地 `VERSION.dat`、`Configure`、`providers/defltprov.c`、`providers/legacyprov.c`、`providers/implementations/include/prov/names.h` 及 `doc/man7/*.pod`。下文“来源”列的文件名均相对于 **OpenSSL 的 `doc/man7/`**；主清单来源为 `OSSL_PROVIDER-default.pod` 和 `OSSL_PROVIDER-legacy.pod`。实际注册源码优先于总览页：例如 ASCON-AEAD128 已在 `defltprov.c` 注册，但总览页尚未列出。

本次未发现提供目录中的 `configdata.pm`、生成的顶层 Makefile、libcrypto/libssl 或 openssl 可执行文件。因此**实际编译启用、provider 加载及运行时 fetch 能力均未验证**。系统 PATH 的 OpenSSL 3.3.0 和当前 Python 的 OpenSSL 1.1.1u 都不是这份源码的编译结果，不能用来证明 4.2.0-dev 的能力。

可用性标记含义：

- **D**：default provider 注册，通常默认构建包含；仍受 `no-*`、平台、CPU、配置和 property query 限制，不等于本机已启用。
- **L**：legacy provider，需要显式加载；加载 legacy 后若仍需 default，应同时显式加载 default。
- **E**：另需构建选项，例如 `enable-lms`、`enable-md2`、`enable-rc5`、`enable-jitter`。加载 provider 不会恢复编译时排除的算法。
- “不推荐”是使用建议，不等于 API 已删除；default 也包含 MD5、SHA-1、3DES 等兼容能力，不能把 D 当作安全推荐。
- FIPS provider 需要单独构建、安装和加载。存在源码或 `fips=yes` 属性不证明某部署取得认证，须核实具体验证版本、操作参数和 indicator。本文不声称这个开发版通过 FIPS 认证。

覆盖 default/legacy provider 的摘要、对称密码、MAC、KDF、密钥交换、签名、非对称加密、KEM 和随机数家族；合并同族长度、模式及别名。索引时另将两个 provider 注册表中这些操作的 **378 条静态注册**存入 `algorithm_inventory` 表，保留名称、别名、操作和来源；条件分支也列入，`runtime_enabled` 一律为 `unverified`。这不是执行 `openssl list` 得到的启用清单。

不逐个展开 OID、EC 全部曲线、内部汇编实现、编码器/解码器、TLS 套件、第三方 provider、ENGINE 或 submodule。`oqs-provider`、`gost-engine` 等同级目录不作为内建支持证据；FIPS 与 base 的重复实现不另算算法。`NULL`、内部摘要和兼容适配器明确标出。若换源码版本，应重新核对说明与注册清单，不能直接复用结论。

## 本项目使用位置

`cloud-rag` 是 Python 标准库 + SQLite FTS5 的检索服务。它**没有** GmSSL/OpenSSL 的字段加解密、签名、密钥管理或数据库 TLS 调用。数据库保存源码片段和说明文字；迁移的是知识资料。

表中项目标记：**K**＝知识映射，实际位置为 `uav_rag/catalog.py` 的指定服务 ID，以及入库后的 `algorithm_descriptions`；**R**＝Python 标准库实际使用；**—**＝仅作为文档/语料收录，未执行密码运算。K 不证明无人机服务已实现或可部署。

`gmssl-sm3`、`gmssl-hkdf`、`gmssl-x509` 是保留兼容的公开 ID，所指语料已是 OpenSSL。不能根据 ID 前缀推断运行库。

## 对称加密与认证加密

| 算法/类别与可用性 | 用途和优点 | 缺点、限制、安全注意 | 项目使用 | 本地来源 |
|---|---|---|---|---|
| AES-128/192/256，分组密码，D | 通信和存储；生态广，常见处理器有加速；可选 GCM/CCM/OCB 等认证模式 | 128/192/256 指密钥位数，块均为 128 位；模式决定安全属性。256 位并不消除 nonce 重用或侧信道问题。速度应在目标机实测 | K：`aes-gcm-v1`（AES-256-GCM） | `EVP_CIPHER-AES.pod` |
| SM4，128 位分组及密钥，D | 国密协议的数据保密，GCM/CCM 可同时认证；接口与 EVP 一致 | 不因“国密”自动满足某产品认证；本地文档明确可移植实现可能秘密相关查表，存在缓存时序风险。支持 CBC/ECB/CTR/OFB/CFB/GCM/CCM/XTS，不能假设所有 OpenSSL 版本都支持这些模式 | K：`sm4-gcm-v1` | `EVP_CIPHER-SM4.pod` |
| ARIA-128/192/256，分组密码，D | 需要 ARIA 的标准/协议互通；提供 GCM、CCM 及常规模式 | 加速与生态不如 AES 普遍，非 AEAD 模式还需认证；不是 AES 的直接格式替代 | — | `EVP_CIPHER-ARIA.pod` |
| Camellia-128/192/256，分组密码，D | 已使用 Camellia 的协议/文件兼容；128 位块，提供多种模式 | 此 provider 的 Camellia 不能凭 AES 的模式清单推定支持 GCM；常规模式不认证，生态和加速较有限 | — | `EVP_CIPHER-CAMELLIA.pod` |
| ChaCha20，流密码，D | 适合软件实现、无 AES 硬件加速的设备；无需分组填充 | 单独使用不认证，可被修改；必须对齐 OpenSSL 原始 ChaCha20 的计数器/nonce 布局，不得与 AEAD 的 96 位 nonce 格式混用；避免密钥流复用 | — | `EVP_CIPHER-CHACHA.pod` |
| ChaCha20-Poly1305，AEAD，D | 通信认证加密；软件性能良好，组合封装减少使用错误 | 256 位密钥、96 位 nonce、128 位标签；同密钥 nonce 唯一，验证成功前不能接受明文，不提供防重放 | — | `EVP_CIPHER-CHACHA.pod` |
| Ascon-AEAD128，轻量 AEAD，D | 资源受限设备；128 位密钥/nonce/标签，支持 AAD | 当前实现按 SP 800-232，不能与旧 Ascon 草案格式混用；nonce 必须唯一，不抗 nonce 误用，标签失败须丢弃输出；部署生态需评估 | — | `EVP_CIPHER-ASCON-AEAD128.pod` |
| 3DES（2-key/3-key），分组密码，D | 读取旧格式，兼容存量系统 | 64 位块、数据量限制及性能劣势；不推荐新加密。名义 168 位不等于 168 位安全强度；2-key 更不应新增使用 | — | `EVP_CIPHER-DES.pod` |
| DES / DESX，分组密码，L | 旧文件互通 | DES 56 位有效密钥不能抵抗现代穷举；DESX 也不是现代 AEAD，64 位块；仅受控历史兼容，不用于新保护 | — | `EVP_CIPHER-DES.pod` |
| Blowfish，分组密码，L | 旧软件格式兼容，密钥长度灵活 | 64 位块造成大数据量碰撞风险；不是 bcrypt；不要据此实现密码存储或新通信协议 | — | `EVP_CIPHER-BLOWFISH.pod` |
| CAST5，分组密码，L | 旧 PGP 等格式兼容 | 64 位块，旧算法生态；不推荐新系统，需独立认证及使用量限制 | — | `EVP_CIPHER-CAST.pod` |
| IDEA，分组密码，L | 历史加密文件互通，128 位密钥 | 64 位块、缺乏现代 AEAD 封装和广泛硬件加速；仅保留兼容用途 | — | `EVP_CIPHER-IDEA.pod` |
| RC2（含 40/64 位变体），分组密码，L | 旧 PKCS 容器读取 | 有效密钥位数必须准确匹配；短密钥不安全，64 位块限制；不推荐新加密 | — | `EVP_CIPHER-RC2.pod` |
| RC4 / RC4-40 / RC4-HMAC-MD5，流密码/组合，L | 只为旧协议兼容保留 | 密钥流偏差等安全问题；RC4-40 尤其脆弱。组合名称不恢复安全性，不推荐使用 | — | `EVP_CIPHER-RC4.pod` |
| RC5，分组密码，L+E | 特定旧数据格式互通，参数化设计 | 默认禁用，需 `enable-rc5`；本实现变体与轮数需对齐，64 位块限制，不推荐新协议 | — | `EVP_CIPHER-RC5.pod` |
| SEED，分组密码，L | 需要 SEED 的旧系统互通，128 位密钥和块 | legacy 加载需求，应用生态有限；不自动提供认证，新系统优先协议约定的现代 AEAD | — | `EVP_CIPHER-SEED.pod` |
| NULL cipher，空变换，D | 测试、接口兼容，无变换开销 | 完全不提供机密性，禁止当作加密保护 | — | `EVP_CIPHER-NULL.pod` |

重要模式差异（以各算法实际支持列表为准）：

| 模式 | 优点及用途 | 限制和必须对齐的参数 |
|---|---|---|
| GCM | 可并行、支持 AAD，通信 AEAD | 同密钥 nonce 必须唯一，常用 12 字节 IV；双方明确标签长度及密文/标签布局，验证成功前不释放明文 |
| CCM | 基于分组密码的 AEAD，适合已规定 CCM 的协议 | 长度和 nonce/标签参数有联动；EVP 调用顺序及预告明文长度要求与 GCM 不同 |
| OCB | 高效 AEAD | 需双方明确支持；nonce 唯一，参数与 GCM 不兼容；不在此版 FIPS provider 的 AES 列表 |
| SIV / GCM-SIV | 降低偶发 nonce 重用的灾难性后果 | 仍应避免重用，可能暴露重复消息；不是无限安全预算。SIV 使用双份 AES 密钥材料；此版实现不支持多次 payload Update 的流式处理；192 位 GCM-SIV 不能假定其他实现支持 |
| CBC / CBC-CTS | 存量格式，CTS 可减少填充扩张 | CBC IV 需不可预测；常规 CBC 需约定 PKCS#7 或禁用填充，并先认证再处理；有填充预言机风险。CTS 变体须一致 |
| CTR / OFB / CFB（CFB1/8/128） | 可处理非整块数据，适合指定协议 | 本身不认证；CTR 计数器不能复用/回绕，CFB 位宽必须匹配，IV 规则依模式确定；不可把模式互换当作迁移 |
| ECB | 独立块运算、测试向量 | 暴露重复块结构，不用于一般业务数据加密 |
| XTS | 磁盘扇区存储，支持 tweak 和 CTS | 不认证、不用于通用消息；双份密钥，数据单元编号/长度一致；关注最后块与 Update 约束 |
| WRAP / WRAP-PAD / INV | 专门封装密钥，PAD 支持非规则长度 | 不是通用大文件 AEAD，INV 操作方向和输出布局须一致；此版不支持多次 payload Update |
| AES-CBC-HMAC-SHA1/SHA256 及 ETM（含 SHA512） | 特定协议记录的组合实现 | 受 CPU 和实现条件限制；不可作为自定义通用 AEAD，必须遵循协议及本地接口文档；旧 SHA1 套件不作新设计推荐 |

## 非对称加密

| 算法与可用性 | 用途和优点 | 缺点、限制、安全注意 | 项目使用 | 本地来源 |
|---|---|---|---|---|
| RSA 加密（优先 OAEP），D | 公钥封装小型秘密；成熟证书和工具生态 | 消息长度受模数与 OAEP 摘要限制，不能直接加密大文件；明确 OAEP/MGF 摘要和 label。PKCS#1 v1.5 解密需防预言机，不用裸 RSA 加密业务数据；不抗量子 | —，`auth-rsa` 是签名 | `EVP_ASYM_CIPHER-RSA.pod` |
| SM2 加密，D | 国密公钥加密，适合密钥或短消息封装 | 不能等同 SM2 签名或 ECDH；曲线、摘要、ASN.1 DER 与 C1/C2/C3 裸拼接格式必须明确；随机数和私钥需保护，不抗量子 | —，`auth-sm2` 是签名 | `EVP_ASYM_CIPHER-SM2.pod`、`EVP_PKEY-SM2.pod` |

## 数字签名

所有签名都不隐藏消息，也不单独阻止重放；需可信公钥绑定、上下文和消息新鲜性。

| 算法与可用性 | 用途和优点 | 缺点、限制、安全注意 | 项目使用 | 本地来源 |
|---|---|---|---|---|
| RSA-PSS / RSA PKCS#1 v1.5 签名，D | 身份、证书、软件签名，兼容生态广；PSS 有明确随机化编码 | 大密钥和签名开销；PSS 摘要、MGF1、盐长严格匹配；不要将签名当加密，避免裸 RSA 及弱摘要。旧 v1.5 只按协议使用，不抗量子 | K：`auth-rsa`（PSS） | `EVP_SIGNATURE-RSA.pod` |
| DSA，D | 老协议验签及兼容 | nonce 错误会泄露私钥，参数管理复杂；不推荐新签名设计，支持接口不代表 FIPS 允许新签名；不抗量子 | — | `EVP_SIGNATURE-DSA.pod` |
| ECDSA，D | 证书/身份签名，同等级下密钥较小 | 随机或确定性 nonce 必须安全；曲线、摘要、DER 与 raw r/s 编码要一致，防侧信道；不抗量子 | — | `EVP_SIGNATURE-ECDSA.pod` |
| SM2 签名，D | 国密身份与指令认证，公钥可独立验签 | 用户 ID、SM3/ZA、曲线、签名 DER 编码必须一致；不能将普通 ECDSA 当 SM2；需安全 nonce，不抗量子 | K：`auth-sm2` | `EVP_SIGNATURE-SM2.pod`、`EVP_PKEY-SM2.pod` |
| Ed25519 / Ed25519ctx / Ed25519ph，D | 短公钥和签名、确定性签名，降低每次签名随机数依赖 | 纯模式与 ctx/ph 不互换；纯 Ed25519 不接受随意外置摘要。密钥生成仍需熵，私钥和实现仍需防护；不抗量子 | K：`auth-ed25519`（纯 Ed25519） | `EVP_SIGNATURE-ED25519.pod` |
| Ed448 / Ed448ph，D | 更高传统安全余量，确定性签名 | 比 Ed25519 更大、更慢；context/prehash 要匹配，协议支持较少，不抗量子 | — | `EVP_SIGNATURE-ED25519.pod`（该页同时描述 Ed448） |
| ML-DSA-44/65/87，D | 基于格的后量子签名；三档参数可权衡成本 | 密钥和签名大于传统 ECC；纯/预哈希、context、external mu 必须按接口处理；不得与旧 Dilithium 草案编码混用 | — | `EVP_SIGNATURE-ML-DSA.pod` |
| SLH-DSA，SHA2/SHAKE × 128/192/256 × s/f，D | 基于哈希、无状态的后量子签名，避免有状态签名的计数器管理 | 签名很大，签名耗时高；s 偏小签名、f 偏速度，安全级别和哈希族不同；不能把旧 SPHINCS+ 原样视为同格式 | — | `EVP_SIGNATURE-SLH-DSA.pod` |
| LMS，D+E | 验证已有有状态哈希签名 | **默认禁用，需 enable-lms；本版本仅验签，不支持生成私钥或签名**。本地文档建议新部署使用 ML-DSA/SLH-DSA；不能替换 GmSSL 的完整 LMS/HSS 签名能力 | — | `EVP_SIGNATURE-LMS.pod` |

RSA/ECDSA/DSA 的固定摘要组合名（如 RSA-SHA256）并非新的算法家族，沿用对应签名和摘要限制。通过 `EVP_SIGNATURE` 暴露的 HMAC/CMAC/SipHash/Poly1305 是兼容适配器，仍是共享密钥 MAC，不是公钥数字签名。

## 摘要、XOF 与内部摘要

摘要不是加密；无密钥摘要不能认证发送者，不能隐藏低熵输入，也不能直接代替口令散列。

| 算法与可用性 | 用途和优点 | 缺点、限制、安全注意 | 项目使用 | 本地来源 |
|---|---|---|---|---|
| SHA-2（224/256/384/512、512/224、512/256），D | 文件完整性、签名摘要、KDF/MAC 基础，成熟且有加速 | 需匹配精确变体，SHA512/256 不是简单截断 SHA512；一般摘要不能当 MAC；内部 SHA256-192 只按其协议用途使用 | R：SHA-256，`index.py`、`retrieval.py`、`server.py`、`client.py`、发布校验脚本；由 Python hashlib 实现，不是本地 4.2 链接 | `EVP_MD-SHA2.pod`、`providers/defltprov.c` |
| SHA-3（224/256/384/512），D | 与 SHA-2 不同的海绵结构，标准摘要 | 常见平台性能/加速不同，不能与同长度 Keccak 输出互换 | — | `EVP_MD-SHA3.pod` |
| KECCAK（224/256/384/512），D | 需要原始 Keccak 的协议/生态 | 域分隔与 SHA3 不同，必须按协议选用；不是“另一个名称的 SHA3” | — | `EVP_MD-KECCAK.pod` |
| SHAKE128/256，XOF，D | 可变长度输出，后量子构造与协议派生组件 | 输出长度由协议固定；短输出降低安全余量，不能认为任意长输出增加无限安全性 | — | `EVP_MD-SHAKE.pod` |
| cSHAKE128/256，定制 XOF，D | 支持功能名和定制字符串，便于域分隔 | N/S 与输出长度要一致；不是直接 MAC，须区分 cSHAKE 与 SHAKE | — | `EVP_MD-SHAKE.pod`（含 CSHAKE） |
| CSHAKE-KECCAK / KECCAK-KMAC 别名，D | cSHAKE/KMAC 的底层构件，便于内部复用 | 不作为通用摘要或直接认证方案；应优先上层 cSHAKE/KMAC API | — | `EVP_MD-SHAKE.pod`（含 CSHAKE-KECCAK） |
| BLAKE2b-512 / BLAKE2s-256，D | 软件摘要，分别适合不同字宽平台 | 不能混同带密钥 BLAKE2 MAC；摘要输出长度和互操作格式要明确，不替代口令 KDF | — | `EVP_MD-BLAKE2.pod` |
| SM3，D | 国密摘要、SM2 和 HMAC-SM3 基础，256 位输出 | 无密钥摘要不认证，不能直接存密码；协议必须指定 SM3，不能静默换 SHA256 | K：`gmssl-sm3` | `EVP_MD-SM3.pod` |
| SHA-1，D，不推荐抗碰撞用途 | 读取历史摘要及受约束的旧协议 | 已存在碰撞攻击，不用于新数字签名/抗碰撞标识；HMAC-SHA1 与裸摘要风险不能简单等同，但新设计应选现代摘要 | — | `EVP_MD-SHA1.pod` |
| MD5 / MD5-SHA1，D，不推荐 | 历史兼容；组合用于旧 TLS 处理 | MD5 已有实用碰撞，组合也不能作为新安全摘要；校验非对抗错误不等于防恶意篡改 | — | `EVP_MD-MD5.pod`、`EVP_MD-MD5-SHA1.pod` |
| RIPEMD160，D/L | 旧协议的 160 位摘要兼容 | 碰撞安全余量低于 256 位摘要；不推荐作为新通用安全摘要，default/legacy 均有注册 | — | `EVP_MD-RIPEMD160.pod` |
| MD2，L+E | 古老格式兼容 | 默认禁用，需要 enable-md2；安全性不足，不推荐使用 | — | `EVP_MD-MD2.pod` |
| MD4，L | 历史协议验证 | 已不具备安全抗碰撞能力，不能用于新保护 | — | `EVP_MD-MD4.pod` |
| MDC2，L | 旧 DES 基础摘要格式兼容 | 输出和底层设计的安全余量有限，生态老旧；不推荐新用途 | — | `EVP_MD-MDC2.pod` |
| Whirlpool，L | 已规定 512 位 Whirlpool 的历史数据兼容 | 较少支持和加速；legacy 不意味着所有算法均已被攻破，但新设计不建议新增依赖 | — | `EVP_MD-WHIRLPOOL.pod` |
| NULL digest，D | 接口适配/测试 | 不产生安全摘要，不能证明完整性 | — | `EVP_MD-NULL.pod` |
| ML-DSA-MU，专用预处理，D | 外部计算 ML-DSA 的 mu，可支持大消息预处理 | 必须绑定公钥、context、纯/预哈希模式及摘要 OID；不是通用哈希，预先给出的 mu 需可信 | — | `EVP_MD-ML-DSA-MU.pod` |

## 消息认证码（MAC）

| 算法与可用性 | 用途和优点 | 缺点、限制、安全注意 | 项目使用 | 本地来源 |
|---|---|---|---|---|
| HMAC，D | 共享密钥消息认证，可复用 SHA-2/SM3 等摘要；无须 nonce | 不能区分多个持钥者责任，不是数字签名；选强密钥、合理标签长度，不能简单 hash(key||msg) 替代 | K：`gmssl-sm3`；R：`server.py` 仅用 `hmac.compare_digest` 常量时间比较，**没有计算 HMAC** | `EVP_MAC-HMAC.pod` |
| CMAC，D | 基于分组密码的认证，适合已有 AES 环境 | 标签长度和每密钥用量限制，不能用裸 CBC 最后一块替代任意长度 CMAC | — | `EVP_MAC-CMAC.pod` |
| GMAC，D | GCM 的认证部分，适合不加密但需认证的消息 | 同密钥 IV 必须唯一；不保密，重用 IV 风险严重 | — | `EVP_MAC-GMAC.pod` |
| KMAC128/256（含 XOF 参数），D | 基于 cSHAKE 的认证与域分隔，可变输出 | 定制字符串、输出长度、XOF 设置必须一致；共享密钥验证不等于公开验签 | — | `EVP_MAC-KMAC.pod` |
| BLAKE2BMAC / BLAKE2SMAC，D | 带密钥 BLAKE2，软件认证效率好 | 与无密钥摘要分开使用；参数和输出长度要固定，协议支持需核对 | — | `EVP_MAC-BLAKE2.pod` |
| SipHash，D | 短消息 keyed hash，抵御哈希表碰撞拒绝服务 | 标签较短，非通用大规模文件签名/摘要；轮数、输出长度和每密钥使用量有约束 | — | `EVP_MAC-Siphash.pod` |
| Poly1305，D | 快速一次性消息认证，常与 ChaCha20 组合 | 一次性认证密钥不能复用；应用优先使用已封装 AEAD，不自行重复使用固定 Poly1305 密钥 | — | `EVP_MAC-Poly1305.pod` |

## 密钥交换与密钥封装（KEM）

这些机制产生共享秘密，不直接加密业务正文，也不单独认证对端；须结合可信身份、上下文 KDF 与 AEAD。

| 算法与可用性 | 用途和优点 | 缺点、限制、安全注意 | 项目使用 | 本地来源 |
|---|---|---|---|---|
| DH / DHX，D | 有限域共享秘密，临时密钥可用于前向保密；已有组标准 | 参数和公钥校验复杂，较大消息/运算成本；不抗量子，避免弱组和静态复用 | — | `EVP_KEYEXCH-DH.pod` |
| ECDH，D | 较小密钥的传统共享秘密，常用于 TLS | 曲线、点编码、公钥验证需一致；原始共享点不能直接当多用途密钥；不抗量子 | — | `EVP_KEYEXCH-ECDH.pod` |
| X25519 / X448，D | 简洁固定曲线接口；X25519 广泛应用，X448 提供更高传统余量 | 必须处理非法/全零共享秘密；X448 更大更慢，不抗量子，不能与 Ed25519/Ed448 签名密钥随意混用 | — | `EVP_KEYEXCH-X25519.pod` |
| RSA RSASVE KEM，D | 封装随机秘密，适合规定该机制的组合方案 | 本接口只提供 RSASVE；其无填充 RSA 运算用于特定秘密封装，**不授权裸 RSA 加密任意数据**。需按标准加 KDF 和确认/认证，不抗量子 | — | `EVP_KEM-RSA.pod` |
| EC / X25519 / X448 DHKEM，D | HPKE 类机制的共享秘密封装，组合临时 DH 与 HKDF | 曲线、KEM ID 和 context 要匹配；KEM 本身不等于完整 HPKE/消息加密，不抗量子 | — | `EVP_KEM-EC.pod`、`EVP_KEM-X25519.pod` |
| ML-KEM-512/768/1024，D | 格基础后量子密钥封装，三档安全/成本权衡 | 公钥及封装数据较大，需严格长度/编码校验；隐式拒绝不是应用可依赖的简单“解密错误”信号，须靠后续认证；不能与旧 Kyber 编码自动互换 | — | `EVP_KEM-ML-KEM.pod` |
| MLX 混合 KEM，D | 结合传统和后量子秘密，降低单一机制失效风险 | 包括 MLKEM512X25519、X25519MLKEM768、X448MLKEM1024、SecP256r1MLKEM512/768、SecP384r1MLKEM1024、curveSM2MLKEM768；需整套参数、拼接和协商一致，开销更大，禁止自造混合方式 | — | `EVP_PKEY-MLX-KEM.pod` |

`curveSM2` 密钥管理条目用于曲线相关操作，不能据此推定实现 GmSSL 的 SM2 身份绑定密钥交换协议。HKDF/TLS1-PRF/SCRYPT 的 key-exchange 适配接口也不是新的 DH 协议。

## 密钥派生（KDF）

除专门口令 KDF 外，不应把低熵密码直接交给快速派生函数。所有派生都不能凭空增加输入熵。

| 算法与可用性 | 用途和优点 | 缺点、限制、安全注意 | 项目使用 | 本地来源 |
|---|---|---|---|---|
| HKDF / HKDF-SHA256/384/512，D | Extract/Expand 派生多个会话密钥，info 域分隔明确 | 非口令加固；salt/info/摘要须一致，Expand 上限 255×摘要长度 | K：`gmssl-hkdf` | `EVP_KDF-HKDF.pod` |
| TLS13-KDF，D | TLS 1.3 专用密钥计划和标签编码 | 不作为通用密码存储；标签、上下文及阶段必须遵守协议 | — | `EVP_KDF-TLS13_KDF.pod` |
| TLS1-PRF，D | 旧 TLS 密钥计划兼容，协议明确 | 旧版 MD5/SHA1 组合不用于新设计；不能替代 TLS 1.3 KDF，摘要和种子顺序必须匹配 | — | `EVP_KDF-TLS1_PRF.pod` |
| IKEV2KDF，D | IKEv2 PRF+，适用于 IPsec 的已规定派生 | 不是通用口令 KDF；key/seed、PRF、输出上限遵守协议 | — | `EVP_KDF-IKEV2KDF.pod` |
| SSKDF，D | 单步 KDF，可选择摘要/MAC 和上下文信息 | OtherInfo 编码、计数器和输出长度须约定；不是密码拉伸 | — | `EVP_KDF-SS.pod` |
| KBKDF，D | 基于 MAC 的计数器/反馈等密钥派生，用于分离子密钥 | 模式、label/context、计数器长度需一致；输入必须有足够熵 | — | `EVP_KDF-KB.pod` |
| PBKDF2，D | 广泛支持的口令派生，盐和迭代数可调 | 不具备内存困难性，硬件并行攻击成本较低；需随机盐、按部署调参并保存参数，不能照搬低迭代默认值 | — | `EVP_KDF-PBKDF2.pod` |
| scrypt，D | 内存困难的口令派生，提高大规模猜测成本 | N/r/p 与内存上限需验证；过大参数可造成拒绝服务，硬件差异需基准测试 | — | `EVP_KDF-SCRYPT.pod` |
| Argon2d / Argon2i / Argon2id，D | 内存困难口令派生；id 折中两种访问策略 | d 的数据相关访问存在侧信道权衡，i/id 策略不同；内存、迭代、lanes/threads 需调优；并行能力受构建线程支持影响，记录版本与参数 | — | `EVP_KDF-ARGON2.pod` |
| PKCS12KDF，D | PKCS#12 密钥/IV/MAC 派生，读取已有容器 | 历史口令编码、用途 ID 和迭代数需精确匹配；不作为新通用口令存储方案 | — | `EVP_KDF-PKCS12KDF.pod` |
| SNMPKDF，D | SNMP 用户密钥本地化，互通标准协议 | 与引擎 ID、摘要及口令预处理绑定，不能当通用 KDF | — | `EVP_KDF-SNMPKDF.pod` |
| SRTPKDF，D | SRTP 的加密/认证/盐密钥派生 | key/salt/index/label 参数专用，不能替代一般 KDF，必须遵守相应 SRTP 配置 | — | `EVP_KDF-SRTPKDF.pod` |
| SSHKDF，D | SSH 会话密钥派生 | 会话 ID、交换哈希、类型字节和共享秘密格式固定，非口令加固 | — | `EVP_KDF-SSHKDF.pod` |
| X942KDF-ASN1 / X942KDF-CONCAT，D | 有限域 DH 的标准化密钥派生 | 两种 OtherInfo 构造不互换；摘要、UKM 和目标算法需一致，不单独提供身份认证 | — | `EVP_KDF-X942-ASN1.pod`、`EVP_KDF-X942-CONCAT.pod` |
| X963KDF，D | EC 共享秘密及 SharedInfo 的标准派生 | 与 HKDF 结构不同，计数器和 SharedInfo 要一致；不能用于低熵口令 | — | `EVP_KDF-X963.pod` |
| KRB5KDF，D | Kerberos 特定加密类型的派生，互通旧系统 | 依赖 cipher 和常量格式，不等同任意 Kerberos 密码转密钥算法，勿移作一般 KDF | — | `EVP_KDF-KRB5KDF.pod` |
| HMAC-DRBG-KDF，D | RFC 6979 确定性 DSA/ECDSA nonce 派生，减少每次随机 nonce 依赖 | 与随机生成器 HMAC-DRBG 不同；固定输入派生，不能当熵源或一般随机密钥生成器 | — | `EVP_KDF-HMAC-DRBG.pod` |
| PBKDF1，L | PKCS#5 旧口令格式兼容 | 输出受摘要长度限制，旧设计；不推荐新增使用 | — | `EVP_KDF-PBKDF1.pod` |
| PVKKDF，L | Microsoft PVK 旧私钥格式兼容 | 与旧格式及弱算法绑定，不作新密钥保护方案 | — | `EVP_KDF-PVKKDF.pod` |

## 随机数、密钥管理与证书

| 能力与可用性 | 用途和优点 | 缺点、限制、安全注意 | 项目使用 | 本地来源 |
|---|---|---|---|---|
| CTR-DRBG，D | 基于分组密码的伪随机生成，可用 AES 加速 | 需要可靠熵源和重播种，状态不可克隆/泄露；不是加密模式选择 | — | `EVP_RAND-CTR-DRBG.pod` |
| HASH-DRBG，D | 基于摘要的伪随机生成，标准化状态机制 | 同样依赖熵和重播种，不能用固定 seed 生成生产密钥 | — | `EVP_RAND-HASH-DRBG.pod` |
| HMAC-DRBG，D | 基于 HMAC 的伪随机生成 | 状态、熵和实例化参数必须安全；不要与 HMAC-DRBG-KDF 混淆 | — | `EVP_RAND-HMAC-DRBG.pod` |
| SEED-SRC，D/base | 从系统获取熵，为 DRBG 提供种子 | 系统熵故障必须处理，不是用于业务数据的独立密码算法 | — | `EVP_RAND-SEED-SRC.pod` |
| JITTER，D/base+E | 处理器时序抖动熵源，提供额外熵来源 | 依赖平台能力和健康检测；默认关闭（构建可因其他选项改变），需 enable-jitter 等具体配置确认 | — | `EVP_RAND-JITTER.pod`、`Configure` |
| TEST-RAND，D | 可复现测试向量 | 输入可控，没有生产随机性，禁止生产密钥/nonce 生成 | — | `EVP_RAND-TEST-RAND.pod` |
| EVP_PKEY / OSSL_ENCODER / OSSL_DECODER / OSSL_STORE | 高层密钥管理和 PEM/DER 编解码，减少依赖内部结构 | 不是新增密码算法；PKCS#1、PKCS#8、SPKI、raw key 不能混用，私钥口令保护参数必须匹配 | — | `provider-keymgmt.pod`、`provider-encoder.pod`、`provider-decoder.pod` |
| X.509 / CRL | 公钥身份绑定、信任链和撤销检查 | 不是加密算法；要检查信任锚、主机名、用途、时间和撤销策略，验签成功不等于证书可信 | K：`gmssl-x509` | `x509.pod`、`crypto/x509/x509_vfy.c` |

## 高层接口与迁移边界

新 C 集成应使用 `EVP_CIPHER_fetch` + `EVP_EncryptInit_ex2`/`EVP_DecryptInit_ex2`，`EVP_MD_fetch`、`EVP_MAC_fetch`、`EVP_KDF_fetch`，以及基于 provider 的 `EVP_PKEY` 签名、验签、派生和 KEM API；按算法手册处理返回值、参数及对象释放，避免低层算法结构与旧 ENGINE 路径。参考 `doc/man3/EVP_EncryptInit.pod`、`doc/man3/EVP_DigestSignInit.pod`、`doc/man3/EVP_PKEY_encapsulate.pod`、`doc/man7/ossl-guide-migration.pod`。

本项目没有可替换的 GmSSL 加解密调用，故没有新增 ctypes、命令行加密封装或密码运行依赖。不能把 Python 内建 hashlib 自动声称为链接到提供的 libcrypto。GmSSL 的 SM9、ZUC、XMSS、SM4-FF1 及 SM2 特定密钥交换不在本次内建能力映射中；若其他业务依赖这些能力，须独立评估 provider 或保留原实现，不能换算法掩盖不支持。

这份文档以 UTF-8 原文单独写入 SQLite `knowledge_documents`（ID `openssl-algorithms`），同时保存 SHA-256 与源码版本；`algorithm_descriptions` 保持现有八项服务的查询契约。通过 `GET /v1/documents/openssl-algorithms` 或 `python -m uav_rag document` 读取。修改文档后须生成新数据库并切换服务，文件本身不自动改变已入库版本。
