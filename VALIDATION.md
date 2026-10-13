# 本地验收记录

更新：2026-10-13，按需查询版本。

- 资料来源：工作区 GmSSL 源码，401 文件、3986 片段，schema 2。
- 语料版本：`eea0294c300203072799024c233d82671b1cc91976ae7f159ce4c66323bd4021`。
- 环境：Windows、Python 3.11.4、SQLite FTS5。
- 本地回归：30 项通过，0 跳过；UAV_PROJECT_ROOT 指向原无人机源码快照。
- 检索覆盖：真实 HTTP、鉴权、中文查询、算法描述数据库读取、引用逐行校验、证据不足、旧接口兼容。
- 边界覆盖：16,000 字符任务保持原样，16 个候选输入去重为 8 项，4,096 字节分页可完整还原描述及引用，短查询限制、无效和跨版本游标、累计预算、失败重试次数、断网、伪造引用。
- 接入覆盖：stdio MCP 子进程经真实 HTTP 查询、工具错误响应、原 Guard 工具与查询前置检查、原 PiRunner 子进程和任务幂等。
- 分发覆盖：目录逃逸拒绝、归档哈希校验、缓存及架构约束。
- 演示：真实 SQLite 和 HTTP，确定性 Agent 替身按需查询 2 页，选择 SM2/SM4；状态 planned，requires_execution_verification=true。

复现：

```powershell
$env:UAV_PROJECT_ROOT='C:\Users\86187\Desktop\pi_uav_k3-codex-service-layout'
python -X utf8 -m unittest discover -s tests -v
python -X utf8 demo.py
```

未设置 UAV_PROJECT_ROOT 时跳过两个原项目契约测试，其余测试仍可运行。

模型和最终执行回执由测试替身产生；没有运行真实模型、启动密码服务或发飞控指令。下载测试只使用传输测试字节，不是可运行 OCI 镜像。本次未更新云端与 k8s，Pod 可达性和实际 Agent 工具调用须按 ON-DEMAND-RAG.md 验收。未重跑原无人机项目全套测试，不宣称其全套回归通过。
