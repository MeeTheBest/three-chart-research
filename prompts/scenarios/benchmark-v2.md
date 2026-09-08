# 大赛盲测场景

- 设置 `scenario=benchmark`、`answerKeyAccess=false`、`knownOutcomeAccess=false`、`networkAccess=false`。
- 不得联网、检索命例、读取答案表、利用已知现实结果或声称记得公开答案。
- 题目和选项只能用于定义问题及最后映射，不能作为命盘证据。
- 每个题目在根级建立一个 `questionDecisions` 项；先根据其 `supportingClaimIds` 所指向的原子 Claim，写不依赖选项的 `openEndedAssessment`，再读取选项填写 `selectedOption` 和 `mappingRationale`。
- `questionDecisions` 是题目级映射层，不得把复合选项反写进原子 Claim；同一题的学历、性格、职业、资产等不同事实分别保留为独立 Claim。
- 无法形成足够区分度时必须选择 `ABSTAIN`，不得为了提高完成率猜测。
- 本场景不生成行动建议：`actionAdvice.status=not-applicable`、`items=[]`。
- 公开大赛题统一使用 P3 和 `empiricalUse=engineering-only`。
