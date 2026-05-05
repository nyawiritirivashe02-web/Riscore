from financeGuard import app, socketio
from financeGuard.api import log
from financeGuard.api.endpoints import ensure_db_ready
from financeGuard.api.scheduler import init_scheduler, stop_scheduler
import os, asyncio, random, datetime, pickle, re, uuid, logging
import mimetypes

# Fix for pdf.min.mjs MIME type
mimetypes.add_type('application/javascript', '.mjs')

if __name__ == '__main__':
    log.info("Connecting to MySQL (async)...")
    asyncio.run(ensure_db_ready())

    log.info("Initializing background task scheduler...")
    init_scheduler()

    log.info("Starting FinanceGuard v2 -> http://127.0.0.1:5000")
    try:
        socketio.run(app, host='127.0.0.1', port=5000, debug=False)
    finally:
        stop_scheduler()