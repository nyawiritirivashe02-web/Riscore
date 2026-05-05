"""
Borrower Profile Analytics API Routes
Endpoints for borrower profiles, loan history, and risk evolution.
"""

from flask import jsonify, request
from financeGuard import app, db
from financeGuard.auth.token import token_required
from financeGuard.models.models import Borrower, Transaction, Alert
from sqlalchemy import select, func, desc, and_
from datetime import datetime, timedelta
from financeGuard.models.models import now_local
import logging

log = logging.getLogger(__name__)


@app.route('/api/dashboard/borrower/<borrower_id>', methods=['GET'])
@token_required
async def get_borrower_profile(current_user, borrower_id):
    """Get detailed borrower profile."""
    try:
        async_session = db.AsyncSessionFactory()
        
        borrower = await async_session.get(Borrower, borrower_id)
        if not borrower:
            await async_session.close()
            return jsonify({"error": "Borrower not found"}), 404
        
        await async_session.close()
        
        return jsonify({
            "id": str(borrower.id),
            "full_name": borrower.full_name,
            "national_id": borrower.national_id,
            "email": borrower.email or "",
            "phone": borrower.phone or "",
            "loan_amount": float(borrower.loan_amount or 0),
            "monthly_salary": float(borrower.monthly_salary or 0),
            "dti_ratio": float(borrower.dti_ratio or 0),
            "risk_score": float(borrower.risk_score or 0),
            "risk_label": borrower.risk_label,
            "decision_status": borrower.decision_status,
            "created_at": borrower.created_at.isoformat() if borrower.created_at else "",
            "decision_made_at": borrower.decision_made_at.isoformat() if borrower.decision_made_at else "",
            "credit_bureau_data": borrower.credit_bureau_data or {},
            "is_blacklisted": borrower.is_blacklisted or False,
            "is_reviewed": borrower.is_reviewed or False
        })
    except Exception as e:
        log.error(f"Error fetching borrower profile: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/dashboard/borrower/<borrower_id>/loan-history', methods=['GET'])
@token_required
async def get_borrower_loan_history(current_user, borrower_id):
    """
    Get borrower's loan history timeline.
    Returns historical loan applications and their status.
    """
    try:
        async_session = db.AsyncSessionFactory()
        
        # For now, return current application
        # TODO: Implement full loan history tracking with Transaction table
        
        borrower = await async_session.get(Borrower, borrower_id)
        if not borrower:
            await async_session.close()
            return jsonify({"error": "Borrower not found"}), 404
        
        history = [{
            "id": str(borrower.id),
            "loan_amount": float(borrower.loan_amount or 0),
            "status": borrower.decision_status,
            "date": borrower.created_at.isoformat() if borrower.created_at else "",
            "risk_score": float(borrower.risk_score or 0)
        }]
        
        await async_session.close()
        
        return jsonify({"data": history})
    except Exception as e:
        log.error(f"Error fetching loan history: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/dashboard/borrower/<borrower_id>/risk-evolution', methods=['GET'])
@token_required
async def get_borrower_risk_evolution(current_user, borrower_id):
    """
    Get risk score evolution over time for a borrower.
    Shows how risk score has changed across multiple assessments.
    """
    try:
        async_session = db.AsyncSessionFactory()
        
        borrower = await async_session.get(Borrower, borrower_id)
        if not borrower:
            await async_session.close()
            return jsonify({"error": "Borrower not found"}), 404
        
        # Currently, we only have one assessment per borrower
        # TODO: Implement historical risk score tracking
        
        evolution = [{
            "date": borrower.created_at.isoformat() if borrower.created_at else "",
            "risk_score": float(borrower.risk_score or 0),
            "risk_label": borrower.risk_label
        }]
        
        await async_session.close()
        
        return jsonify({"data": evolution})
    except Exception as e:
        log.error(f"Error fetching risk evolution: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/dashboard/borrower/<borrower_id>/credit-bureau-comparison', methods=['GET'])
@token_required
async def get_credit_bureau_comparison(current_user, borrower_id):
    """
    Get side-by-side comparison of internal vs credit bureau data.
    """
    try:
        async_session = db.AsyncSessionFactory()
        
        borrower = await async_session.get(Borrower, borrower_id)
        if not borrower:
            await async_session.close()
            return jsonify({"error": "Borrower not found"}), 404
        
        # Internal data
        internal_data = {
            "name": borrower.full_name,
            "salary": float(borrower.monthly_salary or 0),
            "loan_amount": float(borrower.loan_amount or 0),
            "dti": float(borrower.dti_ratio or 0)
        }
        
        # Credit bureau data
        bureau_data = borrower.credit_bureau_data or {}
        
        # Calculate differences
        differences = []
        if internal_data.get('salary') != bureau_data.get('salary'):
            differences.append("Salary mismatch")
        if internal_data.get('dti') != bureau_data.get('dti'):
            differences.append("DTI mismatch")
        
        await async_session.close()
        
        return jsonify({
            "internal": internal_data,
            "credit_bureau": bureau_data,
            "differences": differences,
            "risk_impact": "high" if differences else "low"
        })
    except Exception as e:
        log.error(f"Error comparing credit bureau data: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/dashboard/borrower/<borrower_id>/what-if-simulator', methods=['POST'])
@token_required
async def what_if_simulator(current_user, borrower_id):
    """
    What-if simulator: predict risk score with adjusted parameters.
    
    Body:
    {
        "monthly_salary": float (optional),
        "loan_amount": float (optional),
        "dti_ratio": float (optional)
    }
    """
    try:
        data = request.get_json()
        async_session = db.AsyncSessionFactory()
        
        borrower = await async_session.get(Borrower, borrower_id)
        if not borrower:
            await async_session.close()
            return jsonify({"error": "Borrower not found"}), 404
        
        # Get adjusted values or use current
        adjusted_salary = data.get('monthly_salary', borrower.monthly_salary)
        adjusted_loan = data.get('loan_amount', borrower.loan_amount)
        adjusted_dti = data.get('dti_ratio', borrower.dti_ratio)
        
        # TODO: Run prediction model with adjusted parameters
        # For now, return placeholder
        
        predicted_risk_score = borrower.risk_score  # Would be calculated
        predicted_risk_label = borrower.risk_label  # Would be calculated
        
        await async_session.close()
        
        return jsonify({
            "original": {
                "salary": float(borrower.monthly_salary or 0),
                "loan_amount": float(borrower.loan_amount or 0),
                "dti": float(borrower.dti_ratio or 0),
                "risk_score": float(borrower.risk_score or 0),
                "risk_label": borrower.risk_label
            },
            "adjusted": {
                "salary": adjusted_salary,
                "loan_amount": adjusted_loan,
                "dti": adjusted_dti,
                "risk_score": predicted_risk_score,
                "risk_label": predicted_risk_label
            },
            "impact": "improved" if predicted_risk_score < borrower.risk_score else "worsened"
        })
    except Exception as e:
        log.error(f"Error in what-if simulator: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/dashboard/borrower/<borrower_id>/anomalies', methods=['GET'])
@token_required
async def get_borrower_anomalies(current_user, borrower_id):
    """Get all anomalies associated with a borrower."""
    try:
        async_session = db.AsyncSessionFactory()
        
        # Verify borrower exists
        borrower = await async_session.get(Borrower, borrower_id)
        if not borrower:
            await async_session.close()
            return jsonify({"error": "Borrower not found"}), 404
        
        # Get associated alerts/anomalies
        result = await async_session.execute(
            select(Alert).where(
                Alert.related_borrower_id == borrower_id
            ).order_by(Alert.created_at.desc())
        )
        
        anomalies = []
        for alert in result.scalars().all():
            anomalies.append({
                "id": str(alert.id),
                "message": alert.message,
                "severity": alert.severity,
                "created_at": alert.created_at.isoformat() if alert.created_at else "",
                "is_read": alert.is_read
            })
        
        await async_session.close()
        
        return jsonify({"data": anomalies})
    except Exception as e:
        log.error(f"Error fetching borrower anomalies: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/dashboard/borrower/<borrower_id>/activities', methods=['GET'])
@token_required
async def get_borrower_activities(current_user, borrower_id):
    """Get activity log for a borrower (admin actions, reviews, etc)."""
    try:
        async_session = db.AsyncSessionFactory()
        
        borrower = await async_session.get(Borrower, borrower_id)
        if not borrower:
            await async_session.close()
            return jsonify({"error": "Borrower not found"}), 404
        
        # TODO: Implement activity tracking
        activities = [
            {
                "timestamp": borrower.created_at.isoformat() if borrower.created_at else "",
                "action": "Application submitted",
                "actor": "Borrower"
            }
        ]
        
        if borrower.decision_made_at:
            activities.append({
                "timestamp": borrower.decision_made_at.isoformat(),
                "action": f"Decision made: {borrower.decision_status}",
                "actor": "System"
            })
        
        await async_session.close()
        
        return jsonify({"data": activities})
    except Exception as e:
        log.error(f"Error fetching borrower activities: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/dashboard/borrower/<borrower_id>/risk-decomposition', methods=['GET'])
@token_required
async def get_risk_decomposition(current_user, borrower_id):
    """
    Get risk score decomposition (LIME feature contributions).
    Shows which factors most influenced the risk score.
    """
    try:
        async_session = db.AsyncSessionFactory()
        
        borrower = await async_session.get(Borrower, borrower_id)
        if not borrower:
            await async_session.close()
            return jsonify({"error": "Borrower not found"}), 404
        
        # TODO: Call LIME explainer for this prediction
        # For now, return placeholder decomposition
        
        decomposition = {
            "base_score": 50.0,
            "contributions": [
                {"feature": "DTI Ratio", "impact": 15.5, "direction": "increase"},
                {"feature": "Monthly Salary", "impact": -8.3, "direction": "decrease"},
                {"feature": "Loan Amount", "impact": 12.2, "direction": "increase"},
                {"feature": "Credit Bureau Score", "impact": -5.0, "direction": "decrease"},
                {"feature": "Age of Account", "impact": 3.1, "direction": "decrease"}
            ],
            "final_score": float(borrower.risk_score or 0)
        }
        
        await async_session.close()
        
        return jsonify(decomposition)
    except Exception as e:
        log.error(f"Error calculating risk decomposition: {e}")
        return jsonify({"error": str(e)}), 500
