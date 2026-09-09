import unittest
from unittest.mock import patch
from src.mvp import local_server as research
from src.mvp.report_recovery import preflight_metadata, recover_report, friendly_failure
from src.mvp.session_store import BirthInput, EphemeralSessionStore
from src.research.validator import validate_analysis
from test.research.fixtures import integration_analysis, raw_bundle


class RecoveryTest(unittest.TestCase):
    def setUp(self):
        self.store = EphemeralSessionStore()
        self.birth = BirthInput("2000-01-15", "08:00", "北京市", "女")
        self.sid = self.store.create(self.birth)
        self.patch = patch.object(research.STATE, "store", self.store)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()

    def test_generated_identifier_passes_before_model_call(self):
        case = research.STATE.case_id(self.sid)
        preflight_metadata(research, research.single_fixed_metadata("bazi", case, case + ".bazi.raw.v1", self.birth))
        with self.assertRaises(research.ApiError):
            preflight_metadata(research, research.single_fixed_metadata("bazi", "invalid id", "raw", self.birth))

    def test_real_website_id_and_benchmark_id_validate(self):
        for case in ("C01", "C8de1ef3646047aae"):
            report = integration_analysis()
            report["caseId"] = case
            self.assertEqual(validate_analysis(report, raw_bundle()), [])

    def test_empty_failure_is_saved_but_does_not_unlock_comparison(self):
        self.store.set_diagnostic(self.sid, "bazi", {"status": "failed", "failure": {"code": "analysis_contract_invalid", "details": [{"path": "/caseId"}]}})
        result = recover_report(research, self.sid, "bazi")
        self.assertEqual(result["analysis"]["claims"], [])
        self.assertIn("编号", result["message"])
        self.store.set_checkpoint(self.sid, "bazi", {"partialReport": result})
        self.assertEqual(self.store.get_checkpoint(self.sid, "bazi")["partialReport"], result)
        self.assertEqual(self.store.comparison_status(self.sid)["status"], "locked")
        self.assertEqual(self.store.session_summary(self.sid)["incompleteSystems"], ["bazi"])

    def test_bad_raw_reference_is_not_displayed(self):
        report = integration_analysis()
        report["claims"][0]["rawDataRefs"][0]["pointer"] = "/not-present"
        self.store.set_checkpoint(self.sid, "integration", {"groups": {"a": [{}]}})
        with patch.object(research, "integration_analysis_from_draft", return_value=report), patch.object(research, "integration_fixed_metadata", return_value={}), patch.object(self.store, "get_raw_documents", return_value=raw_bundle()):
            result = recover_report(research, self.sid, "integration")
        self.assertEqual(result["analysis"]["claims"], [])
        self.assertGreater(result["hiddenClaimCount"], 0)

    def test_safe_report_can_be_shown_without_marking_success(self):
        report = integration_analysis()
        self.store.set_checkpoint(self.sid, "integration", {"groups": {"a": [{}]}})
        with patch.object(research, "integration_analysis_from_draft", return_value=report), patch.object(research, "integration_fixed_metadata", return_value={}), patch.object(self.store, "get_raw_documents", return_value=raw_bundle()):
            result = recover_report(research, self.sid, "integration")
        self.assertTrue(result["analysis"]["claims"])
        self.assertEqual(result["status"], "incomplete")
        self.assertIsNone(self.store.get_integration_analysis(self.sid))

    def test_internal_details_not_exposed_in_notice(self):
        self.assertNotIn("secret", friendly_failure({"message": "secret", "details": "secret"}))

    def test_single_text_list_is_preserved_not_invented(self):
        self.assertEqual(research._draft_list("原始反证条件", ["默认值"]), ["原始反证条件"])

    def test_single_recovery_validates_with_full_audit_before_hiding_it(self):
        drafts = [{"theory": [{"ruleId": "test", "statement": "测试规则"}],
                   "observations": ["观察"], "inference": ["推演"],
                   "counterEvidence": ["相反信号"], "uncertainty": ["待核验"], "status": "completed"}
                  for _ in research.PROFESSIONAL_AUDIT_STAGES["bazi"]]
        claims = [{"claimKey": spec["claimKey"], "claim": "有条件倾向", "direction": "待验证",
                   "sourceAuditIds": ["bazi-audit-pillars"], "inference": ["条件判断"],
                   "uncertainty": ["待核验"], "validation": ["观察记录"],
                   "falsificationConditions": ["相反事实"], "provisionalConclusion": "有条件倾向", "actionAdvice": []}
                  for spec in research.CLAIM_SPECS]
        self.store.set_checkpoint(self.sid, "bazi", {"raw": {"test": True}, "professionalDrafts": drafts, "groups": {"all": claims}})
        result = recover_report(research, self.sid, "bazi")
        self.assertTrue(result["analysis"]["claims"])
        self.assertNotIn("professionalAudit", result["analysis"])
        self.assertEqual(len(result["completedStages"]), 7)


if __name__ == "__main__":
    unittest.main()
