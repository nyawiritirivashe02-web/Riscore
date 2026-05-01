from financeGuard import app
from financeGuard.api import log
from financeGuard.api.endpoints import ensure_db_ready
import os, asyncio, random, datetime, pickle, re, uuid, logging
import mimetypes

# Fix for pdf.min.mjs MIME type
mimetypes.add_type('application/javascript', '.mjs')

if __name__ == '__main__':
    log.info("Connecting to MySQL (async)...")
    asyncio.run(ensure_db_ready())

    log.info("Starting FinanceGuard v2 -> http://127.0.0.1:5000")
    app.run()