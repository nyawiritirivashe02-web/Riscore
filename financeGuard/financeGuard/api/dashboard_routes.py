"""
Dashboard API Routes
Endpoints for dashboard analytics, KPIs, filtering, exports, and real-time data.
"""

from flask import jsonify, request, send_file
from financeGuard import app, db
from financeGuard.auth.token import token_required
from financeGuard.api.dashboard_analytics import DashboardKPIs
from financeGuard.api.export_service import ExportService
from financeGuard.models.models import Borrower, Alert
from sqlalchemy import select, and_, or_, func, desc, asc
from sqlalchemy.ext.asyncio import AsyncSession
import asyncio
import logging
from datetime import datetime, timedelta
from financeGuard.models.models import now_local
import uuid

log = logging.getLogger(__name__)

# ════════════════════════════════════════════════════════════════════════════════════
# KPI Endpoints
# ════════════════════════════════════════════════════════════════════════════════════

@app.route('/api/dashboard/kpi/approval-rate', methods=['GET'])
@token_required
async def get_approval_rate(current_user):
    """Get approval rate KPI."""
    try:
        days = request.args.get('days', 30, type=int)
        async with db.engine.begin() as conn:
            async_session = db.AsyncSessionFactory()
            result = await DashboardKPIs.get_approval_rate(async_session, days)
            await async_session.close()
        return jsonify(result)
    except Exception as e:
        log.error(f"Error fetching approval rate: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/dashboard/kpi/loan-by-risk', methods=['GET'])
@token_required
async def get_loan_by_risk(current_user):
    """Get average loan amount by risk tier."""
    try:
        async_session = db.AsyncSessionFactory()
        result = await DashboardKPIs.get_loan_by_risk_tier(async_session)
        await async_session.close()
        return jsonify({"data": result})
    except Exception as e:
        log.error(f"Error fetching loan by risk: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/dashboard/kpi/bad-dti', methods=['GET'])
@token_required
async def get_bad_dti(current_user):
    """Get top bad DTI borrowers."""
    try:
        limit = request.args.get('limit', 10, type=int)
        async_session = db.AsyncSessionFactory()
        result = await DashboardKPIs.get_bad_dti_borrowers(async_session, limit)
        await async_session.close()
        return jsonify({"data": result})
    except Exception as e:
        log.error(f"Error fetching bad DTI borrowers: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/dashboard/kpi/decision-time', methods=['GET'])
@token_required
async def get_decision_time(current_user):
    """Get average decision time."""
    try:
        days = request.args.get('days', 30, type=int)
        async_session = db.AsyncSessionFactory()
        result = await DashboardKPIs.get_average_decision_time(async_session, days)
        await async_session.close()
        return jsonify(result)
    except Exception as e:
        log.error(f"Error fetching decision time: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/dashboard/kpi/credit-bureau-coverage', methods=['GET'])
@token_required
async def get_credit_bureau_coverage(current_user):
    """Get credit bureau data coverage."""
    try:
        async_session = db.AsyncSessionFactory()
        result = await DashboardKPIs.get_credit_bureau_coverage(async_session)
        await async_session.close()
        return jsonify(result)
    except Exception as e:
        log.error(f"Error fetching credit bureau coverage: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/dashboard/kpi/risk-distribution', methods=['GET'])
@token_required
async def get_risk_distribution(current_user):
    """Get risk distribution across borrowers."""
    try:
        async_session = db.AsyncSessionFactory()
        result = await DashboardKPIs.get_risk_distribution(async_session)
        await async_session.close()
        return jsonify(result)
    except Exception as e:
        log.error(f"Error fetching risk distribution: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/dashboard/kpi/daily-stats', methods=['GET'])
@token_required
async def get_daily_stats(current_user):
    """Get daily statistics for trend charts."""
    try:
        days = request.args.get('days', 30, type=int)
        async_session = db.AsyncSessionFactory()
        result = await DashboardKPIs.get_daily_stats(async_session, days)
        await async_session.close()
        return jsonify({"data": result})
    except Exception as e:
        log.error(f"Error fetching daily stats: {e}")
        return jsonify({"error": str(e)}), 500


# ════════════════════════════════════════════════════════════════════════════════════
# Alerts & Notifications
# ════════════════════════════════════════════════════════════════════════════════════

@app.route('/api/dashboard/alerts/recent', methods=['GET'])
@token_required
async def get_recent_alerts(current_user):
    """Get recent unread alerts."""
    try:
        limit = request.args.get('limit', 5, type=int)
        async_session = db.AsyncSessionFactory()
        result = await DashboardKPIs.get_recent_alerts(async_session, limit)
        await async_session.close()
        return jsonify({"data": result})
    except Exception as e:
        log.error(f"Error fetching recent alerts: {e}")
        return jsonify({"error": str(e)}), 500


# ════════════════════════════════════════════════════════════════════════════════════
# Applications Filtering & Search
# ════════════════════════════════════════════════════════════════════════════════════

@app.route('/api/dashboard/applications', methods=['GET'])
@token_required
async def get_dashboard_applications(current_user):
    """
    Get applications with advanced filtering.
    Query parameters:
      - page: int (default 1)
      - limit: int (default 20)
      - start_date: YYYY-MM-DD
      - end_date: YYYY-MM-DD
      - min_amount: float
      - max_amount: float
      - borrower_name: str (partial match)
      - risk_label: str (low/medium/high/critical)
      - decision_status: str (approved/rejected/pending)
      - sort_by: str (created_at, risk_score, loan_amount)
      - sort_order: str (asc/desc)
    """
    try:
        async_session = db.AsyncSessionFactory()
        
        # Parse filters
        page = request.args.get('page', 1, type=int)
        limit = request.args.get('limit', 20, type=int)
        offset = (page - 1) * limit
        
        # Build query
        query = select(Borrower)
        filters = []
        
        # Date range filter
        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')
        if start_date:
            try:
                start = datetime.strptime(start_date, "%Y-%m-%d")
                filters.append(Borrower.created_at >= start)
            except:
                pass
        if end_date:
            try:
                end = datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)
                filters.append(Borrower.created_at < end)
            except:
                pass
        
        # Loan amount range filter
        min_amount = request.args.get('min_amount', type=float)
        max_amount = request.args.get('max_amount', type=float)
        if min_amount is not None:
            filters.append(Borrower.loan_amount >= min_amount)
        if max_amount is not None:
            filters.append(Borrower.loan_amount <= max_amount)
        
        # Borrower name filter (partial match)
        borrower_name = request.args.get('borrower_name')
        if borrower_name:
            filters.append(Borrower.full_name.ilike(f"%{borrower_name}%"))
        
        # Risk label filter
        risk_label = request.args.get('risk_label')
        if risk_label:
            filters.append(Borrower.risk_label == risk_label)
        
        # Decision status filter
        decision_status = request.args.get('decision_status')
        if decision_status:
            filters.append(Borrower.decision_status == decision_status)
        
        # Apply all filters
        if filters:
            query = query.where(and_(*filters))
        
        # Sorting
        sort_by = request.args.get('sort_by', 'created_at')
        sort_order = request.args.get('sort_order', 'desc')
        
        sort_column = getattr(Borrower, sort_by, Borrower.created_at)
        if sort_order.lower() == 'asc':
            query = query.order_by(asc(sort_column))
        else:
            query = query.order_by(desc(sort_column))
        
        # Get total count
        count_query = select(func.count(Borrower.id)).where(
            and_(*filters) if filters else True
        )
        total = await async_session.scalar(count_query)
        
        # Get paginated results
        query = query.offset(offset).limit(limit)
        result = await async_session.execute(query)
        borrowers = result.scalars().all()
        
        # Format response
        data = []
        for borrower in borrowers:
            data.append({
                "id": str(borrower.id),
                "full_name": borrower.full_name,
                "loan_amount": float(borrower.loan_amount or 0),
                "risk_score": float(borrower.risk_score or 0),
                "risk_label": borrower.risk_label,
                "decision_status": borrower.decision_status,
                "created_at": borrower.created_at.isoformat() if borrower.created_at else "",
                "decision_made_at": borrower.decision_made_at.isoformat() if borrower.decision_made_at else "",
            })
        
        await async_session.close()
        
        return jsonify({
            "data": data,
            "page": page,
            "limit": limit,
            "total": total or 0,
            "pages": ((total or 0) + limit - 1) // limit
        })
    except Exception as e:
        log.error(f"Error fetching applications: {e}")
        return jsonify({"error": str(e)}), 500


# ════════════════════════════════════════════════════════════════════════════════════
# Export Endpoints
# ════════════════════════════════════════════════════════════════════════════════════

@app.route('/api/dashboard/applications/export/csv', methods=['POST'])
@token_required
async def export_applications_csv(current_user):
    """
    Export filtered applications to CSV.
    Body: same filters as GET /api/dashboard/applications
    """
    try:
        # TODO: Implement with same filtering logic as get_applications
        return jsonify({"error": "Not yet implemented"}), 501
    except Exception as e:
        log.error(f"Error exporting applications: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/dashboard/applications/export/excel', methods=['POST'])
@token_required
async def export_applications_excel(current_user):
    """
    Export filtered applications to Excel.
    Body: same filters as GET /api/dashboard/applications
    """
    try:
        # TODO: Implement with same filtering logic as get_applications
        return jsonify({"error": "Not yet implemented"}), 501
    except Exception as e:
        log.error(f"Error exporting applications: {e}")
        return jsonify({"error": str(e)}), 500


# ════════════════════════════════════════════════════════════════════════════════════
# Dashboard Summary (All KPIs at once)
# ════════════════════════════════════════════════════════════════════════════════════

@app.route('/api/dashboard/summary', methods=['GET'])
@token_required
async def get_dashboard_summary(current_user):
    """
    Get all dashboard KPIs at once for the main dashboard view.
    Query parameters:
      - days: int (default 30)
    """
    try:
        days = request.args.get('days', 30, type=int)
        async_session = db.AsyncSessionFactory()
        
        # Fetch all KPIs in parallel
        approval_rate = await DashboardKPIs.get_approval_rate(async_session, days)
        risk_dist = await DashboardKPIs.get_risk_distribution(async_session)
        decision_time = await DashboardKPIs.get_average_decision_time(async_session, days)
        credit_bureau = await DashboardKPIs.get_credit_bureau_coverage(async_session)
        daily_stats = await DashboardKPIs.get_daily_stats(async_session, days)
        recent_alerts = await DashboardKPIs.get_recent_alerts(async_session, 5)
        
        await async_session.close()
        
        return jsonify({
            "approval_rate": approval_rate,
            "risk_distribution": risk_dist,
            "decision_time": decision_time,
            "credit_bureau_coverage": credit_bureau,
            "daily_stats": daily_stats,
            "recent_alerts": recent_alerts,
            "period_days": days,
            "timestamp": datetime.now().isoformat()
        })
    except Exception as e:
        log.error(f"Error fetching dashboard summary: {e}")
        return jsonify({"error": str(e)}), 500
