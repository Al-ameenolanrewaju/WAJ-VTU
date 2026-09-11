from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timezone

db = SQLAlchemy()


def utc_now():
    """Returns timezone-aware UTC datetime for timestamp fields."""
    return datetime.now(timezone.utc)


class User(db.Model):
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    whatsapp_id = db.Column(db.String(50), unique=True, nullable=False, index=True)
    phone = db.Column(db.String(20), nullable=False, index=True)
    name = db.Column(db.String(100), default="User")
    wallet_balance = db.Column(db.Numeric(10, 2), default=0.00, nullable=False)

    # User Flow & State Management
    current_state = db.Column(db.String(50), default="IDLE", nullable=False)
    state_data = db.Column(db.JSON, default=dict)

    # Paystack Dedicated Virtual Account details
    paystack_customer_code = db.Column(db.String(100), nullable=True)
    dva_account_number = db.Column(db.String(20), nullable=True)
    dva_bank_name = db.Column(db.String(50), nullable=True)

    # Concurrency control to prevent balance race conditions
    version_id = db.Column(db.Integer, nullable=False, default=1)

    created_at = db.Column(db.DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)

    transactions = db.relationship('Transaction', backref='user', lazy=True, cascade="all, delete-orphan")

    __mapper_args__ = {
        "version_id_col": version_id
    }

    def __repr__(self):
        return f"<User {self.phone} - Balance: NGN {self.wallet_balance}>"


class Transaction(db.Model):
    __tablename__ = 'transactions'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    reference = db.Column(db.String(100), unique=True, nullable=False, index=True)

    # AIRTIME, DATA, CABLE, ELECTRICITY, BETTING, EDU, DEPOSIT
    type = db.Column(db.String(50), nullable=False, index=True)
    amount = db.Column(db.Numeric(10, 2), nullable=False)
    recipient = db.Column(db.String(50), nullable=True)

    # PENDING, SUCCESS, FAILED
    status = db.Column(db.String(20), default='PENDING', nullable=False, index=True)
    description = db.Column(db.String(255), nullable=True)

    # Metadata for storing provider API responses (tokens, PINs, request IDs)
    meta_data = db.Column(db.JSON, default=dict)

    created_at = db.Column(db.DateTime(timezone=True), default=utc_now, nullable=False)

    def __repr__(self):
        return f"<Transaction {self.reference} - {self.type} - {self.status}>"