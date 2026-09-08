# 三术研究提示词 v2

模型只负责生成符合 `research.analysis.v1` 的 JSON；中文 Markdown 报告由确定性渲染器生成。

## 组装顺序

单术分析：

1. `research-core-v2.md`
2. `single-system-v2.md`
3. `scenarios/benchmark-v2.md` 或 `scenarios/website-v2.md`
4. 当前体系 Raw Data、问题和允许的场景输入

三术整合：

1. `research-core-v2.md`
2. `integration-v2.md`
3. 对应场景提示词
4. 三份 Raw Data、三份已冻结单术分析、问题和允许的场景输入

调用时必须使用 `schemas/research-analysis.v1.schema.json` 作为严格结构化输出约束，固定模型版本、`temperature=0`，并在模型支持时记录 seed。大赛模式不得把答案文件、现实结果或网络检索工具放入模型上下文。

## 版本边界

- `validationVersion.phase=pre-validation`：不得读取现实反馈，原始结论不可覆盖。
- 收到现实反馈后：新建 `post-feedback` 分析，填写父分析 ID 和反馈记录 ID。
- P3 结果只允许用于工程流程和相对表现测试。
