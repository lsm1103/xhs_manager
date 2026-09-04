# 需求追踪矩阵

## 1. 目的

确保产品需求中的每个领域都有实现规格和验收入口。范围表达中的起止编号均为包含关系。

## 2. 领域追踪

| 产品需求范围 | 主规格 | 补充规格 | 状态 |
|---|---|---|---|
| `REQ-STR-001` 至 `REQ-STR-006` | `012-account-strategy.md` | `001-core-domain-workflow.md`、`011-operations-security.md` | 已覆盖 |
| `REQ-FEI-001` 至 `REQ-FEI-008` | `009-feishu-integration.md` | `005-review-and-approval.md`、`001-core-domain-workflow.md` | 已覆盖 |
| `REQ-RES-001` 至 `REQ-RES-010` | `002-research-pipeline.md` | `011-operations-security.md` | 已覆盖 |
| `REQ-TOP-001` 至 `REQ-TOP-008` | `003-topic-engine.md` | `005-review-and-approval.md` | 已覆盖 |
| `REQ-CON-001` 至 `REQ-CON-011` | `004-content-generation.md` | `005-review-and-approval.md` | 已覆盖 |
| `REQ-QA-001` 至 `REQ-QA-010` | `005-review-and-approval.md` | `004-content-generation.md` | 已覆盖 |
| `REQ-APR-001` 至 `REQ-APR-006` | `005-review-and-approval.md` | `009-feishu-integration.md` | 已覆盖 |
| `REQ-PUB-001` 至 `REQ-PUB-012` | `006-publishing.md` | `001-core-domain-workflow.md`、`011-operations-security.md` | 已覆盖 |
| `REQ-COM-001` 至 `REQ-COM-015` | `007-comment-operations.md` | `011-operations-security.md` | 已覆盖 |
| `REQ-INS-001` 至 `REQ-INS-006` | `010-knowledge-base.md` | `007-comment-operations.md`、`003-topic-engine.md` | 已覆盖 |
| `REQ-ANA-001` 至 `REQ-ANA-010` | `008-analytics.md` | `010-knowledge-base.md` | 已覆盖 |
| `REQ-KNO-001` 至 `REQ-KNO-008` | `010-knowledge-base.md` | `002-research-pipeline.md`、`009-feishu-integration.md` | 已覆盖 |
| `REQ-OPS-001` 至 `REQ-OPS-007` | `011-operations-security.md` | `001-core-domain-workflow.md` | 已覆盖 |

## 3. 产品验收到规格

| 产品验收主题 | 主要验收条件 |
|---|---|
| 飞书创建研究 | `AC-009-03`、`AC-002-01` |
| 三来源证据选题 | `AC-002-01` 至 `AC-002-09` |
| 选题审批闸门 | `AC-003-07`、`AC-005-03` |
| 图文与自动检查 | `AC-004-01` 至 `AC-004-09`、`AC-005-01` |
| 发布预览和审批 | `AC-005-04` 至 `AC-005-09`、`AC-009-05` |
| 版本变化使审批失效 | `AC-004-08`、`AC-005-05` |
| 审批后自动发布 | `AC-006-01` 至 `AC-006-09` |
| 防止重复发布 | `AC-001-07`、`AC-006-03`、`AC-006-04` |
| 低风险评论回复 | `AC-007-01` 至 `AC-007-09` |
| 高风险评论升级 | `AC-007-01`、`AC-007-06` |
| 多窗口数据与复盘 | `AC-008-01` 至 `AC-008-08` |
| 周复盘和知识沉淀 | `AC-010-04` 至 `AC-010-08` |
| 服务重启恢复 | `AC-001-04`、`AC-011-06` |
| 密钥保护 | `AC-011-01` 至 `AC-011-03` |
| 端到端演练 | 所有规格完成定义与故障演练 |

## 4. 未进入最小版本的需求

以下能力属于后续阶段，不创建第一阶段实现规格：

- X 信息源；
- 抖音信息源；
- 视频生成；
- 视频发布；
- 多平台同步；
- 多账号；
- 产品销售和客户关系管理；
- 自动私信营销。

如果其中任一能力进入开发范围，必须先更新产品需求和技术顶层设计，再新增规格。

## 5. 变更规则

每次产品需求变更必须：

1. 新增或修改需求编号；
2. 更新本矩阵；
3. 更新对应领域规格；
4. 更新验收条件；
5. 更新测试；
6. 检查是否改变审批、安全或外部副作用边界。

