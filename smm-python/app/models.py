from datetime import datetime
from . import db


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(40), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    balance = db.Column(db.Float, default=0.0)
    is_admin = db.Column(db.Boolean, default=False)
    is_banned = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Service(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    description = db.Column(db.Text, nullable=False, default="")
    average_time = db.Column(db.String(120), nullable=False, default="")
    category = db.Column(db.String(80), nullable=False)
    price_per_1000 = db.Column(db.Float, nullable=False)
    min_qty = db.Column(db.Integer, nullable=False)
    max_qty = db.Column(db.Integer, nullable=False)
    status = db.Column(db.String(20), default="active")
    provider_id = db.Column(db.Integer, db.ForeignKey("service_provider.id"), nullable=True)
    provider_service_id = db.Column(db.String(80), nullable=True)
    provider_refill = db.Column(db.Boolean, default=False, nullable=False)
    provider_cancel = db.Column(db.Boolean, default=False, nullable=False)


class ServiceProvider(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), unique=True, nullable=False)
    api_url = db.Column(db.String(255), nullable=False)
    api_key = db.Column(db.String(255), nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    last_synced_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    services = db.relationship("Service", backref="provider", lazy=True)


class Order(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    service_id = db.Column(db.Integer, db.ForeignKey("service.id"), nullable=False)
    link = db.Column(db.String(255), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    charge = db.Column(db.Float, nullable=False)
    status = db.Column(db.String(20), default="pending")
    provider_id = db.Column(db.Integer, db.ForeignKey("service_provider.id"), nullable=True)
    provider_order_id = db.Column(db.String(80), nullable=True)
    start_count = db.Column(db.Integer, nullable=True)
    remains = db.Column(db.Integer, nullable=True)
    refill_status = db.Column(db.String(40), nullable=True)
    cancel_requested = db.Column(db.Boolean, default=False, nullable=False)
    provider_last_check_at = db.Column(db.DateTime, nullable=True)
    refund_applied = db.Column(db.Boolean, default=False, nullable=False)
    refunded_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship("User", backref=db.backref("orders", lazy=True))
    service = db.relationship("Service", backref=db.backref("orders", lazy=True))
    provider = db.relationship("ServiceProvider", backref=db.backref("orders", lazy=True))


class Ticket(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    subject = db.Column(db.String(120), nullable=False)
    message = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(20), default="open")
    admin_reply = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship("User", backref=db.backref("tickets", lazy=True))


class SiteSetting(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(80), unique=True, nullable=False)
    value = db.Column(db.Text, nullable=False, default="")
