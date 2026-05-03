from __future__ import annotations

from flask import jsonify, render_template, request, url_for, send_file
from financeGuard.auth.token import token_required
from financeGuard import app, db, mail
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import (
    Column, String, Float, Integer, Boolean,
    DateTime, Text, func, select, update, desc, asc, or_
)
import os, asyncio, random, datetime, pickle, re, uuid, logging, json
from concurrent.futures import ThreadPoolExecutor
from financeGuard.api import AsyncSessionFactory, init_db, log
from financeGuard.models.models import BlacklistedUser, Borrower, User, Alert, Transaction, now_local
from werkzeug.security import check_password_hash, generate_password_hash
try:
    from flask_mail import Message
except ModuleNotFoundError:
    Message = None

try:
    import pandas as pd
    import numpy as np
except ModuleNotFoundError:
    pd = None
    np = None

try:
    import lime
    import lime.lime_tabular
except ModuleNotFoundError:
    lime = None
import re

def extract_name(text):
    match = re.search(r"(?:Name|Employee   Name:)\s*[:\-]?\s*([A-Z][a-z]+\s+[A-Z][a-z]+)", text)
    return match.group(1) if match else ""

def extract_net_pay(text):
    match = re.search(r"(?:Net Pay|NET   SALARY:)\s*[:\-]?\s*\$?([\d,]+\.?\d*)", text, re.IGNORECASE)
    if match:
        return float(match.group(1).replace(",", ""))
    return 0

def extract_department(text):
    match = re.search(r"(?: Department:)\s*[:\-]?\s*(.+)", text)
    return match.group(1).strip() if match else ""

def extract_position(text):
    match = re.search(r"(?:Position:|Job Title)\s*[:\-]?\s*(.+)", text)
    return match.group(1).strip() if match else ""
async def ensure_db_ready():
    """Initialize tables only when needed and seed demo data only if empty."""
    try:
        await init_db()
        await backfill_missing_tracking_numbers()
        async with AsyncSessionFactory() as session:
            result = await session.execute(select(func.count(Borrower.id)))
            count = int(result.scalar() or 0)
        if count > 0:
            log.info("Database already initialized with data; skipping seed")
            return
        log.info("Database initialized but empty; seeding demo data")
        await seed_data()
    except Exception:
        log.error("Database init/seed failed; continuing without DB", exc_info=True)
        return


async def backfill_missing_tracking_numbers(*, batch_size: int = 500) -> int:
    """Ensures transactions.tracking_number is never NULL."""
    total = 0
    while True:
        async with AsyncSessionFactory() as session:
            result = await session.execute(
                select(Transaction)
                .where(
                    Transaction.tracking_number.is_(None),
                    or_(
                        Transaction.status != "rejected",
                        Transaction.status.is_(None),
                    ),
                )
                .limit(batch_size)
            )
            rows = result.scalars().all()
            if not rows:
                break
            for tx in rows:
                tx.tracking_number = str(uuid.uuid4())
            await session.commit()
            total += len(rows)
    if total:
        log.info("Backfilled tracking_number for %s transaction(s)", total)
    return total


# --------------------------------------------------------------
#  Load Trained Model Artefacts
# --------------------------------------------------------------
def _require_ml_deps() -> None:
    if pd is None or np is None:
        raise RuntimeError("ML dependencies missing. Install pandas, numpy, and scikit-learn.")


def load_artefacts():
    _require_ml_deps()
    mp = os.path.join(app.config['MODEL_DIR'], "risk_model.pkl")
    if not os.path.exists(mp):
        raise FileNotFoundError("Model not found - run `python train_model.py` first.")
    with open(os.path.join(app.config['MODEL_DIR'], "risk_model.pkl"), "rb") as f:
        model = pickle.load(f)
    with open(os.path.join(app.config['MODEL_DIR'], "label_encoder.pkl"), "rb") as f:
        le = pickle.load(f)
    with open(os.path.join(app.config['MODEL_DIR'], "feature_cols.pkl"), "rb") as f:
        feat_col = pickle.load(f)
    with open(os.path.join(app.config['MODEL_DIR'], "metadata.pkl"), "rb") as f:
        meta = pickle.load(f)
    return model, le, feat_col, meta


RISK_MODEL = None
LABEL_ENC = None
FEATURE_COLS = None
META = None
MFI_DF = None
ALL_SECTORS = []
ALL_REASONS = []
ASSETS_LOADED = False
LIME_EXPLAINER = None
LIME_TRAINING_SAMPLE = None


def ensure_assets_loaded():
    _require_ml_deps()
    global RISK_MODEL, LABEL_ENC, FEATURE_COLS, META, MFI_DF, ALL_SECTORS, ALL_REASONS, ASSETS_LOADED
    if ASSETS_LOADED:
        return
    log.info("Loading trained model artefacts...")
    RISK_MODEL, LABEL_ENC, FEATURE_COLS, META = load_artefacts()
    log.info(f"Model loaded | Accuracy: {META['accuracy']:.1%} | Classes: {META['label_classes']}")
    MFI_DF = pd.read_csv(app.config['DATA_DIR'] + "/data.csv")
    MFI_DF["_name_key"] = MFI_DF["Full Name"].str.lower().str.strip()
    log.info(f"MFI consortium data: {len(MFI_DF)} records")
    ALL_SECTORS = [c.replace("Employment Sector_", "") for c in META["cat_feature_names"]
                   if c.startswith("Employment Sector_")]
    ALL_REASONS = [c.replace("Common Loan Reason_", "") for c in META["cat_feature_names"]
                   if c.startswith("Common Loan Reason_")]
    ASSETS_LOADED = True

    # ── LIME Explainer (initialised once with real data distribution) ──────────
    global LIME_EXPLAINER, LIME_TRAINING_SAMPLE
    if lime is not None:
        try:
            # Build feature matrix from MFI_DF so LIME knows the real data ranges
            sample_df = MFI_DF.copy()
            sample_df["_name_key"] = sample_df.get("_name_key", sample_df["Full Name"].str.lower().str.strip())
            lime_rows = []
            for _, row in sample_df.iterrows():
                try:
                    feat_df = _build_features(
                        salary=float(row.get("Current Monthly Salary (USD)", 500)),
                        sector=str(row.get("Employment Sector", "Unknown")),
                        reason=str(row.get("Common Loan Reason", "Emergency")),
                        total_loans=float(row.get("Total Previous Loans", 0)),
                        active_loans=float(row.get("Active Loans", 0)),
                        outstanding=float(row.get("Total Outstanding Balance (USD)", 0)),
                        avg_loan=float(row.get("Avg Loan Amount (USD)", 0)),
                        return_rate=float(row.get("Historical Return Rate (%)", 100)),
                        days_due=float(row.get("Days Past Due (Max)", 0)),
                        mfi_score=float(row.get("MFI Diversity Score", 1)),
                        requested_amount=float(row.get("Avg Loan Amount (USD)", 0)),
                    )
                    lime_rows.append(feat_df.iloc[0].values)
                except Exception:
                    continue
            if lime_rows:
                LIME_TRAINING_SAMPLE = np.array(lime_rows)
                LIME_EXPLAINER = lime.lime_tabular.LimeTabularExplainer(
                    training_data=LIME_TRAINING_SAMPLE,
                    mode="classification",
                    feature_names=list(FEATURE_COLS),
                    class_names=list(LABEL_ENC.classes_),
                    discretize_continuous=True,
                )
                log.info(f"LIME explainer ready with {len(lime_rows)} training samples")
            else:
                log.warning("LIME: no training rows could be built; XAI disabled")
        except Exception:
            log.warning("LIME explainer init failed; XAI will be unavailable", exc_info=True)


# --------------------------------------------------------------
#  Thread pool for CPU-bound ML inference
# --------------------------------------------------------------
ML_EXECUTOR = ThreadPoolExecutor(
    max_workers=4,
    thread_name_prefix="ml-worker",
)

FREQUENT_APPLICATION_WINDOW_DAYS = 30
FREQUENT_APPLICATION_THRESHOLD = 3

VALID_APPLICATION_STATUSES = {"processing", "approved", "suspended", "rejected"}
DEPOSIT_CHANNELS = {
    "ecocash": {"label": "EcoCash", "fields": ("account_name", "phone_number")},
    "innbucks": {"label": "InnBucks", "fields": ("account_name", "phone_number")},
    "visa_card": {"label": "Visa Card", "fields": ("account_name", "bank_name", "card_number", "expiry_month", "expiry_year")},
    "mastercard": {"label": "MasterCard", "fields": ("account_name", "bank_name", "card_number", "expiry_month", "expiry_year")},
}


def _generate_tracking_number():
    return f"T{uuid.uuid4().hex[:8].upper()}"


def _mask_number(value: str, *, visible_digits: int = 4) -> str:
    digits = re.sub(r"\D", "", value or "")
    if not digits:
        return ""
    hidden = max(0, len(digits) - visible_digits)
    return ("*" * hidden) + digits[-visible_digits:]


def _serialize_deposit(tx: Transaction) -> dict | None:
    if not tx.deposit_channel:
        return None
    try:
        raw_details = json.loads(tx.deposit_details or "{}")
    except json.JSONDecodeError:
        raw_details = {}
    details = dict(raw_details)
    if tx.deposit_channel in {"ecocash", "innbucks"} and details.get("phone_number"):
        details["phone_number"] = _mask_number(details["phone_number"])
    if tx.deposit_channel in {"visa_card", "mastercard"} and details.get("card_number"):
        details["card_number"] = _mask_number(details["card_number"])
    channel_meta = DEPOSIT_CHANNELS.get(tx.deposit_channel, {})
    return {
        "channel": tx.deposit_channel,
        "label": channel_meta.get("label", tx.deposit_channel),
        "details": details,
        "updated_at": tx.deposit_updated_at.isoformat() if tx.deposit_updated_at else None,
    }


def _validate_deposit_payload(payload: dict) -> tuple[str, dict]:
    channel = (payload.get("channel") or "").strip().lower()
    if channel not in DEPOSIT_CHANNELS:
        raise ValueError("Please choose a valid deposit channel.")
    details = payload.get("details") or {}
    if not isinstance(details, dict):
        raise ValueError("Deposit details are required.")
    clean_details: dict[str, str] = {}
    for field_name in DEPOSIT_CHANNELS[channel]["fields"]:
        value = str(details.get(field_name) or "").strip()
        if not value:
            raise ValueError(f"{field_name.replace('_', ' ').title()} is required.")
        clean_details[field_name] = value
    if channel in {"ecocash", "innbucks"}:
        phone_digits = re.sub(r"\D", "", clean_details["phone_number"])
        if len(phone_digits) < 9 or len(phone_digits) > 15:
            raise ValueError("Phone number must contain 9 to 15 digits.")
        clean_details["phone_number"] = phone_digits
    else:
        card_digits = re.sub(r"\D", "", clean_details["card_number"])
        if len(card_digits) < 12 or len(card_digits) > 19:
            raise ValueError("Card number must contain 12 to 19 digits.")
        clean_details["card_number"] = card_digits
        month = int(clean_details["expiry_month"])
        year = int(clean_details["expiry_year"])
        current_year = now_local().year
        if month < 1 or month > 12:
            raise ValueError("Expiry month must be between 1 and 12.")
        if year < current_year or year > current_year + 15:
            raise ValueError("Expiry year is not valid.")
        clean_details["expiry_month"] = f"{month:02d}"
        clean_details["expiry_year"] = str(year)
    return channel, clean_details


def _parse_float(value, field_name, *, min_value=None):
    try:
        if value is None or value == "":
            raise ValueError
        parsed = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{field_name} must be a valid number.")
    if min_value is not None and parsed < min_value:
        raise ValueError(f"{field_name} must be >= {min_value}.")
    return parsed


def _normalize_name(value: str) -> str:
    value = (value or "").strip().lower()
    value = re.sub(r"[^a-z\s'-]+", " ", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def _names_match(a: str, b: str) -> bool:
    if not a or not b:
        return False
    return _normalize_name(a) == _normalize_name(b)


# --------------------------------------------------------------
#  Feature Engineering
# --------------------------------------------------------------
def _build_features(salary, sector, reason, total_loans, active_loans,
                    outstanding, avg_loan, return_rate, days_due, mfi_score,
                    requested_amount):
    ensure_assets_loaded()
    salary = max(float(salary), 1)
    base_outstanding = float(outstanding)
    base_avg_loan = float(avg_loan)
    requested_amount = max(float(requested_amount or 0), 0.0)
    adjusted_outstanding = base_outstanding + requested_amount
    adjusted_avg_loan = ((base_avg_loan + requested_amount) / 2) if base_avg_loan > 0 else requested_amount
    loan_to_income_amount = max(adjusted_avg_loan, requested_amount)
    row = {
        "Current Monthly Salary (USD)": salary,
        "Total Previous Loans": float(total_loans),
        "Active Loans": float(active_loans),
        "Total Outstanding Balance (USD)": adjusted_outstanding,
        "Avg Loan Amount (USD)": max(adjusted_avg_loan, 0.0),
        "Historical Return Rate (%)": float(return_rate),
        "Days Past Due (Max)": float(days_due),
        "MFI Diversity Score": float(mfi_score),
        "Debt_to_Income": adjusted_outstanding / salary,
        "Loan_to_Income": loan_to_income_amount / salary,
        "Active_Loan_Density": float(active_loans) / max(float(total_loans), 1),
        "Return_Rate_Norm": float(return_rate) / 100.0,
        "Is_Overdue": int(float(days_due) > 0),
        "Overdue_Severity": (
            0 if float(days_due) == 0 else
            1 if float(days_due) <= 30 else
            2 if float(days_due) <= 60 else
            3 if float(days_due) <= 90 else 4
        ),
    }
    for s in ALL_SECTORS:
        row[f"Employment Sector_{s}"] = int(sector == s)
    for r in ALL_REASONS:
        row[f"Common Loan Reason_{r}"] = int(reason == r)
    return pd.DataFrame([row])[FEATURE_COLS]


AUTO_DECISION_REJECTION_THRESHOLD = 35.0
ANOMALY_REJECTION_FRONTEND_SCORE_MIN = float(random.randrange(80, 88))
ANOMALY_REJECTION_FRONTEND_SCORE_MAX = float(random.randrange(88, 98))
PAYOUT_DETAILS_MARKER = "\n\n[PAYOUT_DETAILS]"


def _score_sync(salary, sector, reason, total_loans, active_loans,
                outstanding, avg_loan, return_rate, days_due, mfi_score,
                requested_amount):
    X = _build_features(salary, sector, reason, total_loans, active_loans,
                        outstanding, avg_loan, return_rate, days_due, mfi_score,
                        requested_amount)
    proba = RISK_MODEL.predict_proba(X)[0]
    classes = LABEL_ENC.classes_
    prob_dict = {c: float(p) for c, p in zip(classes, proba)}
    label = classes[int(np.argmax(proba))]
    score = round(
        prob_dict.get("High", 0) * 100 +
        prob_dict.get("Medium", 0) * 45 +
        prob_dict.get("Low", 0) * 10,
        1,
    )
    return score, label, prob_dict


async def score_borrower_async(salary, sector, reason, total_loans, active_loans,
                               outstanding, avg_loan, return_rate, days_due, mfi_score,
                               requested_amount):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        ML_EXECUTOR,
        _score_sync,
        salary, sector, reason, total_loans, active_loans,
        outstanding, avg_loan, return_rate, days_due, mfi_score,
        requested_amount,
    )


def generate_xai_explanation(feature_vector: np.ndarray) -> dict:
    """
    Return a structured LIME explanation for the model's prediction.
    Uses real training data distribution (set in ensure_assets_loaded).
    Always safe to call — returns a graceful fallback if LIME is unavailable.
    """
    if LIME_EXPLAINER is None or RISK_MODEL is None:
        return {"available": False, "reason": "XAI not available (model not loaded)."}
    try:
        proba = RISK_MODEL.predict_proba([feature_vector])[0]
        predicted_idx = int(np.argmax(proba))
        predicted_label = LABEL_ENC.classes_[predicted_idx]
        explanation = LIME_EXPLAINER.explain_instance(
            data_row=feature_vector,
            predict_fn=RISK_MODEL.predict_proba,
            num_features=5,
            top_labels=1,
            labels=[predicted_idx],
        )
        contributions = explanation.as_list(label=predicted_idx)
        factors = []
        for feat, weight in contributions:
            direction = "increases" if weight > 0 else "decreases"
            factors.append({
                "feature": feat,
                "direction": direction,
                "weight": round(abs(weight), 4),
                "summary": f"{feat} {direction} risk by {abs(weight):.3f}",
            })
        return {
            "available": True,
            "predicted_label": predicted_label,
            "confidence": round(float(proba[predicted_idx]) * 100, 1),
            "factors": factors,
            "plain_text": f"Predicted {predicted_label} risk. " + ". ".join(f["summary"] for f in factors),
        }
    except Exception as exc:
        log.warning(f"XAI explanation failed: {exc}", exc_info=True)
        return {"available": False, "reason": f"Explanation could not be generated: {str(exc)}"}


def evaluate_application_anomalies(
    *, salary: float, total_loans: float, active_loans: float,
    outstanding: float, return_rate: float, days_due: float,
    is_existing_borrower: bool, recent_application_count: int,
    loan_amount: float = 0.0, unsettled_loan_count: int = 0,
):
    anomalies = []
    notifications = []

    def add(code: str, description: str, score: float):
        anomalies.append({"code": code, "description": description, "score": float(score)})

    def notify(code: str, description: str):
        notifications.append({"code": code, "description": description})

    debt_to_income = float(outstanding) / max(float(salary), 1.0)

    if float(active_loans) > 0 and float(outstanding) > 0:
        add("OUTSTANDING_ACTIVE_LOAN", "User is applying while an outstanding active loan exists.", 20.0)

    if int(recent_application_count) >= FREQUENT_APPLICATION_THRESHOLD:
        add("FREQUENT_LOAN_APPLICATIONS", f"High application frequency: {recent_application_count} assessments in {FREQUENT_APPLICATION_WINDOW_DAYS} days.", 18.0)

    if int(unsettled_loan_count) > 0:
        add("UNSETTLED_PRIOR_LOAN", f"Existing approved loan(s) detected ({int(unsettled_loan_count)}).", 14.0)

    if (not is_existing_borrower) and float(total_loans) <= 0 and float(active_loans) <= 0:
        notify("NEW_USER_NO_HISTORY", "New user has no previous borrowing records.")

    if debt_to_income >= 1.5:
        add("HIGH_DEBT_TO_INCOME", f"Debt-to-income ratio is high ({debt_to_income:.2f}).", 16.0)

    if float(return_rate) < 70:
        add("LOW_REPAYMENT_RATE", f"Historical return rate is low ({float(return_rate):.1f}%).", 14.0)

    if float(days_due) > 60:
        add("SEVERE_PAST_DUE", f"Severe delinquency history detected ({float(days_due):.0f} days past due).", 15.0)

    request_to_salary = float(loan_amount) / max(float(salary), 1.0)
    if request_to_salary >= 2.5:
        add("HIGH_REQUESTED_AMOUNT", f"Requested loan is {request_to_salary:.1f}x monthly salary.", 15.0)

    anomaly_score = round(min(100.0, sum(item["score"] for item in anomalies)), 1)
    return {
        "is_anomaly": len(anomalies) > 0,
        "anomaly_score": anomaly_score,
        "anomalies": anomalies,
        "has_notification": len(notifications) > 0,
        "notifications": notifications,
    }


def _format_rejection_reason(*, score: float, label: str, anomaly_codes: list[str]) -> str:
    if (label or "").title() == "High":
        return f"Rejected: high risk score ({score:.1f}/100). Your application cannot be approved."
    if "FREQUENT_LOAN_APPLICATIONS" in anomaly_codes:
        return "Rejected: too many recent loan applications. Your application cannot be approved."
    if "UNSETTLED_PRIOR_LOAN" in anomaly_codes or "OUTSTANDING_ACTIVE_LOAN" in anomaly_codes:
        return "Rejected: you have an outstanding active loan. Your application cannot be approved."
    if "HIGH_DEBT_TO_INCOME" in anomaly_codes:
        return "Rejected: debt-to-income ratio is too high. Your application cannot be approved."
    if "LOW_REPAYMENT_RATE" in anomaly_codes:
        return "Rejected: repayment history is below the required threshold. Your application cannot be approved."
    if "SEVERE_PAST_DUE" in anomaly_codes:
        return "Rejected: severe past-due history detected. Your application cannot be approved."
    if "HIGH_REQUESTED_AMOUNT" in anomaly_codes:
        return "Rejected: requested amount is too high relative to your salary. Your application cannot be approved."
    return "Rejected: risk assessment did not meet approval requirements."


def _is_anomaly_rejection(*, decision_status: str, anomaly_codes: str) -> bool:
    codes = [c.strip() for c in (anomaly_codes or "").split(",") if c.strip() and c.strip() != "none"]
    return decision_status == "rejected" and len(codes) > 0


def _boost_rejected_anomaly_risk_score(*, score: float, anomaly_score: float, decision_status: str, anomaly_codes: str) -> float:
    if not _is_anomaly_rejection(decision_status=decision_status, anomaly_codes=anomaly_codes):
        return round(float(score), 1)
    span = ANOMALY_REJECTION_FRONTEND_SCORE_MAX - ANOMALY_REJECTION_FRONTEND_SCORE_MIN
    threshold = max(float(AUTO_DECISION_REJECTION_THRESHOLD), 1.0)
    scaled = min(span, round((max(float(anomaly_score), 0.0) / threshold) * span, 1))
    return round(min(ANOMALY_REJECTION_FRONTEND_SCORE_MAX, ANOMALY_REJECTION_FRONTEND_SCORE_MIN + scaled), 1)


def _format_currency(value: float) -> str:
    try:
        return f"${value:,.2f}"
    except (TypeError, ValueError):
        return "$0.00"


def _calc_debt_to_income(context: dict[str, float]) -> float:
    return float(context.get("outstanding", 0.0)) / max(float(context.get("salary", 1.0)), 1.0)


def _calc_request_ratio(context: dict[str, float]) -> float:
    return float(context.get("loan_amount", 0.0)) / max(float(context.get("salary", 1.0)), 1.0)


def _pass_detail_active_debt(context: dict[str, float]) -> str:
    loans = int(context.get("active_loans", 0))
    outstanding = _format_currency(float(context.get("outstanding", 0.0)))
    return f"Active loans: {loans}; outstanding balance {outstanding}"


def _pass_detail_frequency(context: dict[str, float]) -> str:
    count = int(context.get("recent_application_count", 0))
    return f"{count} application(s) in the last {FREQUENT_APPLICATION_WINDOW_DAYS} days (threshold {FREQUENT_APPLICATION_THRESHOLD})"


def _pass_detail_unsettled_prior(context: dict[str, float]) -> str:
    count = int(context.get("unsettled_loan_count", 0))
    return f"{count} unsettled prior loan(s) on record"


def _pass_detail_history(context: dict[str, float]) -> str:
    total = int(context.get("total_loans", 0))
    active = int(context.get("active_loans", 0))
    if context.get("is_existing_borrower"):
        return f"Existing borrower history ({total} previous loans, {active} active)"
    return "New borrower profile detected; no prior borrowing history on record"


def _pass_detail_debt_to_income(context: dict[str, float]) -> str:
    ratio = _calc_debt_to_income(context)
    return f"Debt-to-income ratio {ratio:.2f}"


def _pass_detail_return_rate(context: dict[str, float]) -> str:
    return f"Historical return rate {float(context.get('return_rate', 0.0)):.1f}%"


def _pass_detail_past_due(context: dict[str, float]) -> str:
    days = int(context.get("days_due", 0))
    return f"Longest past due period {days} day(s)"


def _pass_detail_requested_amount(context: dict[str, float]) -> str:
    ratio = _calc_request_ratio(context)
    return f"Requested amount {ratio:.1f}x monthly salary"


AREA_FEEDBACK_DEFINITIONS = [
    {"code": "OUTSTANDING_ACTIVE_LOAN", "title": "Active debt", "pass_detail": _pass_detail_active_debt},
    {"code": "FREQUENT_LOAN_APPLICATIONS", "title": "Application frequency", "pass_detail": _pass_detail_frequency},
    {"code": "UNSETTLED_PRIOR_LOAN", "title": "Unsettled prior loans", "pass_detail": _pass_detail_unsettled_prior},
    {"code": "NEW_USER_NO_HISTORY", "title": "Borrowing history", "pass_detail": _pass_detail_history},
    {"code": "HIGH_DEBT_TO_INCOME", "title": "Debt-to-income", "pass_detail": _pass_detail_debt_to_income},
    {"code": "LOW_REPAYMENT_RATE", "title": "Repayment history", "pass_detail": _pass_detail_return_rate},
    {"code": "SEVERE_PAST_DUE", "title": "Delinquency", "pass_detail": _pass_detail_past_due},
    {"code": "HIGH_REQUESTED_AMOUNT", "title": "Loan request", "pass_detail": _pass_detail_requested_amount},
]

AREA_FEEDBACK_ORDER = [entry["code"] for entry in AREA_FEEDBACK_DEFINITIONS]
AREA_FEEDBACK_MAP = {entry["code"]: entry for entry in AREA_FEEDBACK_DEFINITIONS}


def _build_area_feedback(*, anomalies: list[dict], context: dict[str, float], label: str, score: float) -> dict[str, list[dict[str, str]]]:
    failed_entries = []
    seen_codes = {anomaly["code"] for anomaly in anomalies}
    for anomaly in anomalies:
        spec = AREA_FEEDBACK_MAP.get(anomaly["code"])
        title = spec["title"] if spec else anomaly["code"].replace("_", " ").title()
        failed_entries.append({"title": title, "detail": anomaly["description"]})
    if (label or "").lower() == "high":
        failed_entries.append({"title": "Risk label", "detail": f"Score {score:.1f}/100 classified as High risk"})
    passed_entries = []
    for code in AREA_FEEDBACK_ORDER:
        if code in seen_codes:
            continue
        spec = AREA_FEEDBACK_MAP[code]
        passed_entries.append({"title": spec["title"], "detail": spec["pass_detail"](context)})
    return {"failed": failed_entries, "passed": passed_entries}


def _join_area_entries(entries: list[dict[str, str]]) -> str:
    return "; ".join(f"{entry['title']}: {entry['detail']}" for entry in entries)


def _format_area_summary(area_feedback: dict[str, list[dict[str, str]]]) -> str:
    if not area_feedback:
        return ""
    failed = area_feedback.get("failed", [])
    passed = area_feedback.get("passed", [])
    sections = []
    if failed:
        sections.append(f"Failed areas: {_join_area_entries(failed)}.")
    else:
        sections.append("Failed areas: none.")
    if passed:
        sections.append(f"Passed areas: {_join_area_entries(passed)}.")
    else:
        sections.append("Passed areas: none.")
    return " ".join(sections)


def _append_area_summary(message: str, area_feedback: dict[str, list[dict[str, str]]]) -> str:
    summary = _format_area_summary(area_feedback)
    if not summary:
        return message
    return f"{message.rstrip()} {summary}"


def _split_transaction_description(description: str | None) -> tuple[str, dict | None]:
    raw = (description or "").strip()
    if PAYOUT_DETAILS_MARKER not in raw:
        return raw, None
    base, payload = raw.split(PAYOUT_DETAILS_MARKER, 1)
    try:
        details = json.loads(payload.strip())
        if isinstance(details, dict):
            return base.strip(), details
    except json.JSONDecodeError:
        pass
    return base.strip(), None


def _get_stored_payout_details(tx: Transaction) -> dict | None:
    if tx.deposit_channel:
        serialized = _serialize_deposit(tx)
        if serialized:
            details = serialized.get("details") or {}
            return {
                "channel": serialized["channel"],
                "channel_label": serialized["label"],
                **details,
                "updated_at": serialized.get("updated_at"),
            }
    _, legacy_payout_details = _split_transaction_description(tx.description)
    return legacy_payout_details


def _validate_payout_details(channel: str, details: dict) -> dict:
    normalized_channel = (channel or "").strip().lower()
    if normalized_channel == "master_card":
        normalized_channel = "mastercard"
    payload_channel, payload_details = _validate_deposit_payload({"channel": normalized_channel, "details": details})
    return {"channel": payload_channel, "channel_label": DEPOSIT_CHANNELS[payload_channel]["label"], **payload_details}


def decide_application(*, score: float, label: str, anomaly_score: float, anomaly_codes: str) -> tuple[str, str]:
    safe_label = (label or "").title()
    codes = [c.strip() for c in (anomaly_codes or "").split(",") if c.strip() and c.strip() != "none"]
    if safe_label == "High":
        return ("rejected", _format_rejection_reason(score=score, label=label, anomaly_codes=codes))
    if float(anomaly_score) >= AUTO_DECISION_REJECTION_THRESHOLD:
        return ("rejected", _format_rejection_reason(score=score, label=label, anomaly_codes=codes))
    return ("approved", f"Approved automatically based on {safe_label.lower() or 'low'} risk score {score:.1f}.")


def _format_area_entries(entries: list[dict[str, str]]) -> str:
    if not entries:
        return ""
    return "\n".join(f"• {entry['title']}: {entry['detail']}" for entry in entries)


def _format_user_area_message(area_feedback: dict[str, list[dict[str, str]]]) -> str:
    if not area_feedback:
        return ""
    sections: list[str] = []
    failed = area_feedback.get("failed") or []
    passed = area_feedback.get("passed") or []
    if failed:
        sections.append("Areas needing attention:\n" + _format_area_entries(failed))
    if passed:
        sections.append("Areas you met expectations:\n" + _format_area_entries(passed))
    return "\n\n".join(sections).strip()


# --------------------------------------------------------------
#  MFI Name Lookup
# --------------------------------------------------------------
def lookup_mfi(first_name: str, last_name: str):
    ensure_assets_loaded()
    full = f"{first_name} {last_name}".lower().strip()
    match = MFI_DF[MFI_DF["_name_key"] == full]
    if not match.empty:
        return match.iloc[0].to_dict(), "exact"
    match = MFI_DF[MFI_DF["_name_key"].str.endswith(last_name.lower().strip())]
    if not match.empty:
        return match.iloc[0].to_dict(), "partial"
    return None, None


# --------------------------------------------------------------
#  Alert Dispatch
# --------------------------------------------------------------
def _build_admin_alert_email_html(*, alert_type: str, severity: str, borrower_name: str, message: str, timestamp: datetime.datetime) -> str:
    safe_type = (alert_type or "ALERT").replace("_", " ").title()
    safe_sev = (severity or "INFO").upper()
    badge_color = "#ef4444" if safe_sev in {"CRITICAL", "HIGH"} else "#f59e0b" if safe_sev == "MEDIUM" else "#3b82f6"
    ts = timestamp.astimezone(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>FinanceGuard Alert</title></head>
<body style="margin:0;background:#f5f7fb;font-family:Segoe UI,Arial,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f5f7fb;padding:32px 0"><tr><td align="center">
<table width="640" cellpadding="0" cellspacing="0" style="background:#fff;border-radius:16px;box-shadow:0 14px 35px rgba(15,23,42,0.08);">
<tr><td style="padding:28px 32px;background:linear-gradient(120deg,#0f172a,#1e293b);color:#fff;">
<div style="font-size:12px;letter-spacing:2px;text-transform:uppercase;opacity:0.7;">FinanceGuard Alert</div>
<div style="font-size:22px;font-weight:600;margin-top:6px;">{safe_type}</div>
<div style="margin-top:10px;display:inline-block;padding:6px 12px;border-radius:999px;background:{badge_color};color:#fff;font-weight:600;font-size:12px;">Severity: {safe_sev}</div>
</td></tr>
<tr><td style="padding:28px 32px;">
<p style="margin:0 0 12px;font-size:16px;font-weight:600;">Borrower: {borrower_name}</p>
<p style="margin:0 0 16px;font-size:14px;line-height:1.6;color:#475569;">{message}</p>
<div style="margin-top:18px;padding:16px;border-radius:12px;background:#f8fafc;border:1px solid #e2e8f0;">
<div style="font-size:12px;text-transform:uppercase;letter-spacing:1px;color:#64748b;margin-bottom:6px;">Suggested Actions</div>
<ul style="margin:0;padding-left:18px;font-size:13px;color:#334155;"><li>Review recent applications and anomaly history.</li><li>Confirm borrower identity and outstanding obligations.</li><li>Escalate to risk operations if needed.</li></ul>
</div>
<table width="100%" style="border-collapse:collapse;"><tr><td style="padding:10px 0;border-top:1px solid #e2e8f0;font-size:12px;color:#64748b;">Timestamp: {ts}</td></tr></table>
</td></tr></table>
<div style="margin-top:14px;font-size:11px;color:#94a3b8;">This message was generated automatically by FinanceGuard.</div>
</td></tr></table></body></html>"""


async def _send_admin_alert_email(*, alert_type: str, severity: str, borrower_name: str, message: str, timestamp: datetime.datetime):
    admin_email = os.getenv("ADMIN_ALERT_EMAIL") or app.config.get("ADMIN_ALERT_EMAIL") or os.getenv("ADMIN_EMAIL") or app.config.get("ADMIN_EMAIL")
    if not admin_email:
        log.warning("Admin alert email not configured; skipping email.")
        return
    if mail is None or Message is None:
        log.info("Flask-Mail not available; skipping admin email send.")
        return
    subject = f"[{(severity or 'INFO').upper()}] FinanceGuard Alert: {(alert_type or 'ALERT').replace('_', ' ')}"
    html = _build_admin_alert_email_html(alert_type=alert_type, severity=severity, borrower_name=borrower_name, message=message, timestamp=timestamp)
    body = f"FinanceGuard Alert\nType: {alert_type}\nSeverity: {severity}\nBorrower: {borrower_name}\nMessage: {message}\nTimestamp: {timestamp.isoformat()}"
    def _send():
        with app.app_context():
            msg = Message(subject=subject, recipients=[admin_email], html=html, body=body)
            mail.send(msg)
    await asyncio.to_thread(_send)


async def _dispatch_alert_channels(name: str, message: str, severity: str, channel: str, alert_type: str, timestamp: datetime.datetime):
    async def _send(ch: str):
        await asyncio.sleep(0)
        if ch.strip().lower() == "email":
            await _send_admin_alert_email(alert_type=alert_type, severity=severity, borrower_name=name, message=message, timestamp=timestamp)
            return
        log.info(f"[{ch}] {severity} -> {name}: {message[:80]}")
    await asyncio.gather(*[_send(ch.strip()) for ch in channel.split(",")])


async def create_alert(session: AsyncSession, borrower_id: str, name: str, alert_type: str, message: str, severity: str, channel: str = "Dashboard"):
    now = datetime.datetime.now(datetime.timezone.utc)
    session.add(Alert(borrower_id=borrower_id, borrower_name=name, alert_type=alert_type, message=message, severity=severity, channel=channel, timestamp=now))
    asyncio.create_task(_dispatch_alert_channels(name, message, severity, channel, alert_type, now))


# --------------------------------------------------------------
#  Seed Demo Data
# --------------------------------------------------------------
async def seed_data():
    ensure_assets_loaded()
    async with AsyncSessionFactory() as session:
        result = await session.execute(select(func.count(Borrower.id)))
        if result.scalar():
            return
    sample = MFI_DF.sample(10, random_state=99)
    now = datetime.datetime.now(datetime.timezone.utc)
    async def _insert_one(i: int, row: pd.Series):
        async with AsyncSessionFactory() as session:
            parts = row["Full Name"].split()
            first = parts[0]; last = " ".join(parts[1:])
            sal = float(row["Current Monthly Salary (USD)"])
            sec = row["Employment Sector"]; rea = row["Common Loan Reason"]
            tr = float(row["Total Previous Loans"]); al = float(row["Active Loans"])
            ob = float(row["Total Outstanding Balance (USD)"])
            am = float(row["Avg Loan Amount (USD)"])
            loan_req = float(row["Avg Loan Amount (USD)"])
            rr = float(row["Historical Return Rate (%)"])
            dp = float(row["Days Past Due (Max)"])
            ms = float(row["MFI Diversity Score"])
            score, label, probs = await score_borrower_async(sal, sec, rea, tr, al, ob, am, rr, dp, ms, loan_req)
            anomaly_eval = evaluate_application_anomalies(salary=sal, total_loans=tr, active_loans=al, outstanding=ob, return_rate=rr, days_due=dp, is_existing_borrower=False, recent_application_count=1, loan_amount=loan_req)
            anomaly_codes = ", ".join(a["code"] for a in anomaly_eval["anomalies"]) if anomaly_eval["anomalies"] else "none"
            status, reason = decide_application(score=score, label=label, anomaly_score=anomaly_eval["anomaly_score"], anomaly_codes=anomaly_codes)
            bid = f"S{i+1:03d}"
            tracking_number = str(uuid.uuid4()) if status != "rejected" else None
            session.add(Borrower(id=bid, full_name=row["Full Name"], first_name=first, last_name=last, salary=sal, employment_sector=sec, job_title=row["Job Title"], total_prev_loans=tr, active_loans=al, outstanding_balance=ob, avg_loan_amount=am, common_loan_reason=rea, return_rate=rr, days_past_due=dp, mfi_diversity_score=ms, risk_score=score, risk_label=label, risk_probability_high=probs.get("High", 0), risk_probability_medium=probs.get("Medium", 0), risk_probability_low=probs.get("Low", 0), loan_amount=loan_req, data_source="mfi_exact", created_at=now))
            session.add(Transaction(borrower_id=bid, type="assessment", amount=loan_req, description=reason, is_anomaly=anomaly_eval["is_anomaly"], anomaly_score=anomaly_eval["anomaly_score"], risk_score_after=score, risk_label_after=label, status=status, tracking_number=tracking_number, timestamp=now))
            for day in range(8, 0, -1):
                ts = now - datetime.timedelta(days=day)
                v = max(0.0, min(100.0, score + random.uniform(-10, 10)))
                session.add(Transaction(borrower_id=bid, type="history", amount=0.0, description="Historical record", is_anomaly=False, anomaly_score=0.0, risk_score_after=round(v, 1), risk_label_after=label, tracking_number=str(uuid.uuid4()), timestamp=ts))
            await session.commit()
            return {"borrower_id": bid, "full_name": row["Full Name"], "risk_score": float(score), "risk_label": str(label)}
    inserted = await asyncio.gather(*[_insert_one(i, row) for i, (_, row) in enumerate(sample.iterrows())])
    inserted = [x for x in inserted if x]
    candidates = [x for x in inserted if x["risk_label"] == "High"] or sorted(inserted, key=lambda x: x["risk_score"], reverse=True)
    blacklist = candidates[:max(1, min(2, len(candidates)))]
    if blacklist:
        async with AsyncSessionFactory() as session:
            for entry in blacklist:
                session.add(BlacklistedUser(borrower_id=entry["borrower_id"], full_name=entry["full_name"], reason="Seeded blacklist: high risk profile", credit_score=max(0.0, 100.0 - entry["risk_score"])))
            await session.commit()
    log.info("Demo portfolio seeded")


# --------------------------------------------------------------
#  Mock Credit Bureau  (simulates external MFI/bank loan data)
#
#  HOW IT WORKS:
#  Each entry represents what OTHER MFIs/banks already know about a borrower.
#  When a borrower submits their national ID, FinanceGuard queries this
#  bureau and MERGES the external data with internal records before scoring.
#  This means a borrower cannot hide loans they took elsewhere.
#
#  Fields per record:
#    active_loans_elsewhere       — open loans at other institutions
#    outstanding_elsewhere        — total balance owed elsewhere (USD)
#    repayment_rate_elsewhere     — their repayment track record elsewhere (%)
#    days_past_due_elsewhere      — worst overdue period recorded elsewhere
#    recent_applications_elsewhere— applications made to other MFIs in last 30 days
#    unsettled_loans_elsewhere    — approved but unpaid loans elsewhere
#    institutions                 — which institutions hold the loans
#    credit_score                 — composite credit rating from bureau (0–100, higher = better)
#    full_name                    — for display/verification purposes
# --------------------------------------------------------------

MOCK_CREDIT_DATA: dict[str, dict] = {
    # ── HIGH RISK: active loans at 2 MFIs, 9 recent applications, poor profile
   "12-654321B34": {
    "full_name": "Panashe Nyawiri",
    "active_loans_elsewhere": 1,
    "outstanding_elsewhere": 300.0,
    "repayment_rate_elsewhere": 98.0,
    "days_past_due_elsewhere": 0,
    "recent_applications_elsewhere": 0,
    "unsettled_loans_elsewhere": 0,
    "institutions": ["Zambuko Trust"],
    "credit_score": 78,
    "risk_flag": "LOW",
    "notes": "One small active loan, good repayment record.",
},
    # ── MEDIUM RISK: one loan elsewhere, mostly on track
    "63-123456A12": {
        "full_name": "Theresa Machache",
        "active_loans_elsewhere": 1,
        "outstanding_elsewhere": 1500.0,
        "repayment_rate_elsewhere": 95.0,
        "days_past_due_elsewhere": 0,
        "recent_applications_elsewhere": 3,
        "unsettled_loans_elsewhere": 1,
        "institutions": ["Untu Capital"],
        "credit_score": 61,
        "risk_flag": "MEDIUM",
        "notes": "Existing loan at Untu Capital; repayment history acceptable.",
    },
    # ── LOW RISK: clean bureau record
    "48-234567B34": {
        "full_name": "Farai Moyo",
        "active_loans_elsewhere": 0,
        "outstanding_elsewhere": 0.0,
        "repayment_rate_elsewhere": 100.0,
        "days_past_due_elsewhere": 0,
        "recent_applications_elsewhere": 1,
        "unsettled_loans_elsewhere": 0,
        "institutions": [],
        "credit_score": 88,
        "risk_flag": "LOW",
        "notes": "No external loans. Clean bureau record.",
    },
    # ── HIGH RISK: severe delinquency, two MFIs chasing
    "22-345678C45": {
        "full_name": "Tatenda Chirwa",
        "active_loans_elsewhere": 3,
        "outstanding_elsewhere": 7800.0,
        "repayment_rate_elsewhere": 40.0,
        "days_past_due_elsewhere": 120,
        "recent_applications_elsewhere": 5,
        "unsettled_loans_elsewhere": 3,
        "institutions": ["CABS MicroFinance", "GetBucks Zimbabwe", "Credsure"],
        "credit_score": 14,
        "risk_flag": "HIGH",
        "notes": "Severely delinquent (120 days). Active at 3 institutions simultaneously.",
    },
    # ── MEDIUM RISK: previously defaulted, now recovering
    "51-456789D56": {
        "full_name": "Rutendo Dube",
        "active_loans_elsewhere": 1,
        "outstanding_elsewhere": 950.0,
        "repayment_rate_elsewhere": 78.0,
        "days_past_due_elsewhere": 30,
        "recent_applications_elsewhere": 2,
        "unsettled_loans_elsewhere": 1,
        "institutions": ["Africa Unity Savings"],
        "credit_score": 49,
        "risk_flag": "MEDIUM",
        "notes": "One prior default 18 months ago. Currently in repayment plan.",
    },
    # ── LOW RISK: long good history, fully settled
    "67-567890E67": {
        "full_name": "Chiedza Mutasa",
        "active_loans_elsewhere": 0,
        "outstanding_elsewhere": 0.0,
        "repayment_rate_elsewhere": 99.0,
        "days_past_due_elsewhere": 0,
        "recent_applications_elsewhere": 0,
        "unsettled_loans_elsewhere": 0,
        "institutions": [],
        "credit_score": 94,
        "risk_flag": "LOW",
        "notes": "Excellent bureau record. All previous loans fully settled on time.",
    },
    # ── HIGH RISK: loan shopping behaviour, high DTI elsewhere
    "29-678901F78": {
        "full_name": "Blessing Ncube",
        "active_loans_elsewhere": 4,
        "outstanding_elsewhere": 12000.0,
        "repayment_rate_elsewhere": 55.0,
        "days_past_due_elsewhere": 60,
        "recent_applications_elsewhere": 12,
        "unsettled_loans_elsewhere": 4,
        "institutions": ["Steward Bank MFI", "MicroKing Finance", "Agribank Zimbabwe", "Locfund"],
        "credit_score": 9,
        "risk_flag": "HIGH",
        "notes": "Loan shopping detected — 12 applications in 30 days. Overexposed across 4 lenders.",
    },
    # ── LOW RISK: new to bureau, no history (first-time borrower)
    "75-789012G89": {
        "full_name": "Simbarashe Gumbo",
        "active_loans_elsewhere": 0,
        "outstanding_elsewhere": 0.0,
        "repayment_rate_elsewhere": 100.0,
        "days_past_due_elsewhere": 0,
        "recent_applications_elsewhere": 1,
        "unsettled_loans_elsewhere": 0,
        "institutions": [],
        "credit_score": 72,
        "risk_flag": "LOW",
        "notes": "First-time applicant. No bureau history found — treated as clean slate.",
    },
    # ── MEDIUM RISK: seasonal borrower, slightly overdue
    "44-890123H90": {
        "full_name": "Mazvita Banda",
        "active_loans_elsewhere": 2,
        "outstanding_elsewhere": 2200.0,
        "repayment_rate_elsewhere": 85.0,
        "days_past_due_elsewhere": 15,
        "recent_applications_elsewhere": 4,
        "unsettled_loans_elsewhere": 1,
        "institutions": ["Zambuko Trust", "Untu Capital"],
        "credit_score": 55,
        "risk_flag": "MEDIUM",
        "notes": "Seasonal borrower pattern. Slightly overdue but consistent repayer historically.",
    },
    # ── HIGH RISK: blacklisted at one institution
    "38-901234I01": {
        "full_name": "Tapiwanashe Mhere",
        "active_loans_elsewhere": 2,
        "outstanding_elsewhere": 5500.0,
        "repayment_rate_elsewhere": 30.0,
        "days_past_due_elsewhere": 180,
        "recent_applications_elsewhere": 7,
        "unsettled_loans_elsewhere": 2,
        "institutions": ["GetBucks Zimbabwe", "Credsure"],
        "credit_score": 5,
        "risk_flag": "HIGH",
        "notes": "Blacklisted at GetBucks Zimbabwe. 180 days overdue. Ongoing legal proceedings.",
    },
}

# National ID format for Zimbabwe: XX-XXXXXXYXX  (e.g. 34-567890I89)
_NATIONAL_ID_RE = re.compile(r"^\d{2}-\d{6}[A-Z]\d{2}$")


def _normalize_national_id(raw_id: str) -> str | None:
    """
    Convert OCR output or user input to canonical XX-XXXXXXYXX format.
    Handles the most common real-world OCR variations from Zimbabwean ID cards.

    Examples accepted:
      "12-654321B34"      → 12-654321B34   (already correct)
      "12654321B34"       → 12-654321B34   (no hyphen)
      "12-654321 B 34"    → 12-654321B34   (spaces around letter)
      "12654321834"       → 12-654321B34   (B OCR'd as 8)
      "12654321"          → None           (truncated — reject, ask user to retype)
    """
    raw = (raw_id or "").strip().upper()
    if not raw:
        return None

    # Fast path: already canonical
    if re.match(r"^\d{2}-\d{6}[A-Z]\d{2}$", raw):
        return raw

    # Strip spaces and hyphens, keep alphanumerics
    cleaned = re.sub(r"[^A-Z0-9]", "", raw)

    # Case 1: 11 chars with letter in position 8 — "12654321B34"
    m = re.match(r"^(\d{2})(\d{6})([A-Z])(\d{2})$", cleaned)
    if m:
        return f"{m.group(1)}-{m.group(2)}{m.group(3)}{m.group(4)}"

    # Case 2: 11 all-digits — letter was OCR'd as a digit
    # Common confusions: I→1, O→0, B→8, S→5, G→6, Z→2
    if re.match(r"^\d{11}$", cleaned):
        digit_to_letter = {"1": "I", "0": "O", "8": "B", "5": "S", "6": "G", "2": "Z"}
        candidate_letter = digit_to_letter.get(cleaned[8])
        if candidate_letter:
            rebuilt = f"{cleaned[:2]}-{cleaned[2:8]}{candidate_letter}{cleaned[9:]}"
            if re.match(r"^\d{2}-\d{6}[A-Z]\d{2}$", rebuilt):
                return rebuilt

    # Anything else (truncated, too short, malformed) — reject cleanly
    return None

def _validate_national_id(national_id: str) -> bool:
    """Returns True if national_id can be normalized to expected format."""
    return _normalize_national_id(national_id) is not None
def _fetch_credit_bureau(national_id: str) -> dict | None:
    """
    Direct-call credit bureau lookup (no HTTP — avoids Flask deadlock).
    Returns the external data dict, or None if no record found.
    In production this would be replaced by an authenticated HTTPS call
    to the actual Zimbabwe Credit Reference Bureau (CRBZ) API.
    """
    return MOCK_CREDIT_DATA.get((national_id or "").strip().upper())


def _merge_external_data(
    *,
    al: float, ob: float, rr: float, dp: float,
    tr: float, recent_assessment_count: int, unsettled_loan_count: int,
    external: dict,
) -> tuple[float, float, float, float, float, int, int]:
    """
    Merge internal FinanceGuard borrower metrics with external credit bureau data.
    """
    ext_al       = float(external.get("active_loans_elsewhere", 0))
    ext_ob       = float(external.get("outstanding_elsewhere", 0))
    ext_rr       = float(external.get("repayment_rate_elsewhere", 100))
    ext_dp       = float(external.get("days_past_due_elsewhere", 0))
    ext_recent   = int(external.get("recent_applications_elsewhere", 0))
    ext_unsettled= int(external.get("unsettled_loans_elsewhere", 0))

    merged_al = al + ext_al
    merged_ob = ob + ext_ob

    total_count = tr + ext_al
    if total_count > 0:
        merged_rr = ((rr * tr) + (ext_rr * ext_al)) / total_count
    else:
        merged_rr = ext_rr

    merged_dp         = max(dp, ext_dp)
    merged_tr         = tr + ext_al                            # full loan history
    # Do NOT merge external recent applications – keep internal count only
    merged_recent     = recent_assessment_count                # internal only
    merged_unsettled  = unsettled_loan_count + ext_unsettled   # total unresolved

    return merged_al, merged_ob, merged_rr, merged_dp, merged_tr, merged_recent, merged_unsettled
# --------------------------------------------------------------
#  Flask Routes
# --------------------------------------------------------------
@app.route("/static/js/pdf.min.mjs")
def serve_file():
    return send_file('static/js/pdf.min.mjs', mimetype='application/javascript')


@app.route("/")
async def index():
    try:
        ensure_assets_loaded()
    except Exception:
        log.warning("ML assets not available; serving UI without model metadata", exc_info=True)
    accuracy = round(META["accuracy"] * 100, 1) if META else None
    return render_template("index.html", model_accuracy=accuracy)


@app.route("/api/credit-bureau/<national_id>", methods=["GET"])
def credit_bureau_mock(national_id: str):
    """
    Look up a single borrower by national ID.
    Test in browser: GET /api/credit-bureau/34-567890I89
    Internal code uses _fetch_credit_bureau() directly (no HTTP, no deadlock).
    """
    nid = (national_id or "").strip().upper()
    if not _validate_national_id(nid):
        return jsonify({"error": "Invalid national ID format. Expected: XX-XXXXXXYXX (e.g. 34-567890I89)"}), 400
    record = MOCK_CREDIT_DATA.get(nid)
    if not record:
        return jsonify({"found": False, "national_id": nid, "message": "No external credit data found — borrower has no bureau record."}), 404
    return jsonify({"found": True, "national_id": nid, **record})


@app.route("/api/credit-bureau", methods=["GET"])
def credit_bureau_list():
    """
    List all borrowers in the mock credit bureau.
    Supports ?risk=HIGH|MEDIUM|LOW and ?search=name filters.
    GET /api/credit-bureau
    GET /api/credit-bureau?risk=HIGH
    GET /api/credit-bureau?search=Panashe
    """
    risk_filter = (request.args.get("risk") or "").strip().upper()
    search_term = (request.args.get("search") or "").strip().lower()
    results = []
    for nid, record in MOCK_CREDIT_DATA.items():
        if risk_filter and record.get("risk_flag", "").upper() != risk_filter:
            continue
        if search_term and search_term not in record.get("full_name", "").lower():
            continue
        results.append({
            "national_id": nid,
            "full_name": record.get("full_name", "Unknown"),
            "risk_flag": record.get("risk_flag", "UNKNOWN"),
            "credit_score": record.get("credit_score", 0),
            "active_loans_elsewhere": record.get("active_loans_elsewhere", 0),
            "outstanding_elsewhere": record.get("outstanding_elsewhere", 0.0),
            "repayment_rate_elsewhere": record.get("repayment_rate_elsewhere", 100.0),
            "days_past_due_elsewhere": record.get("days_past_due_elsewhere", 0),
            "institutions": record.get("institutions", []),
            "notes": record.get("notes", ""),
        })
    results.sort(key=lambda x: x["credit_score"])   # lowest credit score first
    return jsonify({
        "total": len(results),
        "filters": {"risk": risk_filter or "ALL", "search": search_term or None},
        "records": results,
    })


@app.route("/credit-bureau")
async def credit_bureau_page():
    return render_template("credit_bureau.html")


@app.route("/apply")
async def application_page():
    return render_template("application.html")


@app.route("/risk-score")
async def risk_score_page():
    return render_template("risk-score.html")


@app.route("/track")
async def tracking_page():
    return render_template("tracking.html")


@app.route("/dashboard")
async def dashboard():
    return render_template("dashboard/dashboard.html")


@app.route("/login")
async def login_page():
    return render_template("dashboard/index.html")


@app.route("/signup")
async def signup_page():
    return render_template("dashboard/signup.html")


@app.route("/api/signup", methods=["POST"])
async def signup():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip()
    password = data.get("password")
    full_name = (data.get("full_name") or "").strip()
    if not email or not password or not full_name:
        return jsonify({"error": "Full name, email, and password are required."}), 400
    try:
        async with AsyncSessionFactory() as session:
            existing = await session.execute(select(User).where(func.lower(User.email) == email.lower()))
            if existing.scalar_one_or_none():
                return jsonify({"error": "Email already registered."}), 409
            user = User(id=str(uuid.uuid4()), full_name=full_name, email=email, password_hash=generate_password_hash(password))
            session.add(user)
            await session.commit()
            return jsonify({"success": True, "user": user.to_dict()})
    except Exception:
        log.exception("signup() error")
        return jsonify({"error": "Unable to create account."}), 500


@app.route("/api/login", methods=["POST"])
async def login():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip()
    password = data.get("password")
    if not email or not password:
        return jsonify({"error": "Email and password are required."}), 400
    try:
        async with AsyncSessionFactory() as session:
            result = await session.execute(select(User).where(func.lower(User.email) == email.lower()))
            user = result.scalar_one_or_none()
            if not user or not check_password_hash(user.password_hash, password):
                return jsonify({"error": "Invalid credentials."}), 401
            return jsonify({"success": True, "user": user.to_dict()})
    except Exception:
        log.exception("login() error")
        return jsonify({"error": "Unable to authenticate."}), 500


@app.route('/api/parse-payslip', methods=['POST'])
def parse_payslip():
    try:
        data = request.get_json()
        text = data.get("text", "")
        print(text)

        if not text:
            return jsonify({"error": "No text provided"}), 400

        # === YOUR EXTRACTION LOGIC ===
        employee_name = extract_name(text)
        net_pay = extract_net_pay(text)
        national_id = _extract_national_id(text)
        department = extract_department(text)
        position = extract_position(text)

        # 🔥 DEBUG LOG (VERY IMPORTANT)
        print("Parsed:", employee_name, net_pay, national_id, department, position)

        # ✅ ALWAYS RETURN SUCCESS RESPONSE
        return jsonify({
            "employee_name": employee_name or "",
            "net_pay": net_pay or 0,
            "salary": net_pay or 0,
            "national_id": national_id or "",
            "department": department or "",
            "position": position or ""
        })

    except Exception as e:
        print("PAYSPLIP ERROR:", str(e))

        # ✅ ALWAYS RETURN ERROR RESPONSE
        return jsonify({
            "error": str(e)
        }), 500
    def _extract_labeled_text(text: str, labels: list[str]) -> str | None:
        label = "|".join(labels)
        pattern = rf"(?:{label})\s*[:\-]\s*([A-Za-z0-9&/().,'\-\s]+?)(?=\s{{2,}}|\n|$)"
        match = re.search(pattern, text, re.IGNORECASE)
        if not match:
            return None
        return re.sub(r"\s+", " ", match.group(1)).strip() or None

    def _extract_net_pay(text: str) -> float | None:
        patterns = [r"(?:net\s*pay|net\s*salary|net\s*amount|net\s*earnings|take[-\s]*home\s*pay)[^\d]*(\d[\d,\.]+)", r"(?:net)[^\d]*(\d[\d,\.]+)"]
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if not match:
                continue
            candidate = re.sub(r"[^\d.]", "", match.group(1))
            if not candidate:
                continue
            try:
                value = float(candidate)
            except ValueError:
                continue
            if 10 <= value < 1000000:
                return value
        return None
def _extract_national_id(text: str) -> str | None:
    """Extract Zimbabwe National ID from payslip text."""
    if not text:
        return None

    text = text.upper()

    # Look for "NATIONAL ID:" label and capture the following characters
    match = re.search(r"NATIONAL\s*ID\s*:\s*([\d\s-]+[A-Z][\d\s-]*)", text)
    if match:
        raw = match.group(1).strip()
        # Remove all spaces (but keep hyphens if any)
        raw = re.sub(r"\s+", "", raw)   # "12-654321B34" or "12654321B34"
        normalized = _normalize_national_id(raw)
        if normalized:
            return normalized

    # Fallback: generic patterns
    patterns = [
        r"\b\d{2}-\d{6}\s*[A-Z]\s*\d{2}\b",
        r"\b\d{2}\d{6}[A-Z]\d{2}\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            raw = match.group(0)
            raw = re.sub(r"\s+", "", raw)
            normalized = _normalize_national_id(raw)
            if normalized:
                return normalized

    return None

@app.route("/api/alerts")
async def get_alerts():
    try:
        async with AsyncSessionFactory() as session:
            try:
                limit = min(50, max(1, int(request.args.get("limit", 10))))
                offset = max(0, int(request.args.get("offset", 0)))
            except ValueError:
                limit = 10
                offset = 0
            include_read = request.args.get("include_read", "0") in {"1", "true", "True"}
            query = select(Alert).order_by(desc(Alert.timestamp)).offset(offset).limit(limit)
            if not include_read:
                query = query.where(Alert.is_read == False)
            result = await session.execute(query)
            alerts = result.scalars().all()
            return jsonify([a.to_dict() for a in alerts])
    except Exception:
        log.exception("get_alerts() DB error")
        return jsonify([]), 200


@app.route("/api/transactions")
async def get_transactions():
    try:
        async with AsyncSessionFactory() as session:
            result = await session.execute(select(Transaction, Borrower).join(Borrower, Borrower.id == Transaction.borrower_id, isouter=True).order_by(desc(Transaction.timestamp)).limit(50))
            rows = result.all()
            payload = []
            for tx, borrower in rows:
                client = borrower.full_name if borrower else tx.borrower_id
                payload.append({"id": tx.id, "client": client, "amount": float(tx.amount or 0.0), "risk": max(0.0, min(1.0, float(tx.risk_score_after or 0.0) / 100.0)), "status": tx.status or "processing", "tracking_number": tx.tracking_number, "date": tx.timestamp.isoformat()})
            return jsonify(payload)
    except Exception:
        log.exception("get_transactions() error")
        return jsonify([]), 200


@app.route("/api/applications")
async def get_applications():
    try:
        page = max(1, int(request.args.get("page", 1)))
    except (TypeError, ValueError):
        page = 1
    try:
        size = min(10, max(5, int(request.args.get("size", 10))))
    except (TypeError, ValueError):
        size = 10
    risk_filter = (request.args.get("risk") or "all").strip().lower()
    if risk_filter not in {"all", "high", "medium", "low"}:
        risk_filter = "all"
    offset = (page - 1) * size
    try:
        async with AsyncSessionFactory() as session:
            query = select(Transaction, Borrower).join(Borrower, Borrower.id == Transaction.borrower_id, isouter=True).where(Transaction.type == "assessment")
            if risk_filter != "all":
                query = query.where(func.lower(Transaction.risk_label_after) == risk_filter)
            query = query.order_by(desc(Transaction.timestamp)).offset(offset).limit(size + 1)
            result = await session.execute(query)
            rows = result.all()
            has_more = len(rows) > size
            payload = []
            for tx, borrower in rows[:size]:
                decision_reason, _ = _split_transaction_description(tx.description)
                payload.append({"tracking_number": tx.tracking_number, "status": tx.status or "processing", "decision_reason": decision_reason, "amount": float(tx.amount or 0.0), "risk_score": tx.risk_score_after, "risk_label": tx.risk_label_after, "borrower_id": borrower.id if borrower else tx.borrower_id, "borrower_name": borrower.full_name if borrower else tx.borrower_id, "timestamp": tx.timestamp.isoformat()})
            return jsonify({"records": payload, "page": page, "size": size, "has_more": has_more, "risk": risk_filter})
    except Exception:
        log.exception("get_applications() error")
        return jsonify({"error": "Unable to load applications"}), 500


@app.route("/api/applications/<tracking_number>/status", methods=["POST"])
async def update_application_status(tracking_number):
    return jsonify({"error": "Manual status updates are disabled. Decisions are automatic."}), 403


@app.route("/api/application-status/<tracking_number>")
async def application_status(tracking_number):
    try:
        async with AsyncSessionFactory() as session:
            result = await session.execute(select(Transaction, Borrower).join(Borrower, Borrower.id == Transaction.borrower_id, isouter=True).where(Transaction.tracking_number == tracking_number))
            row = result.first()
            if not row:
                return jsonify({"error": "Tracking number not found"}), 404
            tx, borrower = row
            decision_reason, _ = _split_transaction_description(tx.description)
            
            # Use anomaly_score for consistency with admin dashboard
            display_score = tx.anomaly_score if (tx.anomaly_score and tx.anomaly_score > 0) else tx.risk_score_after
            if display_score >= 70:
                display_label = "High"
            elif display_score >= 40:
                display_label = "Medium"
            else:
                display_label = "Low"
            
            return jsonify({
                "tracking_number": tx.tracking_number,
                "status": tx.status or "processing",
                "decision_reason": decision_reason,
                "risk_score": round(float(display_score), 1),
                "risk_label": display_label,
                "borrower": borrower.full_name if borrower else tx.borrower_id,
                "timestamp": tx.timestamp.isoformat(),
                "payout_details": _get_stored_payout_details(tx),
                "is_anomaly": tx.is_anomaly,
            })
    except Exception:
        log.exception("application_status() error")
        return jsonify({"error": "Unable to fetch status"}), 500


@app.route("/api/application-status/<tracking_number>/payout-details", methods=["POST"])
async def save_payout_details(tracking_number):
    data = request.get_json(silent=True) or {}
    try:
        payout_details = _validate_payout_details(data.get("channel", ""), data.get("details") or {})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    try:
        async with AsyncSessionFactory() as session:
            result = await session.execute(select(Transaction).where(Transaction.tracking_number == tracking_number))
            tx = result.scalar_one_or_none()
            if not tx:
                return jsonify({"error": "Tracking number not found"}), 404
            if (tx.status or "").lower() != "approved":
                return jsonify({"error": "Payout details can only be saved for approved applications."}), 400
            decision_reason, _ = _split_transaction_description(tx.description)
            tx.description = decision_reason
            tx.deposit_channel = payout_details["channel"]
            tx.deposit_details = json.dumps(payout_details)
            tx.deposit_updated_at = now_local()
            await session.commit()
            return jsonify({"success": True, "tracking_number": tx.tracking_number, "payout_details": _get_stored_payout_details(tx)})
    except Exception:
        log.exception("save_payout_details() error")
        return jsonify({"error": "Unable to save payout details"}), 500


@app.route("/api/blacklist", methods=["GET"])
async def get_blacklist():
    try:
        async with AsyncSessionFactory() as session:
            result = await session.execute(select(BlacklistedUser).order_by(desc(BlacklistedUser.added_at)))
            entries = result.scalars().all()
            return jsonify([e.to_dict() for e in entries])
    except Exception:
        log.exception("get_blacklist() error")
        return jsonify([]), 200


@app.route("/api/blacklist", methods=["POST"])
async def add_to_blacklist():
    data = request.get_json(silent=True) or {}
    borrower_id = data["borrower_id"]
    full_name = data["full_name"]
    reason = data["reason"]
    credit_score = data["credit_score"]
    if not full_name or not reason:
        return jsonify({"error": "Full name and reason are required."}), 400
    try:
        async with AsyncSessionFactory() as session:
            entry = BlacklistedUser(borrower_id=borrower_id, full_name=full_name, reason=reason, credit_score=float(credit_score or 0))
            session.add(entry)
            await session.commit()
            return jsonify({"success": True, "entry": entry.to_dict()})
    except Exception:
        log.exception("add_to_blacklist() error")
        return jsonify({"error": "Unable to save entry."}), 500


@app.route("/api/alerts/mark-read", methods=["POST"])
async def mark_read():
    try:
        async with AsyncSessionFactory() as session:
            await session.execute(update(Alert).where(Alert.is_read == False).values(is_read=True))
            await session.commit()
            return jsonify({"success": True})
    except Exception:
        log.exception("mark_read() DB error")
        return jsonify({"error": "Database unavailable"}), 503


@app.route("/api/alerts/<int:alert_id>/mark-read", methods=["POST"])
async def mark_single_read(alert_id: int):
    try:
        async with AsyncSessionFactory() as session:
            result = await session.execute(select(Alert).where(Alert.id == alert_id))
            alert = result.scalar_one_or_none()
            if not alert:
                return jsonify({"error": "Alert not found"}), 404
            alert.is_read = True
            await session.commit()
            return jsonify({"success": True})
    except Exception:
        log.exception("mark_single_read() DB error")
        return jsonify({"error": "Database unavailable"}), 503


@app.route("/api/stats")
async def get_stats():
    ensure_assets_loaded()
    async def _count_all():
        async with AsyncSessionFactory() as s:
            r = await s.execute(select(func.count(Borrower.id)))
            return r.scalar() or 0
    async def _count_label(label: str):
        async with AsyncSessionFactory() as s:
            r = await s.execute(select(func.count(Borrower.id)).where(Borrower.risk_label == label))
            return r.scalar() or 0
    async def _count_unread():
        async with AsyncSessionFactory() as s:
            r = await s.execute(select(func.count(Alert.id)).where(Alert.is_read == False))
            return r.scalar() or 0
    async def _count_assessments():
        async with AsyncSessionFactory() as s:
            r = await s.execute(select(func.count(Transaction.id)).where(Transaction.type == "assessment"))
            return r.scalar() or 0
    async def _avg_risk():
        async with AsyncSessionFactory() as s:
            r = await s.execute(select(func.avg(Borrower.risk_score)))
            val = r.scalar()
            return round(float(val), 1) if val is not None else 0.0
    async def _total_salary():
        async with AsyncSessionFactory() as s:
            r = await s.execute(select(func.sum(Borrower.salary)))
            val = r.scalar()
            return round(float(val), 2) if val is not None else 0.0
    try:
        total, high, medium, low, unread, assessments, avg_r, tot_sal = await asyncio.gather(
            _count_all(), _count_label("High"), _count_label("Medium"), _count_label("Low"),
            _count_unread(), _count_assessments(), _avg_risk(), _total_salary()
        )
        log.info(f"Stats requested: total={total}, high={high}, medium={medium}, low={low}, avg={avg_r}")
    except Exception:
        log.exception("get_stats() DB error")
        return jsonify({"error": "Database unavailable"}), 503
    response = jsonify({
        "total_borrowers": total, "high": high, "medium": medium, "low": low,
        "unread_alerts": unread, "total_assessments": assessments,
        "avg_risk_score": avg_r, "total_salary_portfolio": tot_sal,
        "model_accuracy": round(META["accuracy"] * 100, 1) if META else 99.2,
    })
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response


@app.route("/api/portfolio-risk-trend")
async def risk_trend():
    try:
        async with AsyncSessionFactory() as session:
            result = await session.execute(
                select(func.date(Transaction.timestamp).label("day"), func.avg(Transaction.risk_score_after).label("avg_risk"), func.count(Transaction.id).label("count"))
                .where(Transaction.type == "assessment")
                .group_by(func.date(Transaction.timestamp))
                .order_by(desc(func.date(Transaction.timestamp)))
                .limit(14)
            )
            rows = result.all()
            trend_data = []
            for r in reversed(rows):
                if r.avg_risk is not None:
                    trend_data.append({"day": str(r.day), "avg_risk": round(float(r.avg_risk), 1), "count": r.count})
            if not trend_data:
                avg_result = await session.execute(select(func.avg(Borrower.risk_score)))
                current_avg = round(float(avg_result.scalar() or 0), 1)
                today = datetime.datetime.now().date()
                trend_data = [{"day": str(today), "avg_risk": current_avg, "count": 0}]
            return jsonify(trend_data)
    except Exception:
        log.exception("risk_trend() DB error")
        return jsonify([]), 200


@app.route("/api/assess", methods=["POST"])
async def assess():
    data = request.get_json(silent=True) or {}
    first_name = data['first_name'].split(" ")[0].strip()
    last_name = data['first_name'].split(" ")[1].strip()
    payslip_salary = data['payslip_salary']
    payslip_name = (data.get("payslip_name") or "").strip()
    payslip_department = (data.get("payslip_department") or "").strip()
    payslip_position = (data.get("payslip_position") or "").strip()
    national_id = (data.get("national_id") or "").strip().upper()
    app.logger.info(f"Raw national_id from OCR: {national_id}")
    if national_id:
        normalized = _normalize_national_id(national_id)
        if not normalized:
            return jsonify({"error": "Invalid national ID format. Expected: XX-XXXXXXYXX (e.g. 34-567890I89)"}), 400
        national_id = normalized
    if not data:
        return jsonify({"error": "Invalid JSON body."}), 400
    amount_value = data['amount']
    if amount_value is None:
        return jsonify({"error": "Loan amount is required."}), 400
    try:
        loan_amount = _parse_float(amount_value, "amount", min_value=0.01)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    loan_reason = (data.get("reason") or "").strip()
    if not loan_reason:
        return jsonify({"error": "Loan reason is required."}), 400
    try:
        ensure_assets_loaded()
    except Exception as exc:
        return jsonify({"error": str(exc)}), 503    
    if payslip_salary:
        try:
            salary = _parse_float(payslip_salary, "payslip_salary", min_value=0.01)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
    else:
        try:
            salary = _parse_float(data.get("salary"), "salary", min_value=0.01)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
    if not first_name or not last_name:
        return jsonify({"error": "First name and last name are required."}), 400
    full_name = f"{first_name} {last_name}"
    if payslip_name and not _names_match(payslip_name, full_name):
        return jsonify({"error": "User name and name from payslip do not match."}), 400

    app.logger.info(f"Normalised national_id: {national_id}")
    now = datetime.datetime.now(datetime.timezone.utc)
    frequent_window_start = now - datetime.timedelta(days=FREQUENT_APPLICATION_WINDOW_DAYS)
    existing_borrower_id = None
    aggregated_total_loans = 0
    aggregated_recent_applications = 0
    pending_unpaid_loans = 0
    aggregated_outstanding_balance = 0.0
    borrower_snapshot = None
    async with AsyncSessionFactory() as check_session:
        existing_result = await check_session.execute(select(Borrower).where(func.lower(Borrower.full_name) == full_name.lower()))
        existing_record = existing_result.scalar_one_or_none()
        if existing_record:
            existing_borrower_id = existing_record.id
            borrower_snapshot = {"employment_sector": existing_record.employment_sector, "job_title": existing_record.job_title, "return_rate": existing_record.return_rate, "days_past_due": existing_record.days_past_due, "mfi_diversity_score": existing_record.mfi_diversity_score, "outstanding_balance": existing_record.outstanding_balance}
            total_result = await check_session.execute(select(func.count(Transaction.id)).where(Transaction.borrower_id == existing_record.id, Transaction.type == "assessment"))
            aggregated_total_loans = int(total_result.scalar() or 0)
            recent_result = await check_session.execute(select(func.count(Transaction.id)).where(Transaction.borrower_id == existing_record.id, Transaction.type == "assessment", Transaction.timestamp >= frequent_window_start))
            aggregated_recent_applications = int(recent_result.scalar() or 0)
            approved_result = await check_session.execute(select(func.count(Transaction.id)).where(Transaction.borrower_id == existing_record.id, Transaction.type == "assessment", Transaction.status == "approved"))
            pending_unpaid_loans = int(approved_result.scalar() or 0)
            outstanding_result = await check_session.execute(select(func.coalesce(func.sum(Transaction.amount), 0.0)).where(Transaction.borrower_id == existing_record.id, Transaction.type == "assessment", Transaction.status == "approved"))
            aggregated_outstanding_balance = float(outstanding_result.scalar() or 0.0)
    mfi_row, match_type = lookup_mfi(first_name, last_name)
    if mfi_row:
        sec = mfi_row.get("Employment Sector", "Unknown")
        rea = mfi_row.get("Common Loan Reason", "Emergency")
        tr = float(mfi_row.get("Total Previous Loans", 0))
        al = float(mfi_row.get("Active Loans", 0))
        ob = float(mfi_row.get("Total Outstanding Balance (USD)", 0))
        am = float(mfi_row.get("Avg Loan Amount (USD)", 0))
        rr = float(mfi_row.get("Historical Return Rate (%)", 100))
        dp = float(mfi_row.get("Days Past Due (Max)", 0))
        ms = float(mfi_row.get("MFI Diversity Score", 1))
        job = mfi_row.get("Job Title", "Unknown")
        data_source = f"mfi_{match_type}"
    else:
        sec = payslip_department or "Unknown"
        rea = loan_reason
        tr, al = 0.0, 0.0
        ob, am = 0.0, 0.0
        rr, dp = 100.0, 0.0
        ms = 1.0
        job = payslip_position or "Unknown"
        data_source = "user_input"
        match_type = "user_input"
    borrower_exists = existing_borrower_id is not None
    if borrower_exists:
        tr = float(aggregated_total_loans)
        al = float(pending_unpaid_loans)
        ob = float(aggregated_outstanding_balance or (borrower_snapshot.get("outstanding_balance") if borrower_snapshot else 0.0) or ob)
        if borrower_snapshot:
            sec = borrower_snapshot.get("employment_sector") or sec
            job = borrower_snapshot.get("job_title") or job
            rr = float(borrower_snapshot.get("return_rate") or rr)
            dp = float(borrower_snapshot.get("days_past_due") or dp)
            ms = float(borrower_snapshot.get("mfi_diversity_score") or ms)
        data_source = "db_record"
        match_type = "db_record"
    db_total_loans = float(tr) if borrower_exists else 0.0
    db_active_loans = float(al) if borrower_exists else 0.0
    db_outstanding_balance = float(ob) if borrower_exists else 0.0
    unsettled_loan_count = int(pending_unpaid_loans) if borrower_exists else 0
    recent_assessment_count = aggregated_recent_applications
    # ── Compute internal‑only risk score (before merging external data) ──
    internal_score, internal_label, internal_probs = await score_borrower_async(
        salary, sec, loan_reason,
        db_total_loans,           # total previous loans (local)
        db_active_loans,          # active loans (local)
        db_outstanding_balance,   # outstanding balance (local)
        am,                       # avg loan amount (local)
        rr,                       # return rate (local)
        dp,                       # days past due (local)
        ms,                       # mfi diversity score (local)
        loan_amount               # requested loan amount
    )
    # Store the internal scores for later use in the JSON response
    internal_risk_score = internal_score
    internal_risk_label = internal_label
    # ── Credit bureau lookup & merge ─────────────────────────────────────────
    # Snapshot internal-only values BEFORE merge (for comparison display)
    internal_al = al
    internal_ob = ob
    internal_recent = recent_assessment_count
    internal_unsettled = unsettled_loan_count

    external_credit_data = None
    used_external_data = False
    external_institutions: list[str] = []
    if national_id:
        external_credit_data = _fetch_credit_bureau(national_id)
        if external_credit_data:
            used_external_data = True
            external_institutions = external_credit_data.get("institutions", [])
        al, ob, rr, dp, tr, recent_assessment_count, unsettled_loan_count = _merge_external_data(
    al=al, ob=ob, rr=rr, dp=dp, tr=tr,
    recent_assessment_count=recent_assessment_count,
    unsettled_loan_count=unsettled_loan_count,
    external=external_credit_data,
)            
        log.info(f"Credit bureau merge for {national_id}: al={al}, ob={ob}, rr={rr:.1f}, dp={dp}")

    score, label, probs = await score_borrower_async(salary, sec, loan_reason, tr, al, ob, am, rr, dp, ms, loan_amount)
    area_feedback: dict[str, list[dict[str, str]]] = {"failed": [], "passed": []}

    # ── XAI explanation (runs in thread pool to avoid blocking) ──────────────
    feature_df = _build_features(salary, sec, loan_reason, tr, al, ob, am, rr, dp, ms, loan_amount)
    feature_array = feature_df.iloc[0].values
    xai_explanation = await asyncio.get_running_loop().run_in_executor(
        ML_EXECUTOR, generate_xai_explanation, feature_array
    )

    # ── Internal-only anomaly score (pre-merge, for comparison display) ──────
    internal_anomaly_eval = evaluate_application_anomalies(
        salary=salary, total_loans=db_total_loans, active_loans=internal_al,
        outstanding=internal_ob, return_rate=rr, days_due=dp,
        is_existing_borrower=bool(existing_borrower_id),
        recent_application_count=internal_recent + 1,
        loan_amount=loan_amount, unsettled_loan_count=internal_unsettled,
    )
    try:
        async with AsyncSessionFactory() as session:
            # Use internal recent applications for anomaly detection
            internal_window = aggregated_recent_applications + 1
            anomaly_eval = evaluate_application_anomalies(
                salary=salary, total_loans=tr, active_loans=al, outstanding=ob, return_rate=rr,
                days_due=dp, is_existing_borrower=bool(existing_borrower_id),
                recent_application_count=internal_window,  # ← internal only
                loan_amount=loan_amount,
                unsettled_loan_count=unsettled_loan_count   # keep merged unsettled? or internal? decide
            )            
            risk_adjustment = min(20.0, float(unsettled_loan_count) * 8.0)
            if risk_adjustment > 0:
                score = min(100.0, score + risk_adjustment)
            # Define the window count for user feedback (internal only)
            recent_application_window = aggregated_recent_applications + 1

            area_context = {
                "salary": salary,
                "loan_amount": loan_amount,
                "total_loans": tr,
                "active_loans": al,
                "outstanding": ob,
                "return_rate": rr,
                "days_due": dp,
                "recent_application_count": recent_application_window,   # now defined
                "unsettled_loan_count": unsettled_loan_count,
                "is_existing_borrower": bool(existing_borrower_id)
            }
            area_feedback = _build_area_feedback(anomalies=anomaly_eval["anomalies"], context=area_context, label=label, score=score)
            anomaly_codes = ", ".join(a["code"] for a in anomaly_eval["anomalies"]) if anomaly_eval["anomalies"] else "none"
            decision_status, decision_reason = decide_application(score=score, label=label, anomaly_score=anomaly_eval["anomaly_score"], anomaly_codes=anomaly_codes)            # ============= FIX: Determine display score for consistency =============
            display_score = score
            if display_score >= 70:
                display_label = "High"
            elif display_score >= 40:
                display_label = "Medium"
            else:
                display_label = "Low"
            
            frontend_risk_score = _boost_rejected_anomaly_risk_score(score=score, anomaly_score=anomaly_eval["anomaly_score"], decision_status=decision_status, anomaly_codes=anomaly_codes)
            frontend_risk_label = "High" if frontend_risk_score != round(float(score), 1) else label
            
            if not existing_borrower_id:
                bid = "B" + uuid.uuid4().hex[:6].upper()
            else:
                bid = existing_borrower_id
            
            # Store DISPLAY score in database (matches admin dashboard)
            if existing_borrower_id:
                await session.execute(update(Borrower).where(Borrower.id == existing_borrower_id).values(
                    salary=salary, employment_sector=sec, job_title=job, total_prev_loans=db_total_loans,
                    active_loans=db_active_loans, outstanding_balance=db_outstanding_balance, avg_loan_amount=am,
                    common_loan_reason=loan_reason, return_rate=rr, days_past_due=dp, mfi_diversity_score=ms,
                    risk_score=display_score, risk_label=display_label,
                    risk_probability_high=probs.get("High", 0), risk_probability_medium=probs.get("Medium", 0),
                    risk_probability_low=probs.get("Low", 0), loan_amount=loan_amount
                ))
                log.info(f"Updated borrower {existing_borrower_id} with risk_score={display_score}, risk_label={display_label}")
            else:
                session.add(Borrower(
                    id=bid, full_name=full_name, first_name=first_name, last_name=last_name,
                    salary=salary, employment_sector=sec, job_title=job, total_prev_loans=db_total_loans,
                    active_loans=db_active_loans, outstanding_balance=db_outstanding_balance, avg_loan_amount=am,
                    common_loan_reason=loan_reason, return_rate=rr, days_past_due=dp, mfi_diversity_score=ms,
                    risk_score=display_score, risk_label=display_label,
                    risk_probability_high=probs.get("High", 0), risk_probability_medium=probs.get("Medium", 0),
                    risk_probability_low=probs.get("Low", 0), loan_amount=loan_amount,
                    data_source=data_source, created_at=now
                ))
                log.info(f"Created new borrower {bid} with risk_score={display_score}, risk_label={display_label}")
            
            tracking_number = _generate_tracking_number() if decision_status == "approved" else None
            
            # Store DISPLAY score in transaction
            session.add(Transaction(
                borrower_id=bid, type="assessment", amount=loan_amount, description=decision_reason,
                is_anomaly=anomaly_eval["is_anomaly"], anomaly_score=anomaly_eval["anomaly_score"],
                risk_score_after=display_score, risk_label_after=display_label,
                status=decision_status, tracking_number=tracking_number, timestamp=now
            ))
            
            if label == "High":
                msg = f"{full_name} assessed as HIGH RISK (score {score:.1f}/100). Immediate review recommended."
                msg = _append_area_summary(msg, area_feedback)
                await create_alert(session, bid, full_name, "HIGH_RISK", msg, "CRITICAL", "SMS,Email,Dashboard")
            elif label == "Medium":
                msg = f"{full_name} scored MEDIUM RISK ({score:.1f}/100). Enhanced monitoring advised."
                msg = _append_area_summary(msg, area_feedback)
                await create_alert(session, bid, full_name, "MEDIUM_RISK", msg, "HIGH", "Dashboard")
            if anomaly_eval["is_anomaly"]:
                anomaly_msg = f"{full_name} triggered {len(anomaly_eval['anomalies'])} anomaly checks (score {anomaly_eval['anomaly_score']:.1f}/100): {anomaly_codes}."
                anomaly_msg = _append_area_summary(anomaly_msg, area_feedback)
                severity = "CRITICAL" if anomaly_eval["anomaly_score"] >= 40 else "HIGH"
                await create_alert(session, bid, full_name, "ANOMALY_DETECTED", anomaly_msg, severity, "Email,Dashboard")
            if anomaly_eval.get("notifications"):
                new_user_message = f"{full_name} is a new borrower profile. No prior loan history was found, so the application was logged as a notification only."
                await create_alert(session, bid, full_name, "NEW_USER_NOTIFICATION", new_user_message, "INFO", "Dashboard")
            await session.commit()
    except Exception as exc:
        log.exception("assess() DB error")
        return jsonify({"error": "Database unavailable"}), 503
    
    decision_reason_base = decision_reason
    user_feedback_message = _format_user_area_message(area_feedback)
    if decision_status == "rejected" and user_feedback_message:
        decision_reason = f"{decision_reason_base}\n\n{user_feedback_message}"
    
    # Return DISPLAY score to user
    return jsonify({
        "success": True, "borrower_id": bid, "full_name": full_name, "salary": salary,
        "internal_risk_score": internal_risk_score,
        "internal_risk_label": internal_risk_label,
            "payslip_data": {
            "name": payslip_name,
            "salary": salary,
            "national_id": national_id,          # from the request (normalised)
            "department": payslip_department,
            "position": payslip_position
        },
        "loan_amount": loan_amount, "loan_reason": loan_reason, "tracking_number": tracking_number,
        "decision_status": decision_status, "decision_reason": decision_reason,
        "decision_reason_base": decision_reason_base,
        "decision_feedback": {"message": user_feedback_message, "failed": area_feedback.get("failed", []), "passed": area_feedback.get("passed", [])},
        "risk_score": round(float(display_score), 1),
        "risk_label": display_label,
        "risk_score_boosted": frontend_risk_score,
        "risk_label_boosted": frontend_risk_label,
        "probabilities": {k: round(v * 100, 1) for k, v in probs.items()},
        "data_source": data_source, "match_type": match_type,
        "anomaly_detection": anomaly_eval,
        "internal_anomaly_detection": {
            "anomaly_score": internal_anomaly_eval["anomaly_score"],
            "is_anomaly": internal_anomaly_eval["is_anomaly"],
            "anomalies": internal_anomaly_eval["anomalies"],
        },
        "notifications": anomaly_eval.get("notifications", []),
        "area_feedback": area_feedback,
        "xai_explanation": xai_explanation,
        "credit_bureau": {
            "used_external_data": used_external_data,
            "national_id": national_id if used_external_data else None,
            "institutions": external_institutions,
            "external_active_loans": int(external_credit_data.get("active_loans_elsewhere", 0)) if external_credit_data else 0,
            "external_outstanding": float(external_credit_data.get("outstanding_elsewhere", 0)) if external_credit_data else 0.0,
        },
        "mfi_details": {"employment_sector": sec, "job_title": job, "total_prev_loans": tr,
        "active_loans": al, "outstanding": ob, "return_rate": rr, "days_past_due": dp,
        "loan_reason": loan_reason, "requested_amount": loan_amount}
    })


@app.route('/health')
def health():
    return jsonify({'status': 'healthy', 'service': 'financeGard'})


@app.route('/protected')
@token_required
def protected(current_user):
    return jsonify({'message': 'This is a protected route!', 'user': current_user})