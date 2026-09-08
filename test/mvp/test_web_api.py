"""Deployment plumbing tests; model calls are mocked, not accuracy evidence."""
import os
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from src.mvp.persistence import SnapshotRepository
from src.mvp.session_store import BirthInput, EphemeralSessionStore, SessionNotFound
from src.mvp import web_api
from src.mvp import local_server as research


def fixture_report(system):
    return {"analysisId": "synthetic-test-" + system, "analysisType": system,
            "run": {"answerKeyAccess": False, "knownOutcomeAccess": False},
            "validationVersion": {"phase": "pre-validation"}, "claims": []}


class PublicApiTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.url = "sqlite:///" + str(Path(self.temp.name) / "sessions.sqlite3")
        self.env = patch.dict(os.environ, {"DATABASE_URL": self.url})
        self.env.start()
        web_api._rates.clear()
        self.client = TestClient(web_api.app)
        self.client.__enter__()
        self.birth = BirthInput("2000-01-15", "08:00", "北京市", "女", "Asia/Shanghai", 39.9, 116.4)
        self.session = research.STATE.store.create(self.birth)

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.env.stop()
        self.temp.cleanup()

    def test_health_and_unknown_routes(self):
        self.assertEqual(self.client.get("/api/health").status_code, 200)
        self.assertEqual(self.client.get("/api/not-real").status_code, 404)
        self.assertEqual(self.client.get("/skill-packs/v1/vedic-astrology/SKILL.md").status_code, 404)

    def test_history_survives_store_restart_and_has_no_birth_details(self):
        store = research.STATE.store
        for system in research.SYSTEMS:
            store.record_single_analysis(self.session, system, fixture_report(system))
        store.set_checkpoint(self.session, "vedic", {"professionalDrafts": [{"text": "完整冻结阶段"}]})
        store.set_diagnostic(self.session, "vedic", {"events": [{"rawReply": "完整模型原文"}]})
        research.STATE.store = EphemeralSessionStore(repository=SnapshotRepository(self.url))
        self.assertEqual(research.STATE.store.get_checkpoint(self.session, "vedic")["professionalDrafts"][0]["text"], "完整冻结阶段")
        response = self.client.get(f"/api/sessions/{self.session}")
        self.assertEqual(len(response.json()["completedSystems"]), 3)
        self.assertNotIn("birth", response.json())
        self.assertIn("完整模型原文", self.client.get(f"/api/sessions/{self.session}/analyses/vedic/diagnostic").text)

    def test_deleted_session_cannot_return_after_restart(self):
        self.assertEqual(self.client.delete(f"/api/sessions/{self.session}").status_code, 200)
        research.STATE.store = EphemeralSessionStore(repository=SnapshotRepository(self.url))
        self.assertEqual(self.client.get(f"/api/sessions/{self.session}").status_code, 404)

    def test_expired_database_snapshot_is_inaccessible(self):
        earlier = datetime.now(UTC) - timedelta(days=2)
        identifier = research.STATE.store.create(self.birth, now=earlier)
        research.STATE.store.record_single_analysis(identifier, "ziwei", fixture_report("ziwei"), now=earlier)
        research.STATE.store = EphemeralSessionStore(repository=SnapshotRepository(self.url))
        with self.assertRaises(SessionNotFound):
            research.STATE.store.get_single_analyses(identifier)

    def test_comparison_lock_and_duplicate_enqueue(self):
        stopped = web_api.Jobs()  # deliberately do not start the fixture worker
        with patch.object(web_api, "jobs", stopped), patch.object(research, "qwen_api_key", return_value="synthetic-not-a-real-key"):
            self.assertEqual(self.client.post(f"/api/sessions/{self.session}/comparison").status_code, 409)
            path = f"/api/sessions/{self.session}/analyses/ziwei"
            self.assertEqual(self.client.post(path).status_code, 202)
            self.assertEqual(self.client.post(path).status_code, 202)
            self.assertEqual(stopped.queue.qsize(), 1)
            self.assertEqual(self.client.get(path + "/progress").json()["state"], "queued")

    def test_frozen_report_does_not_call_model_again(self):
        research.STATE.store.record_single_analysis(self.session, "bazi", fixture_report("bazi"))
        with patch.object(research, "qwen_api_key", side_effect=AssertionError("must not need a key")):
            response = self.client.post(f"/api/sessions/{self.session}/analyses/bazi")
        self.assertEqual(response.json()["state"], "completed")

    def test_missing_key_has_actionable_error(self):
        with patch.object(research, "qwen_api_key", return_value=""):
            response = self.client.post(f"/api/sessions/{self.session}/analyses/vedic")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["error"]["code"], "model_not_configured")


if __name__ == "__main__":
    unittest.main()
