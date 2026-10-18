"""
Database Migration Script for WAJ VTU Marketing Automation System.
Can be executed directly via `python migrations.py` or imported into the Flask startup.
Compatible with PostgreSQL and SQLite.
"""
import os
import sys
from sqlalchemy import inspect, text

# Add current workspace to path
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from app import app, db
from models import (
    User,
    Referral,
    ReferralReward,
    MarketingContent,
    MarketingCampaign,
    MarketingMessage,
    MarketingSetting,
)
from services.marketing_settings import seed_default_marketing_settings
from services.referral_service import generate_unique_referral_code


def run_migrations():
    """Runs all schema updates, creates new marketing tables, and seeds defaults."""
    with app.app_context():
        inspector = inspect(db.engine)
        existing_tables = set(inspector.get_table_names())
        dialect = db.engine.dialect.name
        print(f"[migration] Running migrations for dialect: {dialect}...")

        # 1. Create any missing tables
        marketing_models = [
            Referral,
            ReferralReward,
            MarketingContent,
            MarketingCampaign,
            MarketingMessage,
            MarketingSetting,
        ]

        for model in marketing_models:
            table_name = model.__tablename__
            if table_name not in existing_tables:
                print(f"[migration] Creating table: {table_name}")
                model.__table__.create(bind=db.engine)

        # 2. Add new columns to 'users' table if missing
        if "users" in existing_tables:
            user_cols = {col["name"] for col in inspector.get_columns("users")}
            if "referral_code" not in user_cols:
                print("[migration] Adding referral_code column to users")
                db.session.execute(text('ALTER TABLE "users" ADD COLUMN "referral_code" VARCHAR(30)'))
            if "referred_by_id" not in user_cols:
                print("[migration] Adding referred_by_id column to users")
                db.session.execute(text('ALTER TABLE "users" ADD COLUMN "referred_by_id" INTEGER'))
            db.session.commit()

            # 3. Create unique index on referral_code
            user_indexes = {idx["name"] for idx in inspector.get_indexes("users")}
            if "ix_users_referral_code" not in user_indexes:
                print("[migration] Creating unique index on users.referral_code")
                where_clause = "WHERE referral_code IS NOT NULL" if dialect == "postgresql" else ""
                db.session.execute(
                    text(f'CREATE UNIQUE INDEX IF NOT EXISTS ix_users_referral_code ON "users" (referral_code) {where_clause}')
                )
                db.session.commit()

        # 4. Backfill referral codes for existing users without one
        users_without_code = User.query.filter(
            (User.referral_code == None) | (User.referral_code == "")  # noqa: E711
        ).all()
        if users_without_code:
            print(f"[migration] Backfilling referral codes for {len(users_without_code)} existing users...")
            for user in users_without_code:
                user.referral_code = generate_unique_referral_code()
            db.session.commit()

        # 5. Seed default marketing settings
        seed_default_marketing_settings()
        print("[migration] Marketing settings seeded successfully.")
        print("[migration] All migrations completed successfully!")


if __name__ == "__main__":
    run_migrations()
