# 规格一：核心领域与工作流

## 1. 目标

建立所有模块共用的任务状态机、持久工作项、租约、幂等、外部动作和审计机制。

覆盖需求：

- `REQ-STR-002` 至 `REQ-STR-006`
- `REQ-FEI-005` 至 `REQ-FEI-007`
- `REQ-PUB-002`、`REQ-PUB-008` 至 `REQ-PUB-012`
- `REQ-OPS-002` 至 `REQ-OPS-007`

## 2. 核心状态

内容任务状态枚举：

```text
pending_research
researching
pending_topic_approval
producing
quality_checking
pending_publish_approval
scheduled
publishing
published
publication_uncertain
publication_failed
engaging
pending_review
archived
waiting_human
cancelled
```

合法转换由单一状态转换表定义，不允许业务模块直接写状态字段。

关键规则：

- `pending_topic_approval` 只有有效选题审批才能进入 `producing`。
- `pending_publish_approval` 只有有效发布审批才能进入 `scheduled`。
- 研究或排期执行器在不可恢复失败时可以进入 `waiting_human`。
- `publishing` 失败且结果未知进入 `publication_uncertain`。
- `publication_uncertain` 只能经过平台核验后进入 `published`、`publication_failed` 或 `waiting_human`。
- `archived` 和 `cancelled` 默认终态。

## 3. 数据表

### 3.1 `content_tasks`

| 字段 | 类型 | 约束 |
|---|---|---|
| `id` | 文本 | 主键 |
| `account_id` | 文本 | 非空，外键 |
| `state` | 文本 | 非空 |
| `strategy_version_id` | 文本 | 非空 |
| `primary_goal` | 文本 | 非空 |
| `content_pillar` | 文本 | 可空，选题批准后必填 |
| `current_topic_version_id` | 文本 | 可空 |
| `current_content_version_id` | 文本 | 可空 |
| `scheduled_at` | 时间 | 可空 |
| `row_version` | 整数 | 乐观锁 |
| `created_at` | 时间 | 非空 |
| `updated_at` | 时间 | 非空 |

### 3.2 `workflow_instances`

| 字段 | 类型 | 约束 |
|---|---|---|
| `id` | 文本 | 主键 |
| `task_id` | 文本 | 非空 |
| `workflow_type` | 文本 | 非空 |
| `status` | 文本 | 非空 |
| `current_step` | 文本 | 可空 |
| `context_json` | 文本 | 只保存非敏感小型上下文 |
| `created_at` | 时间 | 非空 |
| `completed_at` | 时间 | 可空 |

### 3.3 `work_items`

| 字段 | 类型 | 约束 |
|---|---|---|
| `id` | 文本 | 主键 |
| `workflow_id` | 文本 | 非空 |
| `task_id` | 文本 | 非空 |
| `step_type` | 文本 | 非空 |
| `status` | 文本 | 非空 |
| `attempt` | 整数 | 默认零 |
| `max_attempts` | 整数 | 默认二 |
| `available_at` | 时间 | 非空 |
| `lease_owner` | 文本 | 可空 |
| `lease_expires_at` | 时间 | 可空 |
| `idempotency_key` | 文本 | 唯一 |
| `input_ref` | 文本 | 可空 |
| `output_ref` | 文本 | 可空 |
| `error_code` | 文本 | 可空 |
| `error_detail` | 文本 | 脱敏后保存 |

### 3.4 `external_actions`

| 字段 | 类型 | 约束 |
|---|---|---|
| `id` | 文本 | 主键 |
| `action_type` | 文本 | 非空 |
| `resource_type` | 文本 | 非空 |
| `resource_id` | 文本 | 非空 |
| `idempotency_key` | 文本 | 唯一 |
| `status` | 文本 | `prepared`、`running`、`succeeded`、`failed`、`uncertain` |
| `request_digest` | 文本 | 非空 |
| `external_id` | 文本 | 可空 |
| `result_json` | 文本 | 脱敏摘要 |
| `evidence_ref` | 文本 | 可空 |
| `attempt` | 整数 | 非空 |
| `created_at` | 时间 | 非空 |
| `completed_at` | 时间 | 可空 |

### 3.5 `audit_logs`

包含操作者类型、操作者标识、动作、资源、前状态、后状态、原因、追踪标识和时间。

## 4. 命令

### 4.1 `CreateContentTask`

输入：

```json
{
  "command_id": "唯一标识",
  "account_id": "账号标识",
  "source": "scheduled",
  "primary_goal": "content_validation",
  "strategy_version_id": "策略版本"
}
```

结果：

- 创建 `content_tasks`；
- 创建研究工作流；
- 创建首个 `collect_research` 工作项；
- 写审计日志。

### 4.2 `TransitionTask`

仅供应用服务内部使用。输入当前状态、目标状态、原因和期望行版本。状态不匹配时返回 `STATE_CONFLICT`。

### 4.3 `PauseAutomation`

支持范围：

- `research`
- `publishing`
- `comments`
- `all`

暂停只阻止尚未发生的外部动作。已经发送但结果未知的动作进入核验，不得直接取消状态判断。

### 4.4 `ResumeAutomation`

仅授权运营者可执行。评论风控熔断必须提供人工恢复原因。

## 5. 工作项领取

领取算法：

1. 开启短事务；
2. 查找 `pending` 且 `available_at` 已到期的最早工作项；
3. 排除对应自动化范围已暂停的工作项；
4. 条件更新为 `running`，写入租约所有者和过期时间；
5. 提交事务；
6. 执行任务。

多个进程同时领取时，只有条件更新成功的进程获得任务。

## 6. 步骤提交

步骤成功时，在一个事务中：

1. 保存步骤输出；
2. 更新领域状态；
3. 将当前工作项设为完成；
4. 创建下一工作项或待审批记录；
5. 写审计日志。

步骤失败时：

- 可重试错误：增加尝试次数并设置下次可执行时间；
- 需要人工：工作项失败，任务转 `waiting_human`；
- 结果未知：外部动作设为 `uncertain`，任务进入专用核验状态；
- 永久失败：终止当前工作流并告警。

## 7. 幂等规则

- 命令表保存已处理 `command_id`。
- 工作项 `idempotency_key` 唯一。
- 外部动作 `idempotency_key` 唯一。
- 状态转换使用行版本防止覆盖。
- 重试复用原幂等键。
- 人工重新执行必须创建新命令，但不能绕过原外部动作核验。

## 8. 错误代码

| 错误码 | 含义 | 可重试 |
|---|---|---|
| `STATE_CONFLICT` | 当前状态不允许操作 | 否 |
| `ROW_VERSION_CONFLICT` | 对象已被其他事务更新 | 是 |
| `WORK_ITEM_LEASE_LOST` | 工作项租约丢失 | 否，当前执行停止 |
| `IDEMPOTENCY_CONFLICT` | 相同键对应不同请求 | 否 |
| `AUTOMATION_PAUSED` | 对应自动化已暂停 | 延后 |
| `EXTERNAL_RESULT_UNCERTAIN` | 外部动作结果未知 | 先核验 |
| `MAX_ATTEMPTS_REACHED` | 已达到重试上限 | 人工 |

## 9. 验收条件

- `AC-001-01` 非法状态转换被拒绝且任务状态不变。
- `AC-001-02` 相同创建命令重复提交只产生一个任务。
- `AC-001-03` 两个工作进程竞争同一工作项时只有一个获得租约。
- `AC-001-04` 工作进程崩溃后，租约过期的工作项可以恢复。
- `AC-001-05` 状态更新和下一工作项创建具有原子性。
- `AC-001-06` 自动化暂停后不再发生新的对应外部副作用。
- `AC-001-07` 外部结果未知时不会自动重复执行。
- `AC-001-08` 所有状态变化具有审计记录。

## 10. 测试重点

- 状态转换表全路径测试；
- 乐观锁冲突；
- 租约过期和续约；
- 事务提交前后进程崩溃；
- 重复命令；
- 相同幂等键不同请求；
- 暂停与发布临界竞争；
- 外部成功但响应丢失。
