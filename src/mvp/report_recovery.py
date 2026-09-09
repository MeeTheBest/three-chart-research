"""Deterministic, non-model recovery of safe portions of interrupted reports."""
from copy import deepcopy
import json
import re
from jsonschema import Draft202012Validator


def preflight_metadata(research, metadata):
    schema = json.loads(research.SCHEMA_PATH.read_text(encoding="utf-8"))
    schema["required"] = [key for key in schema["required"] if key != "claims"]
    errors = list(Draft202012Validator(schema).iter_errors(metadata))
    if errors:
        raise research.ApiError(500, "report_metadata_invalid", "报告准备遇到问题，尚未开始模型分析，请联系管理员。")


def friendly_failure(failure):
    code = failure.get("code", "")
    if code in {"analysis_contract_invalid", "report_metadata_invalid"}:
        if any(item.get("path") == "/caseId" for item in (failure.get("details") or []) if isinstance(item, dict)):
            return "报告编号处理出错，完整报告暂未生成；已经完成的内容已保留。"
        return "部分内容未通过完整性或可靠性检查，完整报告暂未生成；可展示的内容已保留。"
    if code in {"model_not_configured", "model_access_denied", "model_authentication_failed"}:
        return "模型服务暂时无法使用，分析未能完成；已有内容已保留，请联系管理员。"
    return "分析途中遇到服务或内容处理问题，未能全部完成；已有内容已保留，可稍后继续。"


def recover_report(research, session_id, system):
    store = research.STATE.store
    checkpoint = store.get_checkpoint(session_id, system)
    diagnostic = store.get_diagnostic(session_id, system)
    result = {"status": "incomplete", "message": friendly_failure(diagnostic.get("failure") or {}),
              "analysis": {"analysisType": system, "claims": []}, "completedStages": [], "hiddenClaimCount": 0}
    drafts = checkpoint.get("professionalDrafts", [])
    if system in research.SYSTEMS:
        result["completedStages"] = [stage["title"] for stage in research.PROFESSIONAL_AUDIT_STAGES[system][:len(drafts)]]
    try:
        case_id = research.STATE.case_id(session_id)
        draft = {"claims": [claim for group in checkpoint.get("groups", {}).values() for claim in group]}
        if not draft["claims"]:
            return result
        if system == "integration":
            raw_documents = store.get_raw_documents(session_id)
            singles = store.get_single_analyses(session_id)
            analysis = research.integration_analysis_from_draft(research.integration_fixed_metadata(case_id, raw_documents, singles), singles, draft)
        else:
            raw = checkpoint.get("raw")
            if not raw:
                return result
            document_id = f"{case_id}.{system}.raw.v1"
            document = {"documentId": document_id, **raw}
            raw_documents = {document_id: document}
            audits = research.professional_audit_from_drafts(system, document, drafts)
            analysis = research.single_analysis_from_draft(system, research.single_fixed_metadata(system, case_id, document_id, store.get_birth_input(session_id)), document, draft, audits)
        analysis = deepcopy(analysis)
        # Validate against the original audit chain; only omit its prose after
        # validation, when building the presentation-only partial envelope.
        initial_count = len(analysis["claims"])
        while analysis["claims"]:
            issues = research.validate_analysis(analysis, raw_documents)
            if not issues:
                analysis.pop("professionalAudit", None)
                result["analysis"] = analysis
                break
            indices = set()
            for issue in issues:
                match = re.match(r"^/claims/(\d+)(?:/|$)", issue.path)
                if not match:
                    # An envelope/reference-set error cannot safely be isolated.
                    indices = set(range(len(analysis["claims"])))
                    break
                indices.add(int(match[1]))
            remaining = [claim for i, claim in enumerate(analysis["claims"]) if i not in indices]
            if len(remaining) == len(analysis["claims"]):
                break
            analysis["claims"] = remaining
        result["hiddenClaimCount"] = initial_count - len(result["analysis"]["claims"])
    except Exception:
        # Never replace the original diagnostic, or expose unvalidated drafts.
        result["message"] = "报告暂未整理完成。已完成阶段和原始记录已保留，但目前没有通过检查的结论可展示。"
    return result
