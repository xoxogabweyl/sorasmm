import os
from pathlib import Path

from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import text


db = SQLAlchemy()


def create_app():
    app = Flask(__name__)
    instance_dir = Path(app.instance_path)
    instance_dir.mkdir(parents=True, exist_ok=True)

    default_sqlite_uri = f"sqlite:///{(instance_dir / 'smm_python.db').as_posix()}"
    database_uri = (
        os.getenv("SQLALCHEMY_DATABASE_URI")
        or os.getenv("DATABASE_URL")
        or default_sqlite_uri
    )
    if database_uri.startswith("postgres://"):
        database_uri = "postgresql://" + database_uri[len("postgres://") :]

    app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "change-me-in-production")
    app.config["SQLALCHEMY_DATABASE_URI"] = database_uri
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    db.init_app(app)

    from .models import User, Service, Order, Ticket, ServiceProvider, SiteSetting  # noqa: F401
    from .routes import register_routes

    register_routes(app)

    # Auto-initialize SQLite tables for first run to avoid "no such table" errors.
    with app.app_context():
        db.create_all()
        ensure_schema_updates()
        seed_data()

    @app.cli.command("init-db")
    def init_db_command():
        """Create tables and seed initial data."""
        with app.app_context():
            db.create_all()
            ensure_schema_updates()
            seed_data()
            print("Database initialized and seeded.")

    return app


def seed_data():
    from .models import User, Service
    from werkzeug.security import generate_password_hash

    if not User.query.filter_by(is_admin=True).first():
        admin_username = os.getenv("SMM_ADMIN_USERNAME", "admin")
        admin_email = os.getenv("SMM_ADMIN_EMAIL", "admin@example.com")
        admin_password = os.getenv("SMM_ADMIN_PASSWORD", "ChangeMe@123")
        admin = User(
            username=admin_username,
            email=admin_email,
            password_hash=generate_password_hash(admin_password),
            is_admin=True,
            balance=0.0,
        )
        db.session.add(admin)

    if Service.query.count() == 0:
        services = [
            Service(
                name="Instagram Followers",
                description="Real-looking Instagram follower growth for profile authority.",
                category="Instagram",
                price_per_1000=2.50,
                min_qty=100,
                max_qty=100000,
                status="active",
            ),
            Service(
                name="Instagram Likes",
                description="Fast likes delivery for posts and reels to improve engagement.",
                category="Instagram",
                price_per_1000=1.20,
                min_qty=50,
                max_qty=50000,
                status="active",
            ),
            Service(
                name="YouTube Views",
                description="High-retention YouTube views to improve social proof on videos.",
                category="YouTube",
                price_per_1000=1.00,
                min_qty=1000,
                max_qty=1000000,
                status="active",
            ),
            Service(
                name="TikTok Followers",
                description="Follower growth package for TikTok creator accounts.",
                category="TikTok",
                price_per_1000=3.10,
                min_qty=100,
                max_qty=50000,
                status="active",
            ),
        ]
        db.session.add_all(services)

    db.session.commit()


def ensure_schema_updates():
    # SQLite-only lightweight migration path.
    if db.session.get_bind().dialect.name != "sqlite":
        return

    # Lightweight schema migration for existing SQLite DBs.
    service_columns = {
        row[1]
        for row in db.session.execute(text("PRAGMA table_info(service)")).fetchall()
    }
    if "description" not in service_columns:
        db.session.execute(
            text("ALTER TABLE service ADD COLUMN description TEXT NOT NULL DEFAULT ''")
        )
        db.session.commit()
    if "average_time" not in service_columns:
        db.session.execute(
            text("ALTER TABLE service ADD COLUMN average_time TEXT NOT NULL DEFAULT ''")
        )
        db.session.commit()
    if "provider_id" not in service_columns:
        db.session.execute(
            text("ALTER TABLE service ADD COLUMN provider_id INTEGER")
        )
        db.session.commit()
    if "provider_service_id" not in service_columns:
        db.session.execute(
            text("ALTER TABLE service ADD COLUMN provider_service_id TEXT")
        )
        db.session.commit()
    if "provider_refill" not in service_columns:
        db.session.execute(
            text("ALTER TABLE service ADD COLUMN provider_refill BOOLEAN NOT NULL DEFAULT 0")
        )
        db.session.commit()
    if "provider_cancel" not in service_columns:
        db.session.execute(
            text("ALTER TABLE service ADD COLUMN provider_cancel BOOLEAN NOT NULL DEFAULT 0")
        )
        db.session.commit()

    user_columns = {
        row[1]
        for row in db.session.execute(text("PRAGMA table_info(user)")).fetchall()
    }
    if "is_banned" not in user_columns:
        db.session.execute(
            text("ALTER TABLE user ADD COLUMN is_banned BOOLEAN NOT NULL DEFAULT 0")
        )
        db.session.commit()

    order_columns = {
        row[1]
        for row in db.session.execute(text("PRAGMA table_info('order')")).fetchall()
    }
    if "provider_id" not in order_columns:
        db.session.execute(
            text("ALTER TABLE 'order' ADD COLUMN provider_id INTEGER")
        )
        db.session.commit()
    if "provider_order_id" not in order_columns:
        db.session.execute(
            text("ALTER TABLE 'order' ADD COLUMN provider_order_id TEXT")
        )
        db.session.commit()
    if "start_count" not in order_columns:
        db.session.execute(
            text("ALTER TABLE 'order' ADD COLUMN start_count INTEGER")
        )
        db.session.commit()
    if "remains" not in order_columns:
        db.session.execute(
            text("ALTER TABLE 'order' ADD COLUMN remains INTEGER")
        )
        db.session.commit()
    if "refill_status" not in order_columns:
        db.session.execute(
            text("ALTER TABLE 'order' ADD COLUMN refill_status TEXT")
        )
        db.session.commit()
    if "cancel_requested" not in order_columns:
        db.session.execute(
            text("ALTER TABLE 'order' ADD COLUMN cancel_requested BOOLEAN NOT NULL DEFAULT 0")
        )
        db.session.commit()
    if "provider_last_check_at" not in order_columns:
        db.session.execute(
            text("ALTER TABLE 'order' ADD COLUMN provider_last_check_at DATETIME")
        )
        db.session.commit()
    if "refund_applied" not in order_columns:
        db.session.execute(
            text("ALTER TABLE 'order' ADD COLUMN refund_applied BOOLEAN NOT NULL DEFAULT 0")
        )
        db.session.commit()
    if "refunded_at" not in order_columns:
        db.session.execute(
            text("ALTER TABLE 'order' ADD COLUMN refunded_at DATETIME")
        )
        db.session.commit()
