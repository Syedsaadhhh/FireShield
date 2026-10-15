"""Adversarial receipt inputs and honest reference-model outcomes."""
import copy
import json
from pathlib import Path

import pytest

from continuity_engine import ContinuityError, compute_receipt_digest, validate_and_parse_incident_receipt
from retest_harness import run_retest_scenario


def golden():
    return json.loads((Path(__file__).parent / "fireos/app/src/test/resources/receipt-v1-unicode.json").read_text())


def test_shared_unicode_golden_receipt_and_mutation():
    receipt = golden()
    assert validate_and_parse_incident_receipt(json.dumps(receipt)) == receipt
    receipt["position_delta_ms"] += 1
    with pytest.raises(ContinuityError, match="digest mismatch"):
        validate_and_parse_incident_receipt(json.dumps(receipt))


def test_removing_digest_does_not_bypass_integrity():
    receipt = golden()
    del receipt["receipt_digest"]
    with pytest.raises(ContinuityError):
        validate_and_parse_incident_receipt(json.dumps(receipt))


@pytest.mark.parametrize("mutate", [
    lambda r: r.update(unexpected="extra"),
    lambda r: r["environment"].update(viewer_email="private@example.invalid"),
    lambda r: r["expected_state"].update(position_ms=True),
    lambda r: r["observed_state"].update(position_ms=-1),
    lambda r: r["observed_state"].update(position_ms=2**60),
    lambda r: r["observed_state"].update(captions_enabled="false"),
    lambda r: r["observed_state"].update(selected_audio_track_id="https://media.invalid/track"),
    lambda r: r.update(mismatch_fields=["captions_enabled", "captions_enabled"]),
    lambda r: r.update(mismatch_fields=["shell_command"]),
    lambda r: r.update(recovery_status="VERIFIED_MATCH"),
    lambda r: r.update(retest_fixture_id="BENIGN_SUBTITLE_CONTINUITY_V1"),
    lambda r: r.update(receipt_schema_version=True),
    lambda r: r.update(incident_id="x" * 129),
    lambda r: r["privacy_attestation"].update(contains_pii=True),
])
def test_even_rehashed_receipts_cannot_bypass_schema(mutate):
    receipt = copy.deepcopy(golden())
    mutate(receipt)
    receipt["receipt_digest"] = compute_receipt_digest(receipt)
    with pytest.raises(ContinuityError):
        validate_and_parse_incident_receipt(json.dumps(receipt))


def test_duplicate_keys_rejected_before_parser_discards_them():
    raw = json.dumps(golden()).replace('"app_version":', '"app_version": "hidden", "app_version":', 1)
    with pytest.raises(ContinuityError, match="Duplicate"):
        validate_and_parse_incident_receipt(raw)


@pytest.mark.parametrize("raw", ["NaN", "[" * 2000, " " * 65537, "[]"])
def test_invalid_or_unbounded_input_rejected(raw):
    with pytest.raises(ContinuityError):
        validate_and_parse_incident_receipt(raw)


def test_reference_model_never_claims_a_native_fix():
    result = run_retest_scenario(golden())
    assert result["test_status"] == "DETECTION_PASS"
    assert result["evidence_scope"] == "REFERENCE_MODEL_ONLY"
    assert result["player_fix_verified"] is False
    benign = run_retest_scenario({"retest_fixture_id": "BENIGN_SUBTITLE_CONTINUITY_V1"})
    assert benign["test_status"] == "MODEL_PASS"
    assert benign["player_fix_verified"] is False


def test_benign_model_failure_cannot_report_success(monkeypatch):
    import dataclasses
    import retest_harness
    original = retest_harness.verify_recovery
    def failing(*args, **kwargs):
        return dataclasses.replace(original(*args, **kwargs), status="DIVERGENCE_DETECTED")
    monkeypatch.setattr(retest_harness, "verify_recovery", failing)
    assert run_retest_scenario({"retest_fixture_id": "BENIGN_SUBTITLE_CONTINUITY_V1"})["test_status"] == "FAIL"
