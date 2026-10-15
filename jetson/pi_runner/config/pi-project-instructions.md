# UAV 项目查询

本项目的 Kubernetes 命名空间为 `uav-mvp`。用户询问 k3s Pod、Deployment 或服务状态时，直接通过 Kubernetes MCP 查询此命名空间。

- Pod：`pods_list_in_namespace({"namespace":"uav-mvp"})`。
- Deployment：`resources_list({"apiVersion":"apps/v1","kind":"Deployment","namespace":"uav-mvp"})`。
- Events、Service、日志等命名空间查询也显式传入 `namespace: "uav-mvp"`。
- 工具返回 forbidden 时，先核对请求命名空间与参数；其他命名空间被拒绝不代表项目资源无权限。
- 根据实际工具结果报告名称、就绪数和状态；metrics API 不可用表示指标服务缺失，不等于 RBAC 拒绝。
- 当前是只读查询环境，写操作任务如实报告尚未接通。
