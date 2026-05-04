"""
Dashboard View Routes
Serves the admin dashboard and handles WebSocket events for real-time updates.
"""

from flask import render_template, jsonify
from flask_socketio import emit, join_room, leave_room, disconnect
from financeGuard import app, socketio
import logging

log = logging.getLogger(__name__)

# Store active WebSocket connections
active_connections = set()


# ════════════════════════════════════════════════════════════════════════════════════
# Dashboard View Routes
# ════════════════════════════════════════════════════════════════════════════════════

@app.route('/dashboard', methods=['GET'])
def dashboard_page():
    """Serve the main admin dashboard."""
    return render_template('dashboard/enhanced_dashboard.html')


@app.route('/dashboard/index', methods=['GET'])
def dashboard_index():
    """Alternate dashboard route."""
    return render_template('dashboard/enhanced_dashboard.html')


# ════════════════════════════════════════════════════════════════════════════════════
# WebSocket Events for Real-Time Updates
# ════════════════════════════════════════════════════════════════════════════════════

@socketio.on('connect')
def handle_connect():
    """Handle WebSocket client connection."""
    client_id = request.sid
    active_connections.add(client_id)
    log.info(f"Client connected: {client_id}. Total connections: {len(active_connections)}")
    emit('connection_response', {'data': 'Connected to dashboard'})


@socketio.on('disconnect')
def handle_disconnect():
    """Handle WebSocket client disconnection."""
    client_id = request.sid
    active_connections.discard(client_id)
    log.info(f"Client disconnected: {client_id}. Total connections: {len(active_connections)}")


@socketio.on('join_dashboard')
def on_join_dashboard(data):
    """Handle client joining the dashboard room."""
    room = data.get('room', 'dashboard')
    join_room(room)
    emit('message', {'data': f'Joined {room}'})
    log.info(f"Client {request.sid} joined room: {room}")


@socketio.on('leave_dashboard')
def on_leave_dashboard(data):
    """Handle client leaving the dashboard room."""
    room = data.get('room', 'dashboard')
    leave_room(room)
    emit('message', {'data': f'Left {room}'})
    log.info(f"Client {request.sid} left room: {room}")


@socketio.on('request_kpi_update')
def handle_kpi_update_request(data):
    """
    Handle request for KPI update.
    Client sends this to get updated dashboard data.
    """
    try:
        days = data.get('days', 30)
        # This would trigger the backend to calculate KPIs
        # and emit them back to the client
        emit('kpi_update_requested', {'days': days})
        log.debug(f"KPI update requested for {days} days")
    except Exception as e:
        log.error(f"Error handling KPI update request: {e}")


# ════════════════════════════════════════════════════════════════════════════════════
# Helper Functions for Broadcasting Updates
# ════════════════════════════════════════════════════════════════════════════════════

def broadcast_new_application(application_data):
    """
    Broadcast a new application notification to all connected dashboard clients.
    
    Args:
        application_data: Dictionary with application details
    """
    try:
        socketio.emit(
            'new_application',
            {
                'id': str(application_data.get('id')),
                'name': application_data.get('full_name'),
                'loan_amount': float(application_data.get('loan_amount', 0)),
                'risk_score': float(application_data.get('risk_score', 0)),
                'created_at': application_data.get('created_at', '')
            },
            room='dashboard'
        )
        log.debug(f"Broadcast: new application from {application_data.get('full_name')}")
    except Exception as e:
        log.error(f"Error broadcasting new application: {e}")


def broadcast_anomaly_detected(anomaly_data):
    """
    Broadcast anomaly detection notification to all connected dashboard clients.
    
    Args:
        anomaly_data: Dictionary with anomaly details
    """
    try:
        socketio.emit(
            'anomaly_detected',
            {
                'id': str(anomaly_data.get('id')),
                'borrower_name': anomaly_data.get('borrower_name'),
                'anomaly_type': anomaly_data.get('anomaly_type'),
                'severity': anomaly_data.get('severity'),
                'message': anomaly_data.get('message'),
                'detected_at': anomaly_data.get('detected_at', '')
            },
            room='dashboard'
        )
        log.debug(f"Broadcast: anomaly detected - {anomaly_data.get('anomaly_type')}")
    except Exception as e:
        log.error(f"Error broadcasting anomaly: {e}")


def broadcast_dashboard_update(kpi_data):
    """
    Broadcast updated KPI data to all connected dashboard clients.
    
    Args:
        kpi_data: Dictionary with all KPI calculations
    """
    try:
        socketio.emit(
            'dashboard_update',
            kpi_data,
            room='dashboard'
        )
        log.debug("Broadcast: dashboard KPI update")
    except Exception as e:
        log.error(f"Error broadcasting dashboard update: {e}")


def broadcast_alert(alert_data):
    """
    Broadcast a new alert or alert update to all connected dashboard clients.
    
    Args:
        alert_data: Dictionary with alert details
    """
    try:
        socketio.emit(
            'new_alert',
            {
                'id': str(alert_data.get('id')),
                'message': alert_data.get('message'),
                'severity': alert_data.get('severity'),
                'created_at': alert_data.get('created_at', '')
            },
            room='dashboard'
        )
        log.debug(f"Broadcast: new alert - {alert_data.get('severity')}")
    except Exception as e:
        log.error(f"Error broadcasting alert: {e}")


def broadcast_bulk_action_result(action_type, result_data):
    """
    Broadcast result of bulk action to all connected dashboard clients.
    
    Args:
        action_type: Type of action (e.g., 'mark_reviewed', 'blacklist')
        result_data: Dictionary with action results
    """
    try:
        socketio.emit(
            'bulk_action_result',
            {
                'action_type': action_type,
                'count': result_data.get('count', 0),
                'status': result_data.get('status', 'completed'),
                'timestamp': result_data.get('timestamp', '')
            },
            room='dashboard'
        )
        log.debug(f"Broadcast: bulk action result - {action_type}")
    except Exception as e:
        log.error(f"Error broadcasting bulk action result: {e}")
