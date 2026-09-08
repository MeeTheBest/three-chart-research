from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator


DEFAULT_SCHEMA = Path(__file__).resolve().parents[2] / "schemas" / "research-analysis.v1.schema.json"
SYSTEMS = ("ziwei", "bazi", "vedic")
GRADE_SCORE = {"D": 0, "C": 1, "B": 2, "A": 3, "S": 4}
HIGH_STAKES_TERMS = ("健康", "疾病", "手术", "死亡", "寿命", "癌", "health", "disease", "death", "surgery")
DETERMINISTIC_HIGH_STAKES_PHRASES = (
    "必然患",
    "一定患",
    "确定患",
    "必死",
    "一定会死",
    "将死亡",
    "诊断为",
    "will certainly develop",
    "will die",
    "diagnosed with",
)
P3_PROOF_PHRASES = ("证明命理有效", "证实命理有效", "证明命盘正确", "proves astrology")


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    path: str
    message: str


def _json_pointer(document: Any, pointer: str) -> Any:
    if pointer == "":
        return document
    current = document
    for raw_token in pointer.lstrip("/").split("/"):
        token = raw_token.replace("~1", "/").replace("~0", "~")
        if isinstance(current, list):
            try:
                current = current[int(token)]
            except (ValueError, IndexError) as exc:
                raise KeyError(pointer) from exc
        elif isinstance(current, dict) and token in current:
            current = current[token]
        else:
            raise KeyError(pointer)
    return current


def _all_refs(claim: dict[str, Any]) -> list[dict[str, Any]]:
    refs = list(claim.get("rawDataRefs", []))
    for system in SYSTEMS:
        refs.extend(claim.get("systemAnalyses", {}).get(system, {}).get("rawDataRefs", []))
    for item in claim.get("counterEvidence", {}).get("items", []):
        refs.extend(item.get("rawDataRefs", []))
    return refs


def _add(issues: list[ValidationIssue], code: str, path: str, message: str) -> None:
    issues.append(ValidationIssue(code, path, message))


def _claim_text(claim: dict[str, Any]) -> str:
    parts = [
        claim.get("claim", ""),
        claim.get("provisionalConclusion", {}).get("text", ""),
        claim.get("gradeRationale", {}).get("explanation", ""),
        claim.get("pollutionRationale", ""),
    ]
    parts.extend(item.get("text", "") for item in claim.get("actionAdvice", {}).get("items", []))
    return " ".join(parts).lower()


def validate_analysis(
    analysis: dict[str, Any],
    raw_documents: dict[str, Any],
    *,
    schema_path: Path = DEFAULT_SCHEMA,
) -> list[ValidationIssue]:
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    issues: list[ValidationIssue] = []
    validator = Draft202012Validator(schema)
    for error in sorted(validator.iter_errors(analysis), key=lambda item: list(item.absolute_path)):
        path = "/" + "/".join(str(item) for item in error.absolute_path)
        issues.append(ValidationIssue("SCHEMA", path, error.message))
    if issues:
        return issues

    claims = analysis["claims"]
    claim_ids = [claim["claimId"] for claim in claims]
    if len(set(claim_ids)) != len(claim_ids):
        issues.append(ValidationIssue("DUPLICATE_CLAIM_ID", "/claims", "claimId values must be unique"))
    claim_by_id = {claim["claimId"]: claim for claim in claims}

    declared_documents = set(analysis["run"]["rawDataDocumentIds"])
    supplied_documents = set(raw_documents)
    audited_documents = set(analysis["inputAudit"]["rawDataDocumentIdsVerified"])
    if audited_documents != declared_documents:
        _add(
            issues,
            "INPUT_AUDIT_DOCUMENT_MISMATCH",
            "/inputAudit/rawDataDocumentIdsVerified",
            "verified document ids must exactly match run.rawDataDocumentIds",
        )
    for missing in sorted(declared_documents - supplied_documents):
        _add(issues, "MISSING_RAW_DOCUMENT", "/run/rawDataDocumentIds", missing)

    run = analysis["run"]
    version = analysis["validationVersion"]
    if analysis["scenario"] == "benchmark":
        if run["networkAccess"] or run["knownOutcomeAccess"]:
            _add(
                issues,
                "BENCHMARK_EVIDENCE_LEAK",
                "/run",
                "benchmark runs require networkAccess=false and knownOutcomeAccess=false",
            )
    if version["phase"] == "pre-validation":
        if version["parentAnalysisId"] is not None or version["feedbackRecordIds"]:
            _add(
                issues,
                "PREVALIDATION_HAS_FEEDBACK",
                "/validationVersion",
                "pre-validation output cannot have a parent analysis or feedback records",
            )
        if run["knownOutcomeAccess"]:
            _add(issues, "PREVALIDATION_KNOWN_OUTCOME", "/run/knownOutcomeAccess", "must be false")
    else:
        if version["parentAnalysisId"] is None or not version["feedbackRecordIds"]:
            _add(
                issues,
                "POSTFEEDBACK_LINEAGE_MISSING",
                "/validationVersion",
                "post-feedback output requires parentAnalysisId and feedbackRecordIds",
            )
        if not run["knownOutcomeAccess"]:
            _add(issues, "POSTFEEDBACK_OUTCOME_FLAG_MISSING", "/run/knownOutcomeAccess", "must be true")

    frozen_inputs = analysis["inputAudit"]["frozenSingleSystemAnalyses"]
    availability = analysis["inputAudit"]["systemAvailability"]
    if analysis["analysisType"] in SYSTEMS:
        expected_stages = {
            "ziwei": {
                "input", "palaces-core", "palaces-relations", "palaces-inner",
                "stars", "themes", "timing",
            },
            "bazi": {"input", "pillars", "strength", "interactions", "blind", "health", "timing"},
            "vedic": {
                "input", "identity", "planets-luminaries", "planets-benefics", "planets-nodes-yoga",
                "divisional", "houses-1-4", "houses-5-8", "houses-9-12", "life", "appendix",
            },
        }[analysis["analysisType"]]
        actual_stages = {item.get("stage") for item in analysis.get("professionalAudit", [])}
        if actual_stages != expected_stages:
            _add(
                issues,
                "PROFESSIONAL_AUDIT_INCOMPLETE",
                "/professionalAudit",
                f"{analysis['analysisType']} requires frozen stages: {sorted(expected_stages)}",
            )
    if analysis["analysisType"] == "integration":
        for system in SYSTEMS:
            if availability[system] == "analyzed" and not frozen_inputs[system]:
                _add(
                    issues,
                    "UNFROZEN_INTEGRATION_INPUT",
                    f"/inputAudit/frozenSingleSystemAnalyses/{system}",
                    "an analyzed system requires a frozen single-system analysis id",
                )

    for audit_index, audit in enumerate(analysis.get("professionalAudit", [])):
        for ref_index, ref in enumerate(audit["rawDataRefs"]):
            ref_path = f"/professionalAudit/{audit_index}/rawDataRefs/{ref_index}"
            document_id = ref["documentId"]
            if document_id not in declared_documents:
                _add(issues, "UNDECLARED_RAW_DOCUMENT", ref_path, f"{document_id} is not listed in run.rawDataDocumentIds")
                continue
            if document_id not in raw_documents:
                continue
            try:
                _json_pointer(raw_documents[document_id], ref["pointer"])
            except KeyError:
                _add(issues, "UNRESOLVED_RAW_POINTER", ref_path, f"{document_id}#{ref['pointer']} does not exist")

    for claim_index, claim in enumerate(claims):
        base = f"/claims/{claim_index}"
        for ref_index, ref in enumerate(_all_refs(claim)):
            ref_path = f"{base}/rawDataRef/{ref_index}"
            document_id = ref["documentId"]
            if document_id not in declared_documents:
                _add(
                    issues,
                    "UNDECLARED_RAW_DOCUMENT",
                    ref_path,
                    f"{document_id} is not listed in run.rawDataDocumentIds",
                )
                continue
            if document_id not in raw_documents:
                continue
            try:
                _json_pointer(raw_documents[document_id], ref["pointer"])
            except KeyError:
                _add(
                    issues,
                    "UNRESOLVED_RAW_POINTER",
                    ref_path,
                    f"{document_id}#{ref['pointer']} does not exist",
                )

        for system in SYSTEMS:
            system_analysis = claim["systemAnalyses"][system]
            system_path = f"{base}/systemAnalyses/{system}"
            refs = system_analysis["rawDataRefs"]
            theory = system_analysis["theory"]
            inference = system_analysis["inference"]
            if system_analysis["status"] == "analyzed" and (not refs or not theory or not inference):
                _add(
                    issues,
                    "ANALYZED_SYSTEM_MISSING_EVIDENCE",
                    system_path,
                    "analyzed systems require rawDataRefs, theory, and inference",
                )
            if system_analysis["status"] == "analyzed" and (
                not system_analysis["frozen"] or not system_analysis["analysisId"]
            ):
                _add(
                    issues,
                    "ANALYZED_SYSTEM_NOT_FROZEN",
                    system_path,
                    "analyzed systems require frozen=true and an analysisId",
                )
            if system_analysis["status"] != "analyzed" and (refs or theory or inference):
                _add(
                    issues,
                    "INACTIVE_SYSTEM_HAS_INFERENCE",
                    system_path,
                    "not-run/insufficient-input systems must not contain evidence or inference",
                )
            if system_analysis["status"] != "analyzed" and (
                system_analysis["frozen"] or system_analysis["analysisId"] is not None
            ):
                _add(
                    issues,
                    "INACTIVE_SYSTEM_MARKED_FROZEN",
                    system_path,
                    "inactive systems require frozen=false and analysisId=null",
                )
            if any(ref["system"] != system for ref in refs):
                _add(
                    issues,
                    "SYSTEM_REF_MISMATCH",
                    system_path,
                    f"all references in {system} analysis must use system={system}",
                )
            if analysis["analysisType"] == "integration" and system_analysis["status"] == "analyzed":
                if system_analysis["analysisId"] != frozen_inputs[system]:
                    _add(
                        issues,
                        "FROZEN_ANALYSIS_ID_MISMATCH",
                        f"{system_path}/analysisId",
                        "claim must reference the frozen analysis id from inputAudit",
                    )

        consistency = claim["consistency"]
        provisional = claim["provisionalConclusion"]
        if consistency["status"] == "conflict":
            if not consistency["conflicts"]:
                _add(issues, "CONFLICT_NOT_DESCRIBED", f"{base}/consistency", "conflict list is empty")
            if not provisional["conflictRetained"]:
                _add(
                    issues,
                    "CONFLICT_ERASED",
                    f"{base}/provisionalConclusion/conflictRetained",
                    "a declared conflict must remain visible in the provisional conclusion",
                )

        comparison = claim["comparisonBasis"]
        if consistency["status"] == "aligned":
            if not all(comparison[key] for key in ("topicAligned", "directionAligned", "timeRangeAligned", "specificityAligned")):
                _add(
                    issues,
                    "FALSE_ALIGNMENT",
                    f"{base}/comparisonBasis",
                    "aligned claims require topic, direction, time range, and specificity alignment",
                )
            if consistency["conflicts"]:
                _add(issues, "ALIGNED_WITH_CONFLICTS", f"{base}/consistency/conflicts", "must be empty")

        counter = claim["counterEvidence"]
        if counter["status"] == "identified" and not counter["items"]:
            _add(issues, "COUNTEREVIDENCE_MISSING_ITEMS", f"{base}/counterEvidence", "items are required")
        if counter["status"] != "identified" and counter["items"]:
            _add(issues, "COUNTEREVIDENCE_STATUS_MISMATCH", f"{base}/counterEvidence", "items must be empty")
        for counter_index, item in enumerate(counter["items"]):
            if any(ref["system"] != item["system"] for ref in item["rawDataRefs"]):
                _add(
                    issues,
                    "COUNTEREVIDENCE_REF_MISMATCH",
                    f"{base}/counterEvidence/items/{counter_index}",
                    "counter-evidence references must match its system",
                )

        if provisional["status"] == "unable-to-judge":
            if "无法判断" not in provisional["text"]:
                _add(
                    issues,
                    "UNABLE_TEXT_NOT_EXPLICIT",
                    f"{base}/provisionalConclusion/text",
                    "unable-to-judge must explicitly contain 无法判断",
                )

        if analysis["scenario"] == "benchmark":
            if claim["actionAdvice"]["status"] != "not-applicable" or claim["actionAdvice"]["items"]:
                _add(issues, "BENCHMARK_HAS_ADVICE", f"{base}/actionAdvice", "benchmark output must not add advice")
        if analysis["analysisType"] == "integration":
            advice = claim["actionAdvice"]
            if advice["status"] != "not-applicable" or advice["items"] or advice["disclaimer"] is not None:
                _add(
                    issues,
                    "INTEGRATION_HAS_UNIFIED_ADVICE",
                    f"{base}/actionAdvice",
                    "integration is comparison-only and cannot provide action advice",
                )

        if analysis["analysisType"] == "integration":
            not_analyzed = [
                system
                for system in SYSTEMS
                if claim["systemAnalyses"][system]["status"] != "analyzed"
            ]
            if not_analyzed and provisional["status"] != "unable-to-judge":
                _add(
                    issues,
                    "INTEGRATION_WITHOUT_THREE_SYSTEMS",
                    f"{base}/systemAnalyses",
                    f"integration requires all three systems or unable-to-judge; unavailable: {not_analyzed}",
                )

        analyzed_count = sum(
            claim["systemAnalyses"][system]["status"] == "analyzed" for system in SYSTEMS
        )
        grade = claim["evidenceGrade"]
        grade_info = claim["gradeRationale"]
        major_counter = any(item["impact"] == "major" for item in counter["items"])
        if provisional["status"] == "unable-to-judge" and grade != "D":
            _add(issues, "UNABLE_GRADE_NOT_D", f"{base}/evidenceGrade", "unable-to-judge requires grade D")
        if analyzed_count < 2 and GRADE_SCORE[grade] > GRADE_SCORE["C"]:
            _add(issues, "SINGLE_SYSTEM_GRADE_TOO_HIGH", f"{base}/evidenceGrade", "single-system evidence is capped at C")
        if consistency["status"] == "conflict" and GRADE_SCORE[grade] > GRADE_SCORE["C"]:
            _add(issues, "CONFLICT_GRADE_TOO_HIGH", f"{base}/evidenceGrade", "conflict is capped at C")
        if claim["uncertainty"]["birthTimeSensitivity"] == "unstable" and GRADE_SCORE[grade] > GRADE_SCORE["C"]:
            _add(issues, "UNSTABLE_TIME_GRADE_TOO_HIGH", f"{base}/evidenceGrade", "unstable birth time is capped at C")
        if grade == "S":
            s_valid = (
                analyzed_count == 3
                and consistency["status"] == "aligned"
                and claim["uncertainty"]["birthTimeSensitivity"] == "stable"
                and not major_counter
                and grade_info["rawDataDirectness"] == "strong"
                and grade_info["theoryChain"] == "complete"
                and grade_info["systemIndependence"] == "three-systems"
            )
            if not s_valid:
                _add(issues, "INVALID_S_GRADE", f"{base}/evidenceGrade", "S-grade hard conditions are not met")

        if claim["pollutionRisk"] == "P3":
            if claim["empiricalUse"] != "engineering-only":
                _add(issues, "P3_EMPIRICAL_OVERCLAIM", f"{base}/empiricalUse", "P3 must be engineering-only")
            text = _claim_text(claim)
            if any(phrase in text for phrase in P3_PROOF_PHRASES):
                _add(issues, "P3_VALIDITY_CLAIM", base, "P3 output cannot claim astrology validity")

        topic_text = f"{claim['claimScope']['topic']} {claim['claim']}".lower()
        high_stakes = any(term in topic_text for term in HIGH_STAKES_TERMS)
        if analysis["scenario"] == "website" and high_stakes:
            if analysis["analysisType"] != "integration":
                advice = claim["actionAdvice"]
                if not advice["disclaimer"]:
                    _add(issues, "HIGH_STAKES_DISCLAIMER_MISSING", f"{base}/actionAdvice/disclaimer", "required")
                if any(not item["nonDiagnostic"] for item in advice["items"]):
                    _add(issues, "DIAGNOSTIC_ADVICE_FLAG", f"{base}/actionAdvice/items", "must be non-diagnostic")
            text = _claim_text(claim)
            if any(phrase in text for phrase in DETERMINISTIC_HIGH_STAKES_PHRASES):
                _add(issues, "DETERMINISTIC_HIGH_STAKES_CLAIM", base, "deterministic medical/death language is forbidden")

    decisions = analysis["questionDecisions"]
    decision_question_ids = [decision["questionId"] for decision in decisions]
    if len(set(decision_question_ids)) != len(decision_question_ids):
        _add(issues, "DUPLICATE_QUESTION_DECISION", "/questionDecisions", "questionId values must be unique")

    if analysis["scenario"] == "benchmark":
        claim_question_ids = {claim["questionId"] for claim in claims if claim.get("questionId")}
        decision_id_set = set(decision_question_ids)
        if decision_id_set != claim_question_ids:
            _add(
                issues,
                "QUESTION_DECISION_COVERAGE_MISMATCH",
                "/questionDecisions",
                "benchmark question decisions must exactly cover all non-null claim questionIds",
            )

        for decision_index, decision in enumerate(decisions):
            decision_path = f"/questionDecisions/{decision_index}"
            expected_claim_ids = {
                claim["claimId"] for claim in claims if claim.get("questionId") == decision["questionId"]
            }
            if set(decision["supportingClaimIds"]) != expected_claim_ids:
                _add(
                    issues,
                    "QUESTION_CLAIM_COVERAGE_MISMATCH",
                    f"{decision_path}/supportingClaimIds",
                    "question decision must reference every atomic claim assigned to that question",
                )
            supporting_claims = []
            for claim_id in decision["supportingClaimIds"]:
                claim = claim_by_id.get(claim_id)
                if claim is None:
                    _add(
                        issues,
                        "UNKNOWN_SUPPORTING_CLAIM",
                        f"{decision_path}/supportingClaimIds",
                        f"{claim_id} does not exist",
                    )
                    continue
                supporting_claims.append(claim)
                if claim.get("questionId") != decision["questionId"]:
                    _add(
                        issues,
                        "QUESTION_CLAIM_MISMATCH",
                        f"{decision_path}/supportingClaimIds",
                        f"{claim_id} belongs to {claim.get('questionId')!r}, not {decision['questionId']!r}",
                    )
            if any(claim["provisionalConclusion"]["status"] == "unable-to-judge" for claim in supporting_claims):
                if decision["selectedOption"] != "ABSTAIN":
                    _add(
                        issues,
                        "UNABLE_BUT_GUESSED",
                        f"{decision_path}/selectedOption",
                        "a question with an unable-to-judge supporting claim must abstain",
                    )

    return issues


def _raw_arg(value: str) -> tuple[str, Path]:
    if "=" not in value:
        raise argparse.ArgumentTypeError("--raw must be DOCUMENT_ID=PATH")
    document_id, path = value.split("=", 1)
    return document_id, Path(path)


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate a research.analysis.v1 document.")
    parser.add_argument("analysis", type=Path)
    parser.add_argument("--raw", action="append", type=_raw_arg, default=[])
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    args = parser.parse_args()

    analysis = json.loads(args.analysis.read_text(encoding="utf-8"))
    raw_documents = {
        document_id: json.loads(path.read_text(encoding="utf-8"))
        for document_id, path in args.raw
    }
    issues = validate_analysis(analysis, raw_documents, schema_path=args.schema)
    if issues:
        for issue in issues:
            print(f"{issue.code} {issue.path}: {issue.message}")
        raise SystemExit(1)
    print("PASS research.analysis.v1")


if __name__ == "__main__":
    main()
