# 单术独立分析协议

本次只分析指定的一个体系。你不得读取或推测另外两个体系的结论。

- 只引用当前体系 Raw Data。
- 当前体系 `status=analyzed` 时必须给出唯一 `analysisId`、`frozen=true`、Raw 引用、Theory、Inference 和 Uncertainty。
- 另外两个体系设置 `status=not-run`、`analysisId=null`、`frozen=false`，不得包含证据或推演。
- 单术输出完成后即冻结；后续整合不得改写。
- 因只有一个体系，整体证据等级最高为 C；证据不足则为 D 和“无法判断”。
- `consistency.status=insufficient`，不得假装已经完成三术一致性比较。
