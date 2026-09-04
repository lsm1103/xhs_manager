# 规格三：选题引擎

## 1. 目标

把研究信号和用户问题转换为可解释、可审批、可实验的选题候选。

覆盖需求：

- `REQ-TOP-001` 至 `REQ-TOP-008`
- `REQ-INS-005`

## 2. 输入

```json
{
  "account_id": "账号标识",
  "strategy_version_id": "策略版本",
  "evidence_package_ids": ["证据包"],
  "recent_topic_ids": ["近期选题"],
  "problem_cluster_ids": ["问题簇"],
  "target_count": 5,
  "reserve_count": 2
}
```

## 3. 选题候选结构

```json
{
  "topic_proposal_id": "唯一标识",
  "version": 1,
  "user_problem": "用户具体问题",
  "working_title": "内部工作标题",
  "core_claim": "文章核心结论",
  "why_now": "现在值得做的原因",
  "content_pillar": "workflow_experiment",
  "primary_goal": "problem_discovery",
  "target_stage": "used_chat_ai",
  "evidence_package_ids": ["证据包"],
  "counterpoints": ["反例或限制"],
  "experiment_hypothesis": "可验证假设",
  "primary_metric": "effective_questions",
  "recommended_format": "carousel",
  "scores": {
    "audience_relevance": 85,
    "freshness": 70,
    "evidence_strength": 90,
    "spread_potential": 65,
    "problem_discovery_value": 88,
    "distinctiveness": 75,
    "production_cost": 40,
    "risk": 20
  },
  "total_score": 79,
  "score_explanation": "评分解释",
  "risks": [],
  "status": "pending_approval"
}
```

## 4. 内容支柱

允许值：

- `concept_explainer`
- `workflow_experiment`
- `solution_comparison`
- `trend_response`
- `replication_experiment`

首月默认配额：

- 概念拆解五篇；
- 真实工作流实验八篇；
- 工具与方案判断四篇；
- 热点响应一篇；
- 复用实验两篇。

配额用于组合建议，不作为每周硬阻断。

## 5. 评分模型

总分范围零至一百。推荐权重：

| 维度 | 权重 |
|---|---:|
| 目标用户相关性 | 20 |
| 问题发现价值 | 15 |
| 证据充分度 | 15 |
| 传播潜力 | 15 |
| 差异化 | 10 |
| 新鲜度 | 10 |
| 制作成本 | 5 |
| 风险 | 10 |

成本和风险使用反向得分。

规则：

- 证据充分度低于五十分时不能成为主选题；
- 风险原始值高于七十分时必须人工补充说明；
- 与近三十天已发布内容高度重复时，差异化最高不超过四十分；
- 来自有效问题簇的选题提高问题发现价值，但不能绕过证据门槛；
- 分数只用于排序，不自动批准。

## 6. 去重

比较范围：

- 待审批选题；
- 已批准选题；
- 制作中任务；
- 近九十天已发布内容；
- 已终止但原因是重复的选题。

相似度分为：

- 用户问题相似度；
- 核心结论相似度；
- 标题角度相似度；
- 证据包重叠度。

如果用户问题相同但结论或实验变量不同，可以保留，但必须明确差异。

## 7. 生成流程

```text
读取策略和近期内容
→ 读取证据包和用户问题簇
→ 生成候选角度
→ 确定性校验字段
→ 计算规则分
→ 模型提供解释分
→ 合并评分
→ 去重
→ 组合主选题和替补
→ 创建不可变选题版本
→ 发送审批
```

## 8. 选题版本

以下变化必须创建新版本：

- 用户问题；
- 核心结论；
- 内容支柱；
- 证据包；
- 实验假设；
- 首要指标；
- 风险结论。

只修改内部备注可以不创建新业务版本，但必须记录审计。

## 9. 审批前校验

候选必须满足：

- 用户问题具体；
- 核心结论单一；
- 至少一个可用证据包；
- 包含限制条件；
- 指定内容支柱；
- 指定实验假设；
- 指定首要指标；
- 不与近期内容无解释重复；
- 风险有明确说明。

## 10. 模型技能协议

技能名称：`topic_planning`

输出必须严格符合选题候选结构。模型不得：

- 自行批准；
- 修改评分权重；
- 虚构热度；
- 使用没有进入证据包的事实；
- 隐藏与账号定位不一致；
- 把产品宣传写成用户问题。

## 11. 错误代码

| 错误码 | 处理 |
|---|---|
| `NO_USABLE_EVIDENCE` | 返回研究补充 |
| `TOPIC_DUPLICATE` | 标记重复并给出相似对象 |
| `TOPIC_SCHEMA_INVALID` | 修复一次 |
| `TOPIC_RISK_TOO_HIGH` | 仅允许人工审查，不进入主选 |
| `STRATEGY_VERSION_INACTIVE` | 使用最新有效策略重新生成 |
| `TOPIC_VERSION_STALE` | 拒绝旧版本操作 |

## 12. 验收条件

- `AC-003-01` 每个候选包含用户问题、证据、实验和风险。
- `AC-003-02` 所有分数都有分项与解释。
- `AC-003-03` 证据不足候选不能成为主选题。
- `AC-003-04` 与近期内容重复时给出明确提示。
- `AC-003-05` 同问题不同实验角度可以保留并说明差异。
- `AC-003-06` 补充研究会创建新版本，旧审批不能作用于新版本。
- `AC-003-07` 未批准候选不能创建正式制作工作项。
- `AC-003-08` 系统能够输出五个主选和最多两个替补。

## 13. 测试重点

- 权重计算；
- 风险和证据硬门槛；
- 多维重复判断；
- 相同用户问题不同结论；
- 策略版本变化；
- 模型缺失字段；
- 评分解释与计算结果冲突；
- 替补选题晋升。

