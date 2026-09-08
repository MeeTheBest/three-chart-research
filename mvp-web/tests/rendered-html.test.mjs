import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

test("exports the astrology research workspace as static HTML", async () => {
  const html = await readFile(new URL("../dist/client/index.html", import.meta.url), "utf8");
  assert.match(html, /<title>三术命盘研究台<\/title>/i);
  assert.match(html, /紫微斗数/);
  assert.match(html, /子平八字/);
  assert.match(html, /吠陀占星/);
  assert.match(html, /三术比较/);
  assert.doesNotMatch(html, /name="(?:timezone|latitude|longitude)"/i);
});

test("keeps staged progress and comparison-only safeguards in the client", async () => {
  const page = await readFile(new URL("../app/page.tsx", import.meta.url), "utf8");
  assert.match(page, /比较层，不是第四套命理/);
  assert.match(page, /不输出统一人生结论/);
  assert.doesNotMatch(page, /<b>Raw Data<\/b>/);
  assert.doesNotMatch(page, /<b>Theory<\/b>/);
  assert.doesNotMatch(page, /title="现实验证"/);
  assert.doesNotMatch(page, /title="反证条件"/);
  assert.doesNotMatch(page, /className="professional-audit" open/);
  assert.match(page, /className="stage-timeline"/);
  assert.match(page, /const claimGroups = \[/);
  assert.match(page, /"health", "健康提醒"/);
  assert.match(page, /查看失败报告/);
  assert.match(page, /\/diagnostic/);
  assert.match(page, /有条件倾向/);
  assert.match(page, /线索较明确/);
  assert.match(page, /存在相反信号/);
  assert.match(page, /对时间敏感/);
  assert.match(page, /三术一致/);
  assert.match(page, /部分一致/);
  assert.match(page, /module-incomplete/);
  assert.match(page, /暂不下结论/);
  assert.match(page, /结论生成待补全/);
  assert.match(page, /查看推演依据/);
  assert.doesNotMatch(page, /<List title="Inference"/);
  assert.doesNotMatch(page, /\{claim\.evidenceGrade\} · \{claim\.pollutionRisk\}/);
  assert.match(page, /历史记录/);
  assert.match(page, /astrology-research-history-v1/);
  assert.doesNotMatch(page, /pagehide/);
  assert.doesNotMatch(page, /waiting-context/);
  assert.match(page, /Math\.min\(94,/);
});
