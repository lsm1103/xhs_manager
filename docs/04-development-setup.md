# 第一阶段开发与运行说明

## 1. 当前完成范围

当前代码实现：

- Python 项目与依赖管理；
- SQLite 配置、写前日志、外键和忙等待；
- Alembic 数据库迁移；
- 账号与初始策略；
- 内容任务状态机；
- 命令幂等；
- 持久工作项、租约、重试和暂停；
- 外部动作幂等与结果未知保护；
- 选题审批和发布审批领域服务；
- 飞书标准化回调入口；
- 授权用户校验；
- 重复事件处理；
- 审计日志；
- 健康检查。
- 研究运行、来源执行记录、标准化信号和证据包的持久化及内部审阅接口；
- 规范 URL 与内容摘要值的确定性去重，同时保留独立来源执行记录；
- 搜索摘要不得标记为已核验原始来源；未核验关键证据不能创建证据包。

当前没有实现：

- 真实信息采集连接器（网页、GitHub、小红书）；
- 大模型调用；
- 图片生成；
- 飞书应用机器人发卡片；
- 小红书浏览器连接器；
- 真实发布；
- 真实评论回复。

配置项尝试开启真实发布或评论时，应用会拒绝启动。

## 2. 环境

项目使用 `uv` 管理 Python 和依赖。

初始化：

```bash
cp .env.example .env
uv sync --dev
uv run alembic upgrade head
```

运行接口：

```bash
uv run uvicorn xhs_manager.api:app --host 127.0.0.1 --port 8000
```

健康检查：

```bash
curl http://127.0.0.1:8000/health/live
curl http://127.0.0.1:8000/health/ready
```

## 3. 质量检查

```bash
uv run ruff check src tests migrations
uv run ruff format --check src tests migrations
uv run pytest
uv run alembic check
```

## 4. 创建账号

请求必须携带内部接口令牌：

```bash
curl -X POST http://127.0.0.1:8000/v1/accounts \
  -H "Content-Type: application/json" \
  -H "X-Internal-Token: replace-me" \
  -d '{
    "command_id": "account-bootstrap-001",
    "name": "AI工作流实验员",
    "timezone": "America/Los_Angeles",
    "strategy_config": {
      "target_audience": "希望从对话式AI进阶到可运行工作流的人",
      "persona": "AI工作流实验员",
      "content_pillars": [
        "concept_explainer",
        "workflow_experiment",
        "solution_comparison"
      ]
    },
    "actor_id": "owner"
  }'
```

相同 `command_id` 和相同内容重复请求会返回原结果；相同标识对应不同内容会被拒绝。

## 5. 创建内容任务

```bash
curl -X POST http://127.0.0.1:8000/v1/tasks \
  -H "Content-Type: application/json" \
  -H "X-Internal-Token: replace-me" \
  -d '{
    "command_id": "content-task-001",
    "account_id": "替换为账号标识",
    "primary_goal": "content_validation",
    "actor_id": "owner"
  }'
```

该操作创建：

- 内容任务；
- 内容生命周期工作流；
- 第一个研究工作项；
- 审计记录。

当前没有研究处理器，因此工作项保持待执行，不会生成虚假研究结果。

## 6. 研究审阅最小链路

目前先提供受内部令牌保护的结构化写入和只读审阅接口，用于接入前的契约测试、人工录入或未来只读连接器的安全适配。它不会访问网络、模型、浏览器，也不会保存 Cookie 或 API Key。

最小顺序是：创建研究运行 → 分别记录来源执行成功或失败 → 写入标准化信号 → 创建证据包 → 只读审阅。

- `POST /v1/research-runs`
- `POST /v1/research-runs/{research_run_id}/sources`
- `POST /v1/research-sources/{research_source_id}/signals`
- `POST /v1/research-runs/{research_run_id}/evidence-packages`
- `GET /v1/research-runs/{research_run_id}`、`GET /v1/research-sources/{research_source_id}`、`GET /v1/research-signals/{research_signal_id}`、`GET /v1/evidence-packages/{evidence_package_id}`

重要边界：只有 `primary_source_verified` 信号可作为证据包关键证据；来自 `web_search` 的搜索摘要不能标记为该状态。来源失败会被保留为状态与错误码，其他来源仍可继续完成。研究证据包尚未连接选题确认或发布链路，因此不会绕过现有人工审批。

## 7. 飞书能力边界

### 自定义群机器人

只能用于向指定群发送静态通知，不能接收用户消息或卡片交互。

### 企业自建应用机器人

双向审批需要：

- 企业自建应用；
- 机器人能力；
- 事件或卡片回调订阅；
- 允许审批的飞书用户标识；
- 回调校验或长连接配置。

当前接口支持：

- 地址校验；
- 标准化卡片操作；
- 审批用户白名单；
- 事件幂等；
- 选题与发布审批领域处理。

真实飞书应用的加密、签名、长连接、消息发送和卡片更新适配器将在下一阶段实现。

## 8. 安全默认值

- 真实发布关闭；
- 评论执行关闭；
- 生产环境必须配置内部接口令牌；
- 生产环境必须配置飞书回调校验令牌；
- 飞书审批用户白名单为空时，拒绝所有审批；
- 发布审批必须绑定内容版本和排期；
- 外部动作结果未知时不能再次执行；
- 日志和数据库不得保存明文密钥。
- 研究接口不返回来源错误详情，避免把凭证、会话或原始页面内容传播到审阅端。

## 9. 下一阶段

下一阶段应按顺序实现：

1. 飞书企业自建应用适配器；
2. Agent Reach、GitHub 与隔离小红书 Profile 的只读研究连接器；
3. 研究确认、选题版本与审批卡片；
4. 大模型结构化输出网关；
5. 内容版本和自动审核；
6. 完成发布前演练后，再实现小红书发布执行器。
