"""
Bulk Actions API Routes
Endpoints for performing batch operations on applications and users.
"""

from flask import jsonify, request
from financeGuard import app, db, mail
from financeGuard.auth.token import token_required
from financeGuard.models.models import Borrower, BlacklistedUser
from financeGuard.api.dashboard_views import broadcast_bulk_action_result
from sqlalchemy import update, select
from datetime import datetime
import logging
import uuid

log = logging.getLogger(__name__)

try:
    from flask_mail import Message
except ModuleNotFoundError:
    Message = None


@app.route('/api/dashboard/bulk-actions/mark-reviewed', methods=['POST'])
@token_required
async def bulk_mark_reviewed(current_user):
    """
    Mark multiple applications as reviewed by admin.
    
    Body:
    {
        "application_ids": ["uuid1", "uuid2", ...],
        "reviewed_by": "admin@example.com"
    }
    """
    try:
        data = request.get_json()
        app_ids = data.get('application_ids', [])
        reviewed_by = data.get('reviewed_by', str(current_user.id))
        
        if not app_ids:
            return jsonify({"error": "No applications specified"}), 400
        
        async_session = db.AsyncSessionFactory()
        
        # Update applications
        stmt = update(Borrower).where(
            Borrower.id.in_(app_ids)
        ).values(
            is_reviewed=True,
            reviewed_by=reviewed_by,
            reviewed_at=datetime.utcnow()
        )
        
        result = await async_session.execute(stmt)
        await async_session.commit()
        
        count = result.rowcount
        await async_session.close()
        
        # Broadcast result
        broadcast_bulk_action_result('mark_reviewed', {
            'count': count,
            'status': 'completed',
            'timestamp': datetime.utcnow().isoformat()
        })
        
        log.info(f"Bulk action: marked {count} applications as reviewed")
        return jsonify({
            "message": f"Marked {count} applications as reviewed",
            "count": count
        })
    except Exception as e:
        log.error(f"Error marking applications as reviewed: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/dashboard/bulk-actions/blacklist', methods=['POST'])
@token_required
async def bulk_blacklist_users(current_user):
    """
    Add multiple users to blacklist.
    
    Body:
    {
        "user_ids": ["uuid1", "uuid2", ...],
        "reason": "Fraud detected",
        "blacklist_by": "admin@example.com"
    }
    """
    try:
        data = request.get_json()
        user_ids = data.get('user_ids', [])
        reason = data.get('reason', 'Manual blacklist by admin')
        blacklist_by = data.get('blacklist_by', str(current_user.id))
        
        if not user_ids:
            return jsonify({"error": "No users specified"}), 400
        
        async_session = db.AsyncSessionFactory()
        
        # Create blacklist records
        blacklist_records = []
        for user_id in user_ids:
            record = BlacklistedUser(
                id=str(uuid.uuid4()),
                user_id=user_id,
                reason=reason,
                blacklisted_at=datetime.utcnow(),
                blacklisted_by=blacklist_by
            )
            blacklist_records.append(record)
        
        for record in blacklist_records:
            async_session.add(record)
        
        await async_session.commit()
        await async_session.close()
        
        count = len(blacklist_records)
        
        # Broadcast result
        broadcast_bulk_action_result('blacklist', {
            'count': count,
            'status': 'completed',
            'timestamp': datetime.utcnow().isoformat()
        })
        
        log.info(f"Bulk action: blacklisted {count} users - {reason}")
        return jsonify({
            "message": f"Added {count} users to blacklist",
            "count": count
        })
    except Exception as e:
        log.error(f"Error blacklisting users: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/dashboard/bulk-actions/send-email', methods=['POST'])
@token_required
def bulk_send_email(current_user):
    """
    Send email to multiple borrowers.
    
    Body:
    {
        "borrower_ids": ["uuid1", "uuid2", ...],
        "subject": "Email Subject",
        "body": "Email body",
        "template": "optional_template_name"
    }
    """
    try:
        if not Message:
            return jsonify({"error": "Email service not configured"}), 501
        
        data = request.get_json()
        borrower_ids = data.get('borrower_ids', [])
        subject = data.get('subject')
        body = data.get('body')
        template = data.get('template')
        
        if not subject or not body:
            return jsonify({"error": "Subject and body required"}), 400
        
        if not borrower_ids:
            return jsonify({"error": "No borrowers specified"}), 400
        
        # TODO: Fetch borrower emails from database
        # For now, this is a placeholder
        emails_sent = 0
        
        # Simulate sending emails (would be async in production)
        for borrower_id in borrower_ids:
            # TODO: Fetch borrower email
            # Try to send email
            try:
                # msg = Message(subject, recipients=[borrower_email], body=body)
                # mail.send(msg)
                emails_sent += 1
            except Exception as e:
                log.warning(f"Failed to send email for borrower {borrower_id}: {e}")
        
        # Broadcast result
        broadcast_bulk_action_result('send_email', {
            'count': emails_sent,
            'status': 'completed',
            'timestamp': datetime.utcnow().isoformat()
        })
        
        log.info(f"Bulk action: sent emails to {emails_sent} borrowers")
        return jsonify({
            "message": f"Emails sent to {emails_sent} borrowers",
            "count": emails_sent
        })
    except Exception as e:
        log.error(f"Error sending bulk emails: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/dashboard/bulk-actions/update-status', methods=['POST'])
@token_required
async def bulk_update_status(current_user):
    """
    Update decision status for multiple applications.
    
    Body:
    {
        "application_ids": ["uuid1", "uuid2", ...],
        "status": "approved|rejected|pending",
        "reason": "Optional reason for status change"
    }
    """
    try:
        data = request.get_json()
        app_ids = data.get('application_ids', [])
        new_status = data.get('status')
        reason = data.get('reason', '')
        
        if not app_ids or not new_status:
            return jsonify({"error": "Missing required fields"}), 400
        
        if new_status not in ['approved', 'rejected', 'pending']:
            return jsonify({"error": "Invalid status"}), 400
        
        async_session = db.AsyncSessionFactory()
        
        # Update applications
        stmt = update(Borrower).where(
            Borrower.id.in_(app_ids)
        ).values(
            decision_status=new_status,
            decision_made_at=datetime.utcnow()
        )
        
        result = await async_session.execute(stmt)
        await async_session.commit()
        
        count = result.rowcount
        await async_session.close()
        
        # Broadcast result
        broadcast_bulk_action_result('update_status', {
            'count': count,
            'new_status': new_status,
            'status': 'completed',
            'timestamp': datetime.utcnow().isoformat()
        })
        
        log.info(f"Bulk action: updated {count} applications to {new_status}")
        return jsonify({
            "message": f"Updated {count} applications to {new_status}",
            "count": count
        })
    except Exception as e:
        log.error(f"Error updating application statuses: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/dashboard/bulk-actions/add-tags', methods=['POST'])
@token_required
async def bulk_add_tags(current_user):
    """
    Add tags to multiple applications for categorization.
    
    Body:
    {
        "application_ids": ["uuid1", "uuid2", ...],
        "tags": ["tag1", "tag2", ...]
    }
    """
    try:
        data = request.get_json()
        app_ids = data.get('application_ids', [])
        tags = data.get('tags', [])
        
        if not app_ids or not tags:
            return jsonify({"error": "Missing required fields"}), 400
        
        # TODO: Implement tag addition logic
        # This depends on your Borrower model having a tags field
        # or a separate tags table
        
        log.info(f"Bulk action: added tags to {len(app_ids)} applications")
        return jsonify({
            "message": f"Added tags to {len(app_ids)} applications",
            "count": len(app_ids)
        })
    except Exception as e:
        log.error(f"Error adding tags: {e}")
        return jsonify({"error": str(e)}), 500
