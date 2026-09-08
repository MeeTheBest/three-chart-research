# 网站研究报告场景

- 设置 `scenario=website`，`questionDecisions=[]`。
- 模型只输出 JSON；中文 Markdown 由确定性程序渲染。
- 单术入口的行动建议放入独立 `actionAdvice`，不得混入 Raw Data、Theory 或盘内证据等级。
- 综合入口只展示一致、部分一致、冲突、不确定性和各术证据等级；不输出统一结论或行动建议，`actionAdvice.status=not-applicable`。
- 建议必须说明依据是暂定结论还是一般安全原则，不得把命理推演表现为事实诊断。
- 健康、疾病、手术、死亡和寿命主题必须提供免责声明，所有建议设置 `nonDiagnostic=true`，不得使用确定患病、确定死亡或替代专业判断的措辞。
- 证据不足时仍应明确“无法判断”；可提供一般安全建议，但必须标记 `basis=general-safety`。
