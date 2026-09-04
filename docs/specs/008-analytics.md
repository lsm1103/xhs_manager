# 规格八：数据统计与内容实验

## 1. 目标

采集可获得的平台指标，将每篇内容与发布前实验假设关联，形成单篇和周期复盘。

覆盖需求：

- `REQ-ANA-001` 至 `REQ-ANA-010`

## 2. 实验定义

每篇内容在发布审批前必须存在实验：

```json
{
  "experiment_id": "唯一标识",
  "task_id": "任务标识",
  "hypothesis": "真实工作流内容比纯工具介绍产生更多有效问题",
  "content_pillar": "workflow_experiment",
  "target_user_stage": "used_chat_ai",
  "primary_metric": "effective_questions",
  "secondary_metrics": ["saves", "comments", "follows"],
  "controlled_variables": {
    "title_structure": "experiment_result",
    "cover_structure": "problem_result",
    "body_structure": "scenario_process_limit",
    "interaction_prompt": "specific_scenario_question"
  },
  "observation_windows": ["2h", "24h", "72h", "7d"],
  "status": "active"
}
```

实验不要求统计显著性，但必须明确哪些结论证据不足。

## 3. 指标快照

```json
{
  "metric_snapshot_id": "唯一标识",
  "publication_id": "发布记录",
  "observed_at": "时间",
  "window": "24h",
  "source": "xiaohongshu_page",
  "metrics": {
    "impressions": null,
    "likes": 10,
    "saves": 8,
    "comments": 3,
    "follows": null
  },
  "unavailable_metrics": ["impressions", "follows"],
  "evidence_ref": "快照指针"
}
```

平台不可获得的指标保存为空并说明原因，不得用模型估算成原始值。

## 4. 计算指标

只有分母存在且有效时才计算比率：

- 点赞率；
- 收藏率；
- 评论率；
- 关注转化趋势；
- 有效问题率；
- 深度互动率。

计算结果必须保存：

- 公式版本；
- 输入快照；
- 计算时间；
- 缺失值处理；
- 是否可比较。

## 5. 采集窗口

默认：

- 发布后二小时；
- 二十四小时；
- 七十二小时；
- 七天。

若任务延迟超过窗口：

- 仍采集当前值；
- 保存实际延迟；
- 标记窗口不精确；
- 周对比时降低可比性，不回填伪造历史值。

## 6. 单篇复盘

输入：

- 实验；
- 内容版本；
- 指标快照；
- 评论分类；
- 用户问题候选；
- 同期账号基线；
- 运行异常。

输出：

```json
{
  "conclusion": "insufficient_evidence",
  "summary": "收藏信号较高，但样本不足以判断结构优劣",
  "supporting_evidence": [],
  "counter_evidence": [],
  "confounders": [],
  "reusable_points": [],
  "next_experiment": {
    "keep": [],
    "change_one": "封面结构",
    "avoid": []
  }
}
```

结论枚举：

- `supported`
- `not_supported`
- `insufficient_evidence`

## 7. 周复盘

周复盘至少包含：

- 计划与实际发布；
- 各内容支柱数量；
- 数据可用性；
- 表现最高和最低内容；
- 用户问题簇；
- 标题、封面和正文结构比较；
- 自动化失败；
- 可复用规则候选；
- 下周实验建议。

新账号前四周：

- 主要建立基线；
- 不自动修改策略；
- 不根据单篇爆发定义稳定规律；
- 复用建议标记为实验中。

## 8. 分析技能

技能名称：`performance_review`

禁止：

- 把相关性写成因果；
- 忽略发布时间等混杂因素；
- 对缺失数据进行确定性推断；
- 使用单篇结果改变长期定位；
- 把点赞等同于需求；
- 把私信数量等同于有效线索。

## 9. 基线

基线按以下范围保存：

- 全账号；
- 内容支柱；
- 用户阶段；
- 发布星期和时段；
- 标题结构；
- 封面结构。

样本数低于配置门槛时只展示原始分布，不输出稳定优劣排序。

## 10. 错误代码

| 错误码 | 处理 |
|---|---|
| `METRIC_SOURCE_UNAVAILABLE` | 延后一次并标记缺失 |
| `METRIC_WINDOW_MISSED` | 保存实际时间 |
| `METRIC_PARSE_FAILED` | 保存证据并人工检查 |
| `EXPERIMENT_MISSING` | 阻止正式复盘 |
| `INSUFFICIENT_SAMPLE` | 输出证据不足 |
| `REVIEW_OUTPUT_INVALID` | 修复一次 |

## 11. 验收条件

- `AC-008-01` 每篇发布内容关联一个实验。
- `AC-008-02` 指标快照保存采集时间和来源。
- `AC-008-03` 不可获得指标不会被估算成真实值。
- `AC-008-04` 比率只在有效分母存在时计算。
- `AC-008-05` 单篇复盘只使用三种允许结论。
- `AC-008-06` 复盘明确列出混杂因素。
- `AC-008-07` 单篇表现不会自动修改账号策略。
- `AC-008-08` 周复盘能够形成待确认知识候选。

## 12. 测试重点

- 全部指标缺失；
- 窗口延迟；
- 平台计数回退或修正；
- 相同快照重复采集；
- 零分母；
- 时区和夏令时；
- 单篇异常高值；
- 评论数据晚于指标窗口。

