# FinanceGuard Admin Dashboard - Complete Implementation Guide

## Overview

The enhanced admin dashboard provides comprehensive tools for monitoring applications, detecting anomalies, and managing borrower profiles with real-time updates, advanced analytics, and export capabilities.

## Architecture

### Backend Structure

```
financeGuard/api/
├── dashboard_analytics.py    # KPI calculations
├── dashboard_routes.py       # API endpoints
├── dashboard_views.py        # View routes & WebSocket handlers
├── export_service.py         # CSV/Excel export utilities
├── bulk_actions.py           # Batch operations
├── anomaly_analytics.py      # Anomaly analysis endpoints
├── borrower_profiles.py      # Borrower profile endpoints
└── scheduler.py              # Background tasks
```

### Frontend Structure

```
financeGuard/templates/dashboard/
├── enhanced_dashboard.html   # Main dashboard (Vue.js + Chart.js)
└── dashboard/                # Additional views (coming soon)
```

## Features Implemented

### Phase 1: Backend Infrastructure ✅

#### 1. KPI Calculation Engine (`dashboard_analytics.py`)

**Available KPIs:**
- **Approval Rate**: Approval/rejection statistics with percentage
- **Loan by Risk Tier**: Average loan amounts grouped by risk level
- **Bad DTI Borrowers**: Top borrowers with highest debt-to-income ratios
- **Average Decision Time**: Time from application to decision (hours)
- **Credit Bureau Coverage**: Percentage of applications with credit bureau data
- **Risk Distribution**: Count of applications by risk level
- **Daily Statistics**: Trend data for charts (7/30/90 days)
- **Recent Alerts**: Latest critical/unread alerts

**Usage:**
```python
from financeGuard.api.dashboard_analytics import DashboardKPIs

# Calculate approval rate for last 30 days
result = await DashboardKPIs.get_approval_rate(session, days=30)
# Returns: {"approved": 45, "rejected": 5, "rate": 90.0, "total": 50}
```

#### 2. Export Service (`export_service.py`)

**Formats Supported:**
- CSV with customizable columns
- Excel with formatting (colors, borders, auto-width)
- PDF HTML templates for reports

**Usage:**
```python
from financeGuard.api.export_service import ExportService

# Export applications to CSV
csv_data = ExportService.applications_to_csv(applications_list)

# Export to Excel with formatting
excel_bytes = ExportService.applications_to_excel(applications_list)

# Generate PDF-ready HTML
html = ExportService.report_to_pdf_html({
    "title": "Risk Report",
    "description": "Monthly risk summary"
})
```

#### 3. Background Task Scheduler (`scheduler.py`)

**Scheduled Jobs:**
- Daily alert summary (8:00 AM)
- Weekly risk report (Mondays 9:00 AM)
- Cleanup old alerts (Sundays 2:00 AM)
- Hourly anomaly detection
- Dashboard update broadcast (every 5 minutes)

**Usage:**
```python
from financeGuard.api.scheduler import init_scheduler, stop_scheduler

# Initialize on app startup
init_scheduler()

# Stop on app shutdown
stop_scheduler()
```

#### 4. WebSocket Real-Time Updates (`dashboard_views.py`)

**Events:**
- `new_application` - New application submitted
- `anomaly_detected` - Anomaly detection alert
- `dashboard_update` - KPI data refresh
- `new_alert` - New alert/notification
- `bulk_action_result` - Bulk action completion

**Client Connection:**
```javascript
const socket = io();

socket.on('connect', () => {
  socket.emit('join_dashboard', { room: 'dashboard' });
});

socket.on('new_application', (data) => {
  console.log(`New app from ${data.name}`);
});
```

### Phase 2: API Endpoints

#### KPI Endpoints

```
GET  /api/dashboard/kpi/approval-rate           # Approval rate
GET  /api/dashboard/kpi/loan-by-risk            # Loan by risk tier
GET  /api/dashboard/kpi/bad-dti                 # Top DTI borrowers
GET  /api/dashboard/kpi/decision-time           # Average decision time
GET  /api/dashboard/kpi/credit-bureau-coverage  # CB coverage
GET  /api/dashboard/kpi/risk-distribution       # Risk distribution
GET  /api/dashboard/kpi/daily-stats             # Trend data
GET  /api/dashboard/alerts/recent               # Recent alerts
GET  /api/dashboard/summary                     # All KPIs at once (RECOMMENDED)
```

#### Applications Management

```
GET  /api/dashboard/applications                # Get with advanced filtering
POST /api/dashboard/applications/export/csv     # Export to CSV
POST /api/dashboard/applications/export/excel   # Export to Excel
```

**Query Parameters:**
```
page=1                      # Pagination
limit=20                    # Records per page
start_date=2024-01-01       # Date range filter
end_date=2024-01-31
min_amount=1000             # Loan amount range
max_amount=50000
borrower_name=John          # Partial name search
risk_label=high             # low/medium/high/critical
decision_status=approved    # approved/rejected/pending
sort_by=created_at          # Sorting field
sort_order=desc             # asc/desc
```

**Example Request:**
```bash
GET /api/dashboard/applications?page=1&limit=20&risk_label=high&decision_status=pending
Authorization: Bearer <token>
```

#### Bulk Actions

```
POST /api/dashboard/bulk-actions/mark-reviewed  # Mark as reviewed
POST /api/dashboard/bulk-actions/blacklist      # Blacklist users
POST /api/dashboard/bulk-actions/send-email     # Send emails
POST /api/dashboard/bulk-actions/update-status  # Update status
POST /api/dashboard/bulk-actions/add-tags       # Add tags
```

**Example - Mark Reviewed:**
```json
{
  "application_ids": ["uuid1", "uuid2"],
  "reviewed_by": "admin@example.com"
}
```

#### Anomaly Analytics

```
GET  /api/dashboard/anomalies                            # List with filters
GET  /api/dashboard/anomalies/severity-distribution      # Severity breakdown
GET  /api/dashboard/anomalies/top-types                  # Most frequent types
GET  /api/dashboard/anomalies/trend                      # Trend over time
PUT  /api/dashboard/anomalies/<id>/mark-resolved         # Mark as resolved
GET  /api/dashboard/anomalies/<id>/view-profile          # Get borrower profile
POST /api/dashboard/anomalies/export/csv                 # Export CSV
POST /api/dashboard/anomalies/export/pdf                 # Export PDF
```

#### Borrower Profiles

```
GET  /api/dashboard/borrower/<id>                          # Profile details
GET  /api/dashboard/borrower/<id>/loan-history            # Loan history
GET  /api/dashboard/borrower/<id>/risk-evolution          # Risk score history
GET  /api/dashboard/borrower/<id>/credit-bureau-comparison # CB comparison
POST /api/dashboard/borrower/<id>/what-if-simulator       # What-if prediction
GET  /api/dashboard/borrower/<id>/anomalies               # Associated anomalies
GET  /api/dashboard/borrower/<id>/activities              # Activity log
GET  /api/dashboard/borrower/<id>/risk-decomposition      # LIME features
```

### Phase 3: Frontend Dashboard

#### Dashboard URL
```
GET /dashboard
```

#### Features

**Overview Tab:**
- KPI cards with real-time updates
- Risk distribution pie chart
- Application trend line chart
- Recent alerts widget
- Date range selector (7/30/90 days)
- Refresh button

**Applications Tab:**
- Advanced filtering panel
- Sortable data table
- Pagination controls
- CSV/Excel export buttons
- Application detail modal
- Bulk action support (future)

**Anomalies Tab:**
- Anomaly list with severity badges
- Severity distribution breakdown
- Frequency ranking chart
- Trend visualization
- Mark resolved action
- Quick view borrower profile

**Borrower Profiles Tab:**
- Loan history timeline
- Risk score evolution chart
- What-if simulator
- Credit bureau comparison
- Anomaly history
- Risk decomposition

**Reports Tab:**
- Pre-defined report templates
- Custom report builder
- Scheduled report setup
- Report history

**UI Features:**
- Dark/Light theme toggle
- Responsive sidebar navigation
- Real-time notifications
- Toast notifications
- Search functionality
- Keyboard shortcuts

## Installation & Setup

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

New dependencies:
- `flask-socketio` - Real-time updates
- `python-socketio` - WebSocket support
- `openpyxl` - Excel export
- `APScheduler` - Background tasks
- `python-dateutil` - Date utilities

### 2. Initialize Database

```bash
# Apply migrations
flask db upgrade

# Seed initial data (optional)
python scripts/seed_dashboard.py
```

### 3. Start Application

```bash
python app.py
```

The dashboard will be available at: `http://localhost:5000/dashboard`

## Usage Examples

### Fetching Dashboard Overview

```python
import requests

headers = {'Authorization': f'Bearer {token}'}

# Get all KPIs at once
response = requests.get(
    'http://localhost:5000/api/dashboard/summary?days=30',
    headers=headers
)

data = response.json()
# {
#   "approval_rate": {...},
#   "risk_distribution": {...},
#   "decision_time": {...},
#   "credit_bureau_coverage": {...},
#   "daily_stats": [...],
#   "recent_alerts": [...]
# }
```

### Advanced Applications Search

```javascript
// In frontend Vue.js
const params = new URLSearchParams({
  page: 1,
  limit: 20,
  start_date: '2024-01-01',
  end_date: '2024-01-31',
  risk_label: 'high',
  decision_status: 'pending',
  sort_by: 'risk_score',
  sort_order: 'desc'
});

const response = await axios.get(
  `/api/dashboard/applications?${params}`,
  { headers: { 'Authorization': `Bearer ${token}` } }
);
```

### Exporting Data

```javascript
// Export applications as CSV
axios.post(
  '/api/dashboard/applications/export/csv',
  { /* filter parameters */ },
  {
    headers: { 'Authorization': `Bearer ${token}` },
    responseType: 'blob'
  }
).then(response => {
  // Trigger download
  const url = window.URL.createObjectURL(new Blob([response.data]));
  const link = document.createElement('a');
  link.href = url;
  link.setAttribute('download', 'applications.csv');
  document.body.appendChild(link);
  link.click();
});
```

### Real-Time WebSocket Connection

```javascript
const socket = io();

// Connect to dashboard room
socket.on('connect', () => {
  socket.emit('join_dashboard', { room: 'dashboard' });
  console.log('Connected to dashboard');
});

// Listen for new applications
socket.on('new_application', (data) => {
  console.log(`New application from ${data.name}`);
  // Update UI
});

// Listen for anomalies
socket.on('anomaly_detected', (data) => {
  console.log(`Anomaly: ${data.message}`);
  // Show notification
});

// Listen for KPI updates
socket.on('dashboard_update', (data) => {
  console.log('Dashboard updated:', data);
  // Refresh charts
});
```

## Performance Optimization

### Caching Strategy

```python
# Cache KPI calculations for 5 minutes
from functools import lru_cache
import time

@lru_cache(maxsize=32)
def get_cached_kpi(kpi_type, days):
    # Expensive calculation
    return result

# Clear cache every 5 minutes
scheduler.add_job(get_cached_kpi.cache_clear, 'interval', minutes=5)
```

### Server-Side Pagination

- Default: 20 records per page
- Maximum: 100 records per page
- Recommended: Use pagination for tables > 1000 rows

### Database Indexing

```sql
-- Recommended indexes for performance
CREATE INDEX idx_borrower_created ON borrower(created_at DESC);
CREATE INDEX idx_borrower_risk_label ON borrower(risk_label);
CREATE INDEX idx_borrower_decision ON borrower(decision_status);
CREATE INDEX idx_alert_severity ON alert(severity);
CREATE INDEX idx_alert_created ON alert(created_at DESC);
```

## Security Considerations

### Authentication
All dashboard endpoints require `@token_required` decorator:
```python
@app.route('/api/dashboard/...')
@token_required
async def endpoint(current_user):
    # current_user is authenticated
```

### Authorization
Implement role-based access control:
```python
@token_required
@require_admin  # Custom decorator
async def endpoint(current_user):
    # Only admins can access
```

### Data Protection
- Sensitive data (national IDs, salaries) is not exposed in list views
- Credit bureau data is marked as confidential
- Audit logging for admin actions

## Troubleshooting

### WebSocket Connection Issues

**Problem:** WebSocket connection fails
**Solution:** 
```python
# Ensure CORS is configured correctly in __init__.py
socketio = SocketIO(app, cors_allowed_origins=[...])
```

### Scheduler Not Running

**Problem:** Background tasks not executing
**Solution:**
```bash
# Check if scheduler is initialized
# In app.py ensure: init_scheduler() is called before socketio.run()

# Check logs
tail -f financeGuard/logs/app.log
```

### Export Fails

**Problem:** CSV/Excel export returns 501
**Solution:** These are placeholder endpoints. Implement using ExportService:
```python
@app.route('/api/dashboard/applications/export/csv', methods=['POST'])
@token_required
async def export_applications_csv(current_user):
    data = request.get_json()
    # Filter applications based on data
    csv_file = ExportService.applications_to_csv(filtered_apps)
    return send_file(csv_file, ...)
```

## Future Enhancements

- [ ] Drag-and-drop dashboard layout customization
- [ ] Custom report templates
- [ ] Scheduled email reports
- [ ] Advanced LIME explainability UI
- [ ] Borrower communication tools
- [ ] Risk model performance monitoring
- [ ] A/B testing framework
- [ ] API rate limiting & quotas

## Database Schema Extensions

```sql
-- Activity log for audit trail
CREATE TABLE admin_activity (
  id VARCHAR(36) PRIMARY KEY,
  admin_id VARCHAR(36),
  action VARCHAR(255),
  entity_type VARCHAR(50),
  entity_id VARCHAR(36),
  timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- User tags for categorization
CREATE TABLE borrower_tags (
  id VARCHAR(36) PRIMARY KEY,
  borrower_id VARCHAR(36),
  tag VARCHAR(100),
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Loan history tracking
CREATE TABLE loan_history (
  id VARCHAR(36) PRIMARY KEY,
  borrower_id VARCHAR(36),
  loan_amount FLOAT,
  status VARCHAR(50),
  decision_date TIMESTAMP,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

## API Response Examples

### KPI Summary Response
```json
{
  "approval_rate": {
    "approved": 45,
    "rejected": 5,
    "rate": 90.0,
    "period_days": 30,
    "total": 50
  },
  "risk_distribution": {
    "low": 20,
    "medium": 18,
    "high": 10,
    "critical": 2,
    "total": 50
  },
  "daily_stats": [
    {
      "date": "2024-01-15",
      "applications": 5,
      "approved": 4,
      "rejected": 1,
      "avg_score": 62.5
    }
  ]
}
```

### Applications List Response
```json
{
  "data": [
    {
      "id": "550e8400-e29b-41d4-a716-446655440000",
      "full_name": "John Doe",
      "loan_amount": 5000,
      "risk_score": 75.5,
      "risk_label": "high",
      "decision_status": "pending",
      "created_at": "2024-01-15T10:30:00"
    }
  ],
  "page": 1,
  "limit": 20,
  "total": 150,
  "pages": 8
}
```

## Support & Contact

For questions or issues:
- Review logs: `logs/financeGuard.log`
- Check API documentation: This file
- Test endpoints: Use Postman/curl
- Contact: admin@finaneguard.local
