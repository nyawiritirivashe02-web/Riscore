"""
Dashboard Analytics Module
Provides KPI calculations, aggregations, and analytics for admin dashboard.
"""

from sqlalchemy import func, select, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime, timedelta
from financeGuard.models.models import Borrower, User, Alert, now_local
import logging

log = logging.getLogger(__name__)


class DashboardKPIs:
    """Centralized KPI calculations for dashboard."""

    @staticmethod
    async def get_approval_rate(session: AsyncSession, days: int = 30) -> dict:
        """
        Calculate approval rate for the last N days.
        
        Returns:
            {
                "approved": int,
                "rejected": int,
                "rate": float (0-100),
                "period_days": int
            }
        """
        try:
            cutoff = now_local() - timedelta(days=days)
            
            # Count approved and rejected in period
            approved = await session.scalar(
                select(func.count(Borrower.id)).where(
                    and_(
                        Borrower.created_at >= cutoff,
                        Borrower.decision_status == "approved"
                    )
                )
            )
            rejected = await session.scalar(
                select(func.count(Borrower.id)).where(
                    and_(
                        Borrower.created_at >= cutoff,
                        Borrower.decision_status == "rejected"
                    )
                )
            )
            
            total = (approved or 0) + (rejected or 0)
            rate = ((approved or 0) / total * 100) if total > 0 else 0
            
            return {
                "approved": approved or 0,
                "rejected": rejected or 0,
                "rate": round(rate, 2),
                "period_days": days,
                "total": total
            }
        except Exception as e:
            log.error(f"Error calculating approval rate: {e}")
            return {"approved": 0, "rejected": 0, "rate": 0, "period_days": days, "total": 0}

    @staticmethod
    async def get_loan_by_risk_tier(session: AsyncSession) -> list[dict]:
        """
        Get average loan amount grouped by risk tier.
        
        Returns:
            [
                {"risk_tier": "low", "avg_amount": 5000, "count": 45},
                {"risk_tier": "medium", "avg_amount": 8000, "count": 23},
                {"risk_tier": "high", "avg_amount": 12000, "count": 8}
            ]
        """
        try:
            # This assumes risk_label exists on Borrower model
            result = await session.execute(
                select(
                    Borrower.risk_label,
                    func.avg(Borrower.loan_amount).label("avg_amount"),
                    func.count(Borrower.id).label("count")
                ).group_by(Borrower.risk_label)
            )
            
            data = []
            for row in result.all():
                data.append({
                    "risk_tier": row[0] or "unknown",
                    "avg_amount": float(row[1] or 0),
                    "count": row[2] or 0
                })
            return data
        except Exception as e:
            log.error(f"Error calculating loan by risk tier: {e}")
            return []

    @staticmethod
    async def get_bad_dti_borrowers(session: AsyncSession, limit: int = 10) -> list[dict]:
        """
        Get top borrowers with highest DTI (debt-to-income ratio).
        
        Returns:
            [
                {"name": "John Doe", "dti": 0.85, "loan_amount": 5000, "salary": 5882},
                ...
            ]
        """
        try:
            # This assumes dti_ratio field exists; adjust based on actual model
            result = await session.execute(
                select(
                    Borrower.full_name,
                    Borrower.dti_ratio,
                    Borrower.loan_amount,
                    Borrower.monthly_salary
                ).order_by(Borrower.dti_ratio.desc()).limit(limit)
            )
            
            data = []
            for row in result.all():
                data.append({
                    "name": row[0] or "Unknown",
                    "dti": round(float(row[1] or 0), 2),
                    "loan_amount": float(row[2] or 0),
                    "salary": float(row[3] or 0)
                })
            return data
        except Exception as e:
            log.error(f"Error fetching bad DTI borrowers: {e}")
            return []

    @staticmethod
    async def get_average_decision_time(session: AsyncSession, days: int = 30) -> dict:
        """
        Calculate average time from application to decision.
        
        Returns:
            {
                "avg_hours": float,
                "median_hours": float,
                "min_hours": float,
                "max_hours": float,
                "count": int
            }
        """
        try:
            cutoff = now_local() - timedelta(days=days)
            
            # Get all borrowers with decisions in period
            result = await session.execute(
                select(Borrower).where(
                    and_(
                        Borrower.created_at >= cutoff,
                        Borrower.decision_made_at.isnot(None)
                    )
                )
            )
            
            durations = []
            for borrower in result.scalars().all():
                if borrower.decision_made_at:
                    delta = borrower.decision_made_at - borrower.created_at
                    hours = delta.total_seconds() / 3600
                    durations.append(hours)
            
            if not durations:
                return {"avg_hours": 0, "median_hours": 0, "min_hours": 0, "max_hours": 0, "count": 0}
            
            durations.sort()
            avg = sum(durations) / len(durations)
            median = durations[len(durations) // 2]
            
            return {
                "avg_hours": round(avg, 2),
                "median_hours": round(median, 2),
                "min_hours": round(min(durations), 2),
                "max_hours": round(max(durations), 2),
                "count": len(durations)
            }
        except Exception as e:
            log.error(f"Error calculating average decision time: {e}")
            return {"avg_hours": 0, "median_hours": 0, "min_hours": 0, "max_hours": 0, "count": 0}

    @staticmethod
    async def get_credit_bureau_coverage(session: AsyncSession) -> dict:
        """
        Get credit bureau data coverage statistics.
        
        Returns:
            {
                "total_borrowers": int,
                "with_credit_bureau": int,
                "coverage_percent": float,
                "mismatch_count": int
            }
        """
        try:
            total = await session.scalar(select(func.count(Borrower.id)))
            
            # Assuming credit_bureau_data field exists
            with_bureau = await session.scalar(
                select(func.count(Borrower.id)).where(
                    Borrower.credit_bureau_data.isnot(None)
                )
            )
            
            coverage = (with_bureau / total * 100) if total > 0 else 0
            
            return {
                "total_borrowers": total or 0,
                "with_credit_bureau": with_bureau or 0,
                "coverage_percent": round(coverage, 2),
                "mismatch_count": 0  # To be calculated based on business logic
            }
        except Exception as e:
            log.error(f"Error calculating credit bureau coverage: {e}")
            return {"total_borrowers": 0, "with_credit_bureau": 0, "coverage_percent": 0, "mismatch_count": 0}

    @staticmethod
    async def get_risk_distribution(session: AsyncSession) -> dict:
        """
        Get distribution of borrowers by risk level.
        
        Returns:
            {
                "low": int,
                "medium": int,
                "high": int,
                "critical": int,
                "total": int
            }
        """
        try:
            result = await session.execute(
                select(
                    Borrower.risk_label,
                    func.count(Borrower.id).label("count")
                ).group_by(Borrower.risk_label)
            )
            
            distribution = {"low": 0, "medium": 0, "high": 0, "critical": 0, "total": 0}
            
            for row in result.all():
                risk_label = (row[0] or "low").lower()
                count = row[1] or 0
                if risk_label in distribution:
                    distribution[risk_label] = count
                distribution["total"] += count
            
            return distribution
        except Exception as e:
            log.error(f"Error calculating risk distribution: {e}")
            return {"low": 0, "medium": 0, "high": 0, "critical": 0, "total": 0}

    @staticmethod
    async def get_daily_stats(session: AsyncSession, days: int = 30) -> list[dict]:
        """
        Get daily statistics for the last N days (for trend charts).
        
        Returns:
            [
                {"date": "2024-01-15", "applications": 5, "approved": 3, "rejected": 2, "avg_score": 62.5},
                ...
            ]
        """
        try:
            cutoff = now_local() - timedelta(days=days)
            
            result = await session.execute(
                select(
                    func.date(Borrower.created_at).label("date"),
                    func.count(Borrower.id).label("applications"),
                    func.sum(
                        (Borrower.decision_status == "approved").cast(int)
                    ).label("approved"),
                    func.sum(
                        (Borrower.decision_status == "rejected").cast(int)
                    ).label("rejected"),
                    func.avg(Borrower.risk_score).label("avg_score")
                ).where(Borrower.created_at >= cutoff).group_by(
                    func.date(Borrower.created_at)
                ).order_by(func.date(Borrower.created_at).desc())
            )
            
            data = []
            for row in result.all():
                data.append({
                    "date": str(row[0] or ""),
                    "applications": row[1] or 0,
                    "approved": row[2] or 0,
                    "rejected": row[3] or 0,
                    "avg_score": round(float(row[4] or 0), 2)
                })
            return list(reversed(data))  # Return in chronological order
        except Exception as e:
            log.error(f"Error calculating daily stats: {e}")
            return []

    @staticmethod
    async def get_recent_alerts(session: AsyncSession, limit: int = 5) -> list[dict]:
        """
        Get recent critical/unread alerts.
        
        Returns:
            [
                {"id": "uuid", "message": "...", "severity": "critical", "created_at": "2024-01-15T10:30:00"},
                ...
            ]
        """
        try:
            result = await session.execute(
                select(Alert).order_by(Alert.created_at.desc()).limit(limit)
            )
            
            data = []
            for alert in result.scalars().all():
                data.append({
                    "id": str(alert.id),
                    "message": alert.message,
                    "severity": alert.severity or "info",
                    "created_at": alert.created_at.isoformat() if alert.created_at else "",
                    "is_read": alert.is_read or False
                })
            return data
        except Exception as e:
            log.error(f"Error fetching recent alerts: {e}")
            return []
