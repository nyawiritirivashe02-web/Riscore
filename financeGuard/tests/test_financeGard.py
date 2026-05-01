import pytest
import sys
import os

# Add the app directory to the Python path (adjust if needed)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from financeGuard.api.endpoints import (
    evaluate_application_anomalies,
    _parse_float,
    decide_application,
    _score_sync,
    _build_features,
    ensure_assets_loaded,
    FREQUENT_APPLICATION_THRESHOLD,
    FREQUENT_APPLICATION_WINDOW_DAYS,
    AUTO_DECISION_REJECTION_THRESHOLD
)

# ============================================================
# ORIGINAL 6 TESTS (anomaly rules and input validation)
# ============================================================

def test_high_requested_amount_triggers_anomaly():
    anomalies = evaluate_application_anomalies(
        salary=1000,
        total_loans=2,
        active_loans=1,
        outstanding=500,
        return_rate=90,
        days_due=0,
        is_existing_borrower=False,
        recent_application_count=1,
        loan_amount=4000,
    )
    codes = {entry['code'] for entry in anomalies['anomalies']}
    assert 'HIGH_REQUESTED_AMOUNT' in codes
    assert anomalies['is_anomaly']

def test_low_requested_amount_skips_amount_anomaly():
    anomalies = evaluate_application_anomalies(
        salary=1200,
        total_loans=0,
        active_loans=0,
        outstanding=0,
        return_rate=100,
        days_due=0,
        is_existing_borrower=False,
        recent_application_count=1,
        loan_amount=300,
    )
    codes = {entry['code'] for entry in anomalies['anomalies']}
    assert 'HIGH_REQUESTED_AMOUNT' not in codes

def test_parse_float_requires_positive_amount():
    assert _parse_float('2500', 'amount', min_value=0.01) == 2500.0
    with pytest.raises(ValueError):
        _parse_float('-1', 'amount', min_value=0.01)

def test_outstanding_active_loan_anomaly_triggers():
    anomalies = evaluate_application_anomalies(
        salary=1500,
        total_loans=1,
        active_loans=1,
        outstanding=1200,
        return_rate=95,
        days_due=0,
        is_existing_borrower=True,
        recent_application_count=1,
        loan_amount=1000,
    )
    codes = {entry['code'] for entry in anomalies['anomalies']}
    assert 'OUTSTANDING_ACTIVE_LOAN' in codes
    assert anomalies['is_anomaly']

def test_frequent_applications_anomaly_triggered():
    anomalies = evaluate_application_anomalies(
        salary=2000,
        total_loans=2,
        active_loans=0,
        outstanding=0,
        return_rate=100,
        days_due=0,
        is_existing_borrower=True,
        recent_application_count=FREQUENT_APPLICATION_THRESHOLD,
        loan_amount=500,
    )
    codes = {entry['code'] for entry in anomalies['anomalies']}
    assert 'FREQUENT_LOAN_APPLICATIONS' in codes

def test_high_debt_to_income_anomaly_triggered():
    anomalies = evaluate_application_anomalies(
        salary=800,
        total_loans=0,
        active_loans=0,
        outstanding=2000,
        return_rate=90,
        days_due=0,
        is_existing_borrower=False,
        recent_application_count=1,
        loan_amount=400,
    )
    codes = {entry['code'] for entry in anomalies['anomalies']}
    assert 'HIGH_DEBT_TO_INCOME' in codes

# ============================================================
# PATH TESTING for decide_application()
# ============================================================

def test_decide_application_path_high_label():
    """Path A: label == 'High' -> immediate rejection"""
    status, reason = decide_application(
        score=85,
        label="High",
        anomaly_score=10,
        anomaly_codes=""
    )
    assert status == "rejected"
    assert "high risk score" in reason.lower()

def test_decide_application_path_anomaly_score():
    """Path B: anomaly_score >= threshold -> rejection"""
    status, reason = decide_application(
        score=25,
        label="Low",
        anomaly_score=40,
        anomaly_codes="FREQUENT_LOAN_APPLICATIONS"
    )
    assert status == "rejected"

def test_decide_application_path_approve():
    """Path C: low risk, anomaly_score below threshold -> approval"""
    status, reason = decide_application(
        score=25,
        label="Low",
        anomaly_score=20,
        anomaly_codes=""
    )
    assert status == "approved"
    assert "Approved automatically" in reason

def test_decide_application_edge_anomaly_score_equal_threshold():
    """Exactly at threshold should reject"""
    status, _ = decide_application(
        score=30,
        label="Low",
        anomaly_score=float(AUTO_DECISION_REJECTION_THRESHOLD),
        anomaly_codes="HIGH_DEBT_TO_INCOME"
    )
    assert status == "rejected"

def test_decide_application_high_label_with_anomaly_codes():
    """High label overrides any anomaly codes"""
    status, reason = decide_application(
        score=90,
        label="High",
        anomaly_score=10,
        anomaly_codes="SOME_CODE"
    )
    assert status == "rejected"
    assert "high risk score" in reason.lower()

# ============================================================
# BRANCH TESTING (additional branches beyond the 6 original)
# ============================================================

def test_anomaly_branch_active_loan_true():
    result = evaluate_application_anomalies(
        salary=1000,
        total_loans=2,
        active_loans=1,
        outstanding=500,
        return_rate=95,
        days_due=0,
        is_existing_borrower=True,
        recent_application_count=1,
        loan_amount=500,
        unsettled_loan_count=0
    )
    codes = {a["code"] for a in result["anomalies"]}
    assert "OUTSTANDING_ACTIVE_LOAN" in codes

def test_anomaly_branch_active_loan_false():
    result = evaluate_application_anomalies(
        salary=1000,
        total_loans=0,
        active_loans=0,
        outstanding=0,
        return_rate=95,
        days_due=0,
        is_existing_borrower=False,
        recent_application_count=1,
        loan_amount=500,
        unsettled_loan_count=0
    )
    codes = {a["code"] for a in result["anomalies"]}
    assert "OUTSTANDING_ACTIVE_LOAN" not in codes

def test_anomaly_branch_low_repayment_true():
    result = evaluate_application_anomalies(
        salary=1000,
        total_loans=1,
        active_loans=0,
        outstanding=0,
        return_rate=65,
        days_due=0,
        is_existing_borrower=True,
        recent_application_count=1,
        loan_amount=500,
        unsettled_loan_count=0
    )
    codes = {a["code"] for a in result["anomalies"]}
    assert "LOW_REPAYMENT_RATE" in codes

def test_anomaly_branch_low_repayment_false():
    result = evaluate_application_anomalies(
        salary=1000,
        total_loans=1,
        active_loans=0,
        outstanding=0,
        return_rate=85,
        days_due=0,
        is_existing_borrower=True,
        recent_application_count=1,
        loan_amount=500,
        unsettled_loan_count=0
    )
    codes = {a["code"] for a in result["anomalies"]}
    assert "LOW_REPAYMENT_RATE" not in codes

def test_anomaly_branch_severe_past_due_true():
    result = evaluate_application_anomalies(
        salary=1000,
        total_loans=1,
        active_loans=0,
        outstanding=0,
        return_rate=90,
        days_due=75,
        is_existing_borrower=True,
        recent_application_count=1,
        loan_amount=500,
        unsettled_loan_count=0
    )
    codes = {a["code"] for a in result["anomalies"]}
    assert "SEVERE_PAST_DUE" in codes

def test_anomaly_branch_frequent_applications_edge():
    result = evaluate_application_anomalies(
        salary=1000,
        total_loans=1,
        active_loans=0,
        outstanding=0,
        return_rate=90,
        days_due=0,
        is_existing_borrower=True,
        recent_application_count=FREQUENT_APPLICATION_THRESHOLD,
        loan_amount=500,
        unsettled_loan_count=0
    )
    codes = {a["code"] for a in result["anomalies"]}
    assert "FREQUENT_LOAN_APPLICATIONS" in codes

def test_anomaly_branch_unsettled_prior_loan_true():
    result = evaluate_application_anomalies(
        salary=1000,
        total_loans=2,
        active_loans=1,
        outstanding=500,
        return_rate=90,
        days_due=0,
        is_existing_borrower=True,
        recent_application_count=1,
        loan_amount=500,
        unsettled_loan_count=1
    )
    codes = {a["code"] for a in result["anomalies"]}
    assert "UNSETTLED_PRIOR_LOAN" in codes

def test_anomaly_branch_new_user_notification():
    result = evaluate_application_anomalies(
        salary=1000,
        total_loans=0,
        active_loans=0,
        outstanding=0,
        return_rate=100,
        days_due=0,
        is_existing_borrower=False,
        recent_application_count=1,
        loan_amount=500,
        unsettled_loan_count=0
    )
    assert result["has_notification"] is True
    notifications = {n["code"] for n in result["notifications"]}
    assert "NEW_USER_NO_HISTORY" in notifications

# ============================================================
# LOOP TESTING (zero, one, multiple anomalies)
# ============================================================

def test_anomaly_loop_zero_anomalies():
    result = evaluate_application_anomalies(
        salary=2000,
        total_loans=0,
        active_loans=0,
        outstanding=0,
        return_rate=100,
        days_due=0,
        is_existing_borrower=False,
        recent_application_count=1,
        loan_amount=1000,
        unsettled_loan_count=0
    )
    assert result["is_anomaly"] is False
    assert len(result["anomalies"]) == 0

def test_anomaly_loop_one_anomaly():
    result = evaluate_application_anomalies(
        salary=1000,
        total_loans=0,
        active_loans=0,
        outstanding=0,
        return_rate=100,
        days_due=0,
        is_existing_borrower=False,
        recent_application_count=1,
        loan_amount=3000,
        unsettled_loan_count=0
    )
    assert result["is_anomaly"] is True
    assert len(result["anomalies"]) == 1
    assert result["anomalies"][0]["code"] == "HIGH_REQUESTED_AMOUNT"

def test_anomaly_loop_multiple_anomalies():
    result = evaluate_application_anomalies(
        salary=800,
        total_loans=2,
        active_loans=1,
        outstanding=2000,
        return_rate=65,
        days_due=0,
        is_existing_borrower=True,
        recent_application_count=1,
        loan_amount=2500,
        unsettled_loan_count=0
    )
    codes = {a["code"] for a in result["anomalies"]}
    expected = {"HIGH_DEBT_TO_INCOME", "LOW_REPAYMENT_RATE", "HIGH_REQUESTED_AMOUNT"}
    assert expected.issubset(codes)
    assert result["is_anomaly"] is True

# ============================================================
# DATA FLOW TESTING
# ============================================================


# ============================================================
# CONSTANT VALIDATION TEST (optional)
# ============================================================

def test_constants_are_reasonable():
    assert FREQUENT_APPLICATION_THRESHOLD >= 2
    assert FREQUENT_APPLICATION_WINDOW_DAYS >= 7
    assert AUTO_DECISION_REJECTION_THRESHOLD >= 20