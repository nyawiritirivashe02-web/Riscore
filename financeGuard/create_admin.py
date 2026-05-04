import asyncio
import uuid
from werkzeug.security import generate_password_hash
from financeGuard.api import AsyncSessionFactory
from financeGuard.models.models import User
from sqlalchemy import select, func

async def create_admin():
    async with AsyncSessionFactory() as session:
        email = 'admin@financeguard.local'   # <-- fixed email
        result = await session.execute(select(func.count(User.id)).where(User.email == email))
        count = result.scalar()
        if count > 0:
            print(f'Admin user already exists: {email}')
            return

        admin = User(
            id=str(uuid.uuid4()),
            full_name='Admin User',
            email=email,
            password_hash=generate_password_hash('admin123')
        )
        session.add(admin)
        await session.commit()
        print(f'Admin user created: {email} / admin123')

if __name__ == '__main__':
    asyncio.run(create_admin())