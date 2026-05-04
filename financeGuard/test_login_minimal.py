import asyncio
from financeGuard.api import AsyncSessionFactory
from financeGuard.models.models import User
from werkzeug.security import check_password_hash
from sqlalchemy import select, func

async def manual_check():
    async with AsyncSessionFactory() as session:
        result = await session.execute(select(User).where(func.lower(User.email) == 'admin@financeguard.local'))
        user = result.scalar_one_or_none()
        if user:
            print("User found")
            print("Password valid:", check_password_hash(user.password_hash, 'admin123'))
        else:
            print("User not found")

asyncio.run(manual_check())