from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import MetaData
from datetime import datetime, timezone
import os

database_url = os.getenv("DATABASE_URL", "")
default_schema = "public" if database_url.startswith(("postgres://", "postgresql://")) else None
db = SQLAlchemy(metadata=MetaData(schema=default_schema))


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

    # Website login (same wallet/user record as the WhatsApp bot)
    email = db.Column(db.String(120), unique=True, nullable=True, index=True)
    password_hash = db.Column(db.String(255), nullable=True)

    # User Flow & State Management
    current_state = db.Column(db.String(50), default="IDLE", nullable=False)
    state_data = db.Column(db.JSON, default=dict)
    is_escalated = db.Column(db.Boolean, default=False, nullable=False)

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


class InboundMessage(db.Model):
    __tablename__ = "inbound_messages"

    id = db.Column(db.Integer, primary_key=True)
    provider = db.Column(db.String(30), nullable=False, default="whatsapp")
    message_id = db.Column(db.String(200), nullable=False)
    sender = db.Column(db.String(50), nullable=True)
    status = db.Column(db.String(20), nullable=False, default="PROCESSING")
    created_at = db.Column(db.DateTime(timezone=True), default=utc_now, nullable=False)
    processed_at = db.Column(db.DateTime(timezone=True), nullable=True)

    __table_args__ = (
        db.UniqueConstraint("provider", "message_id", name="uq_inbound_provider_message"),
    )


class SavedService(db.Model):
    __tablename__ = 'saved_services'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    service_type = db.Column(db.String(20), nullable=False, index=True)
    label = db.Column(db.String(100), nullable=False)
    provider = db.Column(db.String(50), nullable=True)
    identifier = db.Column(db.String(100), nullable=False)
    service_metadata = db.Column(db.JSON, default=dict)
    created_at = db.Column(db.DateTime(timezone=True), default=utc_now, nullable=False)

    def __repr__(self):
        return f"<SavedService {self.service_type} - {self.label}>"


class ScheduledTask(db.Model):
    __tablename__ = 'scheduled_tasks'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    
    # frequency: e.g. "daily", "weekly", "monthly"
    frequency = db.Column(db.String(20), nullable=False)
    next_run = db.Column(db.DateTime(timezone=True), nullable=False)
    
    # JSON data for the agent's execute_tool
    tool_name = db.Column(db.String(50), nullable=False)
    tool_kwargs = db.Column(db.JSON, nullable=False)
    
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=utc_now, nullable=False)
    
    def __repr__(self):
        return f"<ScheduledTask {self.tool_name} ({self.frequency}) for User {self.user_id}>"


class ServiceMarkup(db.Model):
    __tablename__ = 'service_markups'

    id = db.Column(db.Integer, primary_key=True)
    service_type = db.Column(db.String(20), unique=True, nullable=False, index=True)
    markup_amount = db.Column(db.Numeric(10, 2), default=0.00, nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)


class AdminAuditLog(db.Model):
    __tablename__ = 'admin_audit_logs'

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(100), nullable=False, index=True)
    action = db.Column(db.String(100), nullable=False, index=True)
    success = db.Column(db.Boolean, default=False, nullable=False, index=True)
    reason = db.Column(db.String(255), nullable=True)
    ip_address = db.Column(db.String(45), nullable=True, index=True)
    user_agent = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), default=utc_now, nullable=False, index=True)

    def __repr__(self):
        return f"<AdminAuditLog {self.username} - {self.action} - {'SUCCESS' if self.success else 'FAILURE'}>"


class PaymentFeeTier(db.Model):
    __tablename__ = 'payment_fee_tiers'

    id = db.Column(db.Integer, primary_key=True)
    label = db.Column(db.String(50), unique=True, nullable=False, index=True)
    min_amount = db.Column(db.Numeric(10, 2), default=0.00, nullable=False)
    max_amount = db.Column(db.Numeric(10, 2), nullable=True)
    fee_percentage = db.Column(db.Numeric(5, 2), default=0.00, nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)