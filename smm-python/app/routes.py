import json
import re
from datetime import datetime
from functools import wraps
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

from flask import (
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from sqlalchemy import or_
from werkzeug.security import check_password_hash, generate_password_hash

from . import db, seed_data
from .models import Order, Service, ServiceProvider, SiteSetting, Ticket, User


def current_user():
    uid = session.get("user_id")
    if not uid:
        return None
    return User.query.get(uid)


def login_required(view_func):
    @wraps(view_func)
    def wrapper(*args, **kwargs):
        user = current_user()
        if not user:
            flash("Please login first.", "warning")
            return redirect(url_for("login"))
        if user.is_banned:
            session.clear()
            flash("Your account has been banned. Contact support.", "danger")
            return redirect(url_for("login"))
        return view_func(*args, **kwargs)

    return wrapper


def admin_required(view_func):
    @wraps(view_func)
    def wrapper(*args, **kwargs):
        user = current_user()
        if not user or not user.is_admin:
            flash("Admin access required.", "danger")
            return redirect(url_for("dashboard"))
        return view_func(*args, **kwargs)

    return wrapper


def register_routes(app):
    interface_setting_defaults = {
        "site_name": "SORA",
        "site_title": "Sora SMM",
        "site_logo_url": "",
        "brand_color": "#2f74ff",
        "brand_dark_color": "#1f5dda",
        "accent_color": "#18b8ff",
        "bg_main_color": "#eef4ff",
        "bg_accent_color": "#dfeaff",
        "text_main_color": "#152746",
        "text_muted_color": "#4d658f",
        "payment_mode_manual": "1",
        "payment_mode_paypal": "0",
        "payment_mode_stripe": "0",
        "payment_mode_crypto": "0",
        "payment_mode_bank_transfer": "0",
        "payment_note": "Contact support to add funds to your balance.",
        "custom_css": "",
    }
    payment_mode_labels = {
        "payment_mode_manual": "Manual / Support",
        "payment_mode_paypal": "PayPal",
        "payment_mode_stripe": "Card (Stripe)",
        "payment_mode_crypto": "Cryptocurrency",
        "payment_mode_bank_transfer": "Bank Transfer",
    }
    currency_options = {
        "USD": {"label": "USD ($)", "symbol": "$", "rate": 1.0},
        "EUR": {"label": "EUR (€)", "symbol": "€", "rate": 0.92},
        "GBP": {"label": "GBP (£)", "symbol": "£", "rate": 0.79},
        "SGD": {"label": "SGD (S$)", "symbol": "S$", "rate": 1.35},
        "INR": {"label": "INR (₹)", "symbol": "₹", "rate": 83.0},
        "PHP": {"label": "PHP (₱)", "symbol": "₱", "rate": 56.0},
    }
    color_pattern = re.compile(r"^#[0-9a-fA-F]{6}$")

    def get_interface_settings():
        keys = list(interface_setting_defaults.keys())
        settings = {key: default for key, default in interface_setting_defaults.items()}
        rows = SiteSetting.query.filter(SiteSetting.key.in_(keys)).all()
        for row in rows:
            settings[row.key] = row.value or interface_setting_defaults.get(row.key, "")
        return settings

    def set_site_setting(key, value):
        setting = SiteSetting.query.filter_by(key=key).first()
        if not setting:
            setting = SiteSetting(key=key, value=value)
            db.session.add(setting)
            return
        setting.value = value

    def get_enabled_payment_modes(settings):
        enabled = []
        for setting_key, label in payment_mode_labels.items():
            raw_value = str(settings.get(setting_key, "0")).strip().lower()
            if raw_value in {"1", "true", "yes", "on", "enabled"}:
                enabled.append(label)
        return enabled

    def get_currency_meta():
        selected = session.get("currency")
        if not selected:
            selected = request.cookies.get("currency", "USD")
        selected = str(selected or "USD").upper().strip()
        if selected not in currency_options:
            selected = "USD"
        session["currency"] = selected
        return selected, currency_options[selected]

    def user_to_dict(user):
        return {
            "id": user.id,
            "username": user.username,
            "email": user.email,
            "balance": round(user.balance or 0.0, 4),
            "is_admin": bool(user.is_admin),
            "is_banned": bool(user.is_banned),
            "created_at": user.created_at.isoformat() if user.created_at else None,
        }

    def _to_float(value, default=0.0):
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    def _to_int(value, default=1):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return default

    def _to_bool(value):
        if isinstance(value, bool):
            return value
        text = str(value or "").strip().lower()
        return text in {"1", "true", "yes", "enabled", "on"}

    def _normalize_service_status(raw_status):
        status = str(raw_status or "active").strip().lower()
        if status in {"active", "enabled", "available", "on"}:
            return "active"
        return "paused"

    def _format_duration_label(total_seconds):
        try:
            seconds = int(round(float(total_seconds)))
        except (TypeError, ValueError):
            return ""
        if seconds <= 0:
            return ""

        days, rem = divmod(seconds, 86400)
        hours, rem = divmod(rem, 3600)
        minutes, _ = divmod(rem, 60)
        parts = []
        if days > 0:
            parts.append(f"{days} day{'s' if days != 1 else ''}")
        if hours > 0:
            parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
        if minutes > 0 or not parts:
            parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")
        return " ".join(parts[:2])

    def _average_completion_time_map(service_ids):
        unique_ids = []
        seen = set()
        for service_id in service_ids or []:
            try:
                sid = int(service_id)
            except (TypeError, ValueError):
                continue
            if sid in seen:
                continue
            seen.add(sid)
            unique_ids.append(sid)

        if not unique_ids:
            return {}

        rows = (
            db.session.query(Order.service_id, Order.created_at, Order.provider_last_check_at)
            .filter(
                Order.service_id.in_(unique_ids),
                Order.status == "completed",
                Order.created_at.isnot(None),
            )
            .all()
        )

        totals = {}
        counts = {}
        for service_id, created_at, last_check_at in rows:
            if not created_at:
                continue
            completion_at = last_check_at or datetime.utcnow()
            delta = (completion_at - created_at).total_seconds()
            if delta <= 0:
                continue
            totals[service_id] = totals.get(service_id, 0.0) + delta
            counts[service_id] = counts.get(service_id, 0) + 1

        labels = {}
        for service_id, count in counts.items():
            if count <= 0:
                continue
            avg_seconds = totals[service_id] / count
            label = _format_duration_label(avg_seconds)
            if label:
                labels[service_id] = label
        return labels

    def _normalize_provider_key(raw_key):
        return re.sub(r"[^a-z0-9]", "", str(raw_key or "").lower())

    def _clean_average_time(value):
        text = str(value or "").strip()
        if not text:
            return ""
        text = re.sub(r"\s+", " ", text)
        return text[:120].strip()

    def _extract_average_time(item):
        if not isinstance(item, dict):
            return ""

        # Providers use different keys for estimated/average delivery time.
        candidate_keys = (
            "average_time",
            "avg_time",
            "avg",
            "time",
            "delivery_time",
            "deliver_time",
            "avg_delivery_time",
            "average_delivery_time",
        )
        for key in candidate_keys:
            cleaned = _clean_average_time(item.get(key))
            if cleaned:
                return cleaned

        normalized = {}
        for raw_key, raw_value in item.items():
            normalized[_normalize_provider_key(raw_key)] = raw_value

        normalized_candidate_keys = (
            "averagetime",
            "avgtime",
            "timeavg",
            "deliverytime",
            "delivertime",
            "avgdeliverytime",
            "averagedeliverytime",
            "processtime",
            "processingtime",
        )
        for key in normalized_candidate_keys:
            cleaned = _clean_average_time(normalized.get(key))
            if cleaned:
                return cleaned

        for nested_key in ("details", "detail", "meta", "extra", "data", "info"):
            nested = item.get(nested_key)
            if isinstance(nested, dict):
                nested_time = _extract_average_time(nested)
                if nested_time:
                    return nested_time

        description = str(item.get("description") or "")
        if description:
            desc_patterns = (
                re.compile(r"(?:average|avg)\s*(?:delivery)?\s*time\s*[:\-]\s*([^\n\r|,;]+)", re.IGNORECASE),
                re.compile(r"\bETA\s*[:\-]\s*([^\n\r|,;]+)", re.IGNORECASE),
            )
            for pattern in desc_patterns:
                match = pattern.search(description)
                if match:
                    cleaned = _clean_average_time(match.group(1))
                    if cleaned:
                        return cleaned

        return ""

    def provider_api_request(provider, action, **params):
        payload = {"key": provider.api_key, "action": action}
        payload.update(params)
        encoded_payload = urlencode(payload).encode("utf-8")
        req = Request(
            provider.api_url,
            data=encoded_payload,
            headers={"Content-Type": "application/x-www-form-urlencoded", "User-Agent": "SORA-SMM/1.0"},
            method="POST",
        )

        try:
            with urlopen(req, timeout=20) as response:
                body = response.read().decode("utf-8", errors="replace")
        except HTTPError as exc:
            raise ValueError(f"Provider returned HTTP {exc.code}") from exc
        except URLError as exc:
            raise ValueError("Failed to reach provider API URL") from exc
        except Exception as exc:  # noqa: BLE001
            raise ValueError("Provider request failed") from exc

        try:
            parsed = json.loads(body)
        except json.JSONDecodeError as exc:
            raise ValueError("Provider response is not valid JSON") from exc

        if isinstance(parsed, dict) and parsed.get("error"):
            raise ValueError(str(parsed.get("error")))
        return parsed

    def normalize_order_status(raw_status):
        status = str(raw_status or "").strip().lower()
        if status in {"in progress", "in_progress", "processing"}:
            return "processing"
        if status in {"partial", "completed", "complete"}:
            return "completed"
        if status in {"canceled", "cancelled", "failed"}:
            return "canceled"
        if status in {"pending"}:
            return "pending"
        return status or "pending"

    def is_canceled_status(raw_status):
        return str(raw_status or "").strip().lower() in {"canceled", "cancelled"}

    def apply_cancellation_refund(order, previous_status=None):
        if not is_canceled_status(order.status):
            return False
        if previous_status is not None and is_canceled_status(previous_status):
            return False
        if order.refund_applied:
            return False
        if not order.user:
            return False

        refund_amount = round(order.charge or 0.0, 4)
        if refund_amount <= 0:
            order.refund_applied = True
            order.refunded_at = datetime.utcnow()
            return False

        order.user.balance = round((order.user.balance or 0.0) + refund_amount, 4)
        order.refund_applied = True
        order.refunded_at = datetime.utcnow()
        return True

    def fetch_provider_services(provider):
        parsed = provider_api_request(provider, "services")
        if isinstance(parsed, dict):
            if isinstance(parsed.get("services"), list):
                parsed = parsed.get("services")

        if not isinstance(parsed, list):
            raise ValueError("Provider response format is unsupported")

        normalized = []
        for item in parsed:
            if not isinstance(item, dict):
                continue

            provider_service_id = str(item.get("service") or item.get("id") or "").strip()
            name = str(item.get("name") or "").strip()
            if not provider_service_id or not name:
                continue

            min_qty = _to_int(item.get("min"), 1)
            max_qty = _to_int(item.get("max"), min_qty)
            if max_qty < min_qty:
                max_qty = min_qty

            normalized.append(
                {
                    "provider_service_id": provider_service_id,
                    "name": name,
                    "description": str(item.get("description") or "").strip(),
                    "average_time": _extract_average_time(item),
                    "category": str(item.get("category") or item.get("type") or "General").strip() or "General",
                    "price_per_1000": _to_float(item.get("rate"), 0.0),
                    "min_qty": max(1, min_qty),
                    "max_qty": max(1, max_qty),
                    "status": _normalize_service_status(item.get("status")),
                    "provider_refill": _to_bool(item.get("refill")),
                    "provider_cancel": _to_bool(item.get("cancel")),
                }
            )

        return normalized

    def place_provider_order(order):
        service = order.service
        if not service.provider_id or not service.provider_service_id:
            return

        provider = ServiceProvider.query.get(service.provider_id)
        if not provider or not provider.is_active:
            raise ValueError("Service provider is unavailable")

        parsed = provider_api_request(
            provider,
            "add",
            service=service.provider_service_id,
            link=order.link,
            quantity=order.quantity,
        )

        if not isinstance(parsed, dict):
            raise ValueError("Unexpected provider order response")
        provider_order_id = parsed.get("order")
        if provider_order_id is None:
            raise ValueError("Provider did not return order ID")

        order.provider_id = provider.id
        order.provider_order_id = str(provider_order_id)
        provider_status = parsed.get("status")
        if provider_status is None:
            provider_status = parsed.get("order_status")
        normalized_status = normalize_order_status(provider_status)
        order.status = (
            normalized_status
            if normalized_status in {"pending", "processing", "completed", "canceled"}
            else "pending"
        )

    def refresh_provider_order_status(order):
        if not order.provider_id or not order.provider_order_id:
            return False
        provider = ServiceProvider.query.get(order.provider_id)
        if not provider or not provider.is_active:
            return False

        parsed = provider_api_request(provider, "status", order=order.provider_order_id)
        if isinstance(parsed, dict) and str(order.provider_order_id) in parsed:
            parsed = parsed.get(str(order.provider_order_id))
        if not isinstance(parsed, dict):
            return False

        previous_status = order.status
        order.status = normalize_order_status(parsed.get("status"))
        if "start_count" in parsed:
            order.start_count = _to_int(parsed.get("start_count"), order.start_count or 0)
        if "remains" in parsed:
            order.remains = _to_int(parsed.get("remains"), order.remains or 0)
        apply_cancellation_refund(order, previous_status=previous_status)
        order.provider_last_check_at = datetime.utcnow()
        return True

    def sync_provider_services(provider):
        incoming_services = fetch_provider_services(provider)
        updated = 0
        skipped_new = 0

        for item in incoming_services:
            service = Service.query.filter_by(
                provider_id=provider.id,
                provider_service_id=item["provider_service_id"],
            ).first()

            if service:
                service.name = item["name"]
                service.description = item["description"]
                # Keep existing manual value if provider does not supply average time.
                if item["average_time"]:
                    service.average_time = item["average_time"]
                service.category = item["category"]
                service.price_per_1000 = item["price_per_1000"]
                service.min_qty = item["min_qty"]
                service.max_qty = item["max_qty"]
                service.status = item["status"]
                service.provider_refill = item["provider_refill"]
                service.provider_cancel = item["provider_cancel"]
                updated += 1
            else:
                # Do not auto-import new provider services from sync action.
                skipped_new += 1

        provider.last_synced_at = datetime.utcnow()
        db.session.commit()
        return updated, skipped_new, len(incoming_services)

    @app.context_processor
    def inject_user():
        user = current_user()
        interface_settings = get_interface_settings()
        enabled_payment_modes = get_enabled_payment_modes(interface_settings)
        currency_code, currency_meta = get_currency_meta()
        currency_rate = float(currency_meta["rate"])
        currency_symbol = str(currency_meta["symbol"])

        def convert_money(value):
            try:
                amount = float(value or 0.0)
            except (TypeError, ValueError):
                amount = 0.0
            return round(amount * currency_rate, 6)

        def format_money(value, decimals=2):
            try:
                digits = max(0, int(decimals))
            except (TypeError, ValueError):
                digits = 2
            converted = convert_money(value)
            return f"{currency_symbol}{converted:,.{digits}f}"

        context = {
            "active_user": user,
            "site_logo_url": interface_settings["site_logo_url"],
            "site_name": interface_settings["site_name"],
            "site_title": interface_settings["site_title"],
            "interface_settings": interface_settings,
            "enabled_payment_modes": enabled_payment_modes,
            "payment_note": interface_settings["payment_note"],
            "currency_code": currency_code,
            "currency_symbol": currency_symbol,
            "currency_rate": currency_rate,
            "currency_options": [
                {"code": code, "label": meta["label"]}
                for code, meta in currency_options.items()
            ],
            "convert_money": convert_money,
            "format_money": format_money,
            "nav_orders_badge": 0,
            "nav_tickets_badge": 0,
            "nav_canceled_orders_badge": 0,
            "nav_processing_orders_badge": 0,
        }
        if not user:
            return context

        if user.is_admin:
            context["nav_orders_badge"] = Order.query.filter_by(status="pending").count()
            context["nav_tickets_badge"] = Ticket.query.filter_by(status="open").count()
            context["nav_processing_orders_badge"] = Order.query.filter_by(status="processing").count()
        else:
            context["nav_orders_badge"] = Order.query.filter_by(
                user_id=user.id,
                status="pending",
            ).count()
            context["nav_tickets_badge"] = Ticket.query.filter_by(
                user_id=user.id,
                status="open",
            ).count()
            context["nav_processing_orders_badge"] = Order.query.filter_by(
                user_id=user.id,
                status="processing",
            ).count()
            context["nav_canceled_orders_badge"] = (
                Order.query.filter_by(user_id=user.id)
                .filter(Order.status.in_(["canceled", "cancelled"]))
                .count()
            )

        return context

    @app.route("/")
    def home():
        if current_user():
            return redirect(url_for("dashboard"))
        return redirect(url_for("login"))

    @app.route("/register", methods=["GET", "POST"])
    def register():
        if request.method == "POST":
            username = request.form.get("username", "").strip()
            email = request.form.get("email", "").strip().lower()
            password = request.form.get("password", "")

            if not username or not email or not password:
                flash("All fields are required.", "danger")
                return redirect(url_for("register"))

            if User.query.filter((User.username == username) | (User.email == email)).first():
                flash("Username or email already exists.", "danger")
                return redirect(url_for("register"))

            user = User(
                username=username,
                email=email,
                password_hash=generate_password_hash(password),
                balance=0.0,
                is_admin=False,
            )
            db.session.add(user)
            db.session.commit()
            flash("Account created. You can now login.", "success")
            return redirect(url_for("login"))

        return render_template("register.html", title="Register")

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "")

            user = User.query.filter_by(username=username).first()
            if not user or not check_password_hash(user.password_hash, password):
                flash("Invalid credentials.", "danger")
                return redirect(url_for("login"))
            if user.is_banned:
                flash("Your account has been banned. Contact support.", "danger")
                return redirect(url_for("login"))

            session["user_id"] = user.id
            flash("Logged in successfully.", "success")
            if user.is_admin:
                return redirect(url_for("admin_dashboard"))
            return redirect(url_for("dashboard"))

        return render_template("login.html", title="Login")

    @app.route("/logout")
    def logout():
        session.clear()
        flash("Logged out.", "info")
        return redirect(url_for("login"))

    @app.route("/set-currency", methods=["POST"])
    def set_currency():
        selected = str(request.form.get("currency", "USD") or "USD").upper().strip()
        if selected not in currency_options:
            selected = "USD"
            flash("Unsupported currency selected.", "warning")

        session["currency"] = selected

        def redirect_with_currency_cookie(target):
            response = redirect(target)
            response.set_cookie(
                "currency",
                selected,
                max_age=60 * 60 * 24 * 365,
                samesite="Lax",
            )
            return response

        if selected in currency_options:
            next_path = str(request.form.get("next", "") or "").strip()
        else:
            next_path = ""
        if next_path.startswith("/") and not next_path.startswith("//"):
            return redirect_with_currency_cookie(next_path)

        referrer = request.referrer or ""
        if referrer:
            parsed = urlparse(referrer)
            if not parsed.netloc or parsed.netloc == request.host:
                safe_path = parsed.path or "/"
                if parsed.query:
                    safe_path += f"?{parsed.query}"
                return redirect_with_currency_cookie(safe_path)

        if current_user():
            return redirect_with_currency_cookie(url_for("dashboard"))
        return redirect_with_currency_cookie(url_for("login"))

    @app.route("/dashboard")
    @login_required
    def dashboard():
        user = current_user()
        if user.is_admin:
            return redirect(url_for("admin_dashboard"))
        orders_count = Order.query.filter_by(user_id=user.id).count()
        pending_count = Order.query.filter_by(user_id=user.id, status="pending").count()
        ticket_count = Ticket.query.filter_by(user_id=user.id).count()
        recent_orders = (
            Order.query.filter_by(user_id=user.id)
            .order_by(Order.created_at.desc())
            .limit(5)
            .all()
        )
        return render_template(
            "dashboard.html",
            title="Dashboard",
            orders_count=orders_count,
            pending_count=pending_count,
            ticket_count=ticket_count,
            recent_orders=recent_orders,
        )

    @app.route("/account", methods=["GET", "POST"])
    @login_required
    def account():
        user = current_user()
        if request.method == "POST":
            current_password = request.form.get("current_password", "")
            new_password = request.form.get("new_password", "")
            confirm_password = request.form.get("confirm_password", "")

            if not current_password or not new_password or not confirm_password:
                flash("All password fields are required.", "danger")
                return redirect(url_for("account"))
            if not check_password_hash(user.password_hash, current_password):
                flash("Current password is incorrect.", "danger")
                return redirect(url_for("account"))
            if len(new_password) < 6:
                flash("New password must be at least 6 characters.", "danger")
                return redirect(url_for("account"))
            if new_password != confirm_password:
                flash("New password and confirmation do not match.", "danger")
                return redirect(url_for("account"))

            user.password_hash = generate_password_hash(new_password)
            db.session.commit()
            flash("Password updated successfully.", "success")
            return redirect(url_for("account"))

        orders_count = Order.query.filter_by(user_id=user.id).count()
        tickets_count = Ticket.query.filter_by(user_id=user.id).count()
        return render_template(
            "account.html",
            title="Account",
            orders_count=orders_count,
            tickets_count=tickets_count,
        )

    @app.route("/services")
    @login_required
    def services():
        service_list = Service.query.filter_by(status="active").order_by(Service.category, Service.id).all()
        return render_template("services.html", title="Services", services=service_list)

    @app.route("/orders/new", methods=["GET", "POST"])
    @login_required
    def new_order():
        user = current_user()
        services = Service.query.filter_by(status="active").order_by(Service.id).all()
        completion_avg_map = _average_completion_time_map([service.id for service in services])
        for service in services:
            service.display_average_time = (
                (service.average_time or "").strip()
                or completion_avg_map.get(service.id, "")
            )

        if request.method == "POST":
            service_id = request.form.get("service_id", type=int)
            link = request.form.get("link", "").strip()
            qty = request.form.get("quantity", type=int)

            service = Service.query.get(service_id)
            if not service:
                flash("Selected service is invalid.", "danger")
                return redirect(url_for("new_order"))

            if not link:
                flash("Link is required.", "danger")
                return redirect(url_for("new_order"))

            if qty is None or qty < service.min_qty or qty > service.max_qty:
                flash(f"Quantity must be between {service.min_qty} and {service.max_qty}.", "danger")
                return redirect(url_for("new_order"))

            charge = round((qty / 1000) * service.price_per_1000, 4)
            if user.balance < charge:
                flash("Insufficient balance.", "danger")
                return redirect(url_for("new_order"))

            user.balance = round(user.balance - charge, 4)
            order = Order(
                user_id=user.id,
                service_id=service.id,
                link=link,
                quantity=qty,
                charge=charge,
                status="pending",
            )
            db.session.add(order)
            try:
                db.session.flush()
                place_provider_order(order)
                db.session.commit()
            except ValueError as exc:
                db.session.rollback()
                flash(f"Provider order failed: {exc}", "danger")
                return redirect(url_for("new_order"))
            except Exception:  # noqa: BLE001
                db.session.rollback()
                flash("Could not place order right now. Please try again.", "danger")
                return redirect(url_for("new_order"))
            flash(f"Order #{order.id} placed successfully.", "success")
            return redirect(url_for("orders"))

        return render_template("new_order.html", title="New Order", services=services)

    @app.route("/orders", methods=["GET", "POST"])
    @login_required
    def orders():
        user = current_user()
        if request.method == "POST":
            action = request.form.get("order_action", "").strip().lower()
            order_id = request.form.get("order_id", type=int)
            order = Order.query.filter_by(id=order_id, user_id=user.id).first()
            if not order:
                flash("Order not found.", "danger")
                return redirect(url_for("orders"))

            if action == "refresh_status":
                try:
                    if refresh_provider_order_status(order):
                        db.session.commit()
                        flash(f"Order #{order.id} status synced.", "success")
                    else:
                        flash("This order is not linked to an active provider.", "warning")
                except ValueError as exc:
                    db.session.rollback()
                    flash(f"Status sync failed: {exc}", "danger")
            elif action == "request_cancel":
                if not order.service.provider_cancel:
                    flash("Cancel is not supported for this service.", "warning")
                    return redirect(url_for("orders"))
                if not order.provider_id or not order.provider_order_id:
                    flash("This order is not linked to a provider.", "warning")
                    return redirect(url_for("orders"))
                provider = ServiceProvider.query.get(order.provider_id)
                if not provider or not provider.is_active:
                    flash("Provider is unavailable.", "danger")
                    return redirect(url_for("orders"))
                try:
                    provider_api_request(provider, "cancel", order=order.provider_order_id)
                    order.cancel_requested = True
                    db.session.commit()
                    flash(f"Cancel requested for order #{order.id}.", "success")
                except ValueError as exc:
                    db.session.rollback()
                    flash(f"Cancel failed: {exc}", "danger")
            elif action == "request_refill":
                if not order.service.provider_refill:
                    flash("Refill is not supported for this service.", "warning")
                    return redirect(url_for("orders"))
                if not order.provider_id or not order.provider_order_id:
                    flash("This order is not linked to a provider.", "warning")
                    return redirect(url_for("orders"))
                provider = ServiceProvider.query.get(order.provider_id)
                if not provider or not provider.is_active:
                    flash("Provider is unavailable.", "danger")
                    return redirect(url_for("orders"))
                try:
                    response = provider_api_request(provider, "refill", order=order.provider_order_id)
                    refill_id = None
                    if isinstance(response, dict):
                        refill_id = response.get("refill")
                    order.refill_status = f"requested#{refill_id}" if refill_id else "requested"
                    db.session.commit()
                    flash(f"Refill requested for order #{order.id}.", "success")
                except ValueError as exc:
                    db.session.rollback()
                    flash(f"Refill failed: {exc}", "danger")

            return redirect(url_for("orders"))

        order_list = Order.query.filter_by(user_id=user.id).order_by(Order.created_at.desc()).all()
        return render_template("orders.html", title="Orders", orders=order_list)

    @app.route("/tickets", methods=["GET", "POST"])
    @login_required
    def tickets():
        user = current_user()

        if request.method == "POST":
            subject = request.form.get("subject", "").strip()
            message = request.form.get("message", "").strip()
            if not subject or not message:
                flash("Subject and message are required.", "danger")
                return redirect(url_for("tickets"))

            ticket = Ticket(user_id=user.id, subject=subject, message=message, status="open")
            db.session.add(ticket)
            db.session.commit()
            flash("Ticket submitted.", "success")
            return redirect(url_for("tickets"))

        ticket_list = Ticket.query.filter_by(user_id=user.id).order_by(Ticket.created_at.desc()).all()
        return render_template("tickets.html", title="Tickets", tickets=ticket_list)

    @app.route("/admin")
    @app.route("/admin-panel")
    @login_required
    @admin_required
    def admin_dashboard():
        users_count = User.query.filter_by(is_admin=False).count()
        services_count = Service.query.count()
        total_orders = Order.query.count()
        pending_orders = Order.query.filter_by(status="pending").count()
        completed_orders = Order.query.filter_by(status="completed").count()
        canceled_orders = Order.query.filter(Order.status.in_(["canceled", "cancelled"])).count()
        open_tickets = Ticket.query.filter_by(status="open").count()

        recent_orders = Order.query.order_by(Order.created_at.desc()).limit(10).all()
        return render_template(
            "admin/dashboard.html",
            title="Admin Dashboard",
            users_count=users_count,
            services_count=services_count,
            total_orders=total_orders,
            pending_orders=pending_orders,
            completed_orders=completed_orders,
            canceled_orders=canceled_orders,
            open_tickets=open_tickets,
            recent_orders=recent_orders,
        )

    @app.route("/admin/settings", methods=["GET", "POST"])
    @app.route("/admin-panel/settings", methods=["GET", "POST"])
    @login_required
    @admin_required
    def admin_settings():
        if request.method == "POST":
            payment_mode_keys = [
                "payment_mode_manual",
                "payment_mode_paypal",
                "payment_mode_stripe",
                "payment_mode_crypto",
                "payment_mode_bank_transfer",
            ]
            submitted = {
                "site_name": request.form.get("site_name", "").strip(),
                "site_title": request.form.get("site_title", "").strip(),
                "site_logo_url": request.form.get("site_logo_url", "").strip(),
                "brand_color": request.form.get("brand_color", "").strip(),
                "brand_dark_color": request.form.get("brand_dark_color", "").strip(),
                "accent_color": request.form.get("accent_color", "").strip(),
                "bg_main_color": request.form.get("bg_main_color", "").strip(),
                "bg_accent_color": request.form.get("bg_accent_color", "").strip(),
                "text_main_color": request.form.get("text_main_color", "").strip(),
                "text_muted_color": request.form.get("text_muted_color", "").strip(),
                "payment_note": request.form.get("payment_note", "").strip(),
                "custom_css": request.form.get("custom_css", "").strip(),
            }
            for setting_key in payment_mode_keys:
                submitted[setting_key] = "1" if request.form.get(setting_key) else "0"

            if not submitted["site_name"]:
                flash("Site name cannot be empty.", "danger")
                return redirect(url_for("admin_settings"))
            if len(submitted["site_name"]) > 60:
                flash("Site name must be 60 characters or less.", "danger")
                return redirect(url_for("admin_settings"))
            if not submitted["site_title"]:
                flash("Site title cannot be empty.", "danger")
                return redirect(url_for("admin_settings"))
            if len(submitted["site_title"]) > 120:
                flash("Site title must be 120 characters or less.", "danger")
                return redirect(url_for("admin_settings"))
            if submitted["site_logo_url"] and not submitted["site_logo_url"].startswith(("http://", "https://", "/")):
                flash("Logo URL must start with http://, https://, or /", "danger")
                return redirect(url_for("admin_settings"))

            for color_key in [
                "brand_color",
                "brand_dark_color",
                "accent_color",
                "bg_main_color",
                "bg_accent_color",
                "text_main_color",
                "text_muted_color",
            ]:
                if not color_pattern.match(submitted[color_key]):
                    flash(f"{color_key.replace('_', ' ').title()} must be a valid hex color.", "danger")
                    return redirect(url_for("admin_settings"))

            if len(submitted["payment_note"]) > 500:
                flash("Payment note must be 500 characters or less.", "danger")
                return redirect(url_for("admin_settings"))
            if len(submitted["custom_css"]) > 12000:
                flash("Custom CSS is too long. Keep it under 12,000 characters.", "danger")
                return redirect(url_for("admin_settings"))

            for key, value in submitted.items():
                set_site_setting(key, value)

            db.session.commit()
            flash("Interface settings updated.", "success")
            return redirect(url_for("admin_settings"))

        return render_template(
            "admin/settings.html",
            title="Admin Settings",
            settings=get_interface_settings(),
        )

    @app.route("/admin/services", methods=["GET", "POST"])
    @app.route("/admin-panel/services", methods=["GET", "POST"])
    @login_required
    @admin_required
    def admin_services():
        if request.method == "POST":
            action_values = request.form.getlist("action")
            action = action_values[-1] if action_values else "add"
            if action in {"bulk_update_status", "bulk_delete"}:
                raw_ids = request.form.getlist("service_ids")
                service_ids = []
                for value in raw_ids:
                    try:
                        service_ids.append(int(value))
                    except (TypeError, ValueError):
                        continue

                if not service_ids:
                    flash("Select at least one service.", "warning")
                    return redirect(url_for("admin_services"))

                if action == "bulk_delete":
                    deleted = Service.query.filter(Service.id.in_(service_ids)).delete(synchronize_session=False)
                    db.session.commit()
                    flash(f"Deleted {deleted} services.", "success")
                else:
                    bulk_status = request.form.get("bulk_status", "").strip().lower()
                    if bulk_status not in {"active", "paused"}:
                        flash("Invalid bulk status.", "danger")
                        return redirect(url_for("admin_services"))

                    updated = (
                        Service.query.filter(Service.id.in_(service_ids))
                        .update({"status": bulk_status}, synchronize_session=False)
                    )
                    db.session.commit()
                    flash(f"Updated {updated} services to {bulk_status}.", "success")
            elif action == "update_status":
                service_id = request.form.get("service_id", type=int)
                new_status = request.form.get("status", "active").strip().lower()
                service = Service.query.get(service_id)
                allowed_statuses = {"active", "paused"}

                if not service:
                    flash("Service not found.", "danger")
                    return redirect(url_for("admin_services"))
                if new_status not in allowed_statuses:
                    flash("Invalid status.", "danger")
                    return redirect(url_for("admin_services"))

                service.status = new_status
                db.session.commit()
                flash(f"Service #{service.id} status updated to {new_status}.", "success")
            elif action == "update_service":
                service_id = request.form.get("service_id", type=int)
                service = Service.query.get(service_id)
                allowed_statuses = {"active", "paused"}

                if not service:
                    flash("Service not found.", "danger")
                    return redirect(url_for("admin_services"))

                service.name = request.form.get("name", "").strip()
                service.description = request.form.get("description", "").strip()
                service.average_time = request.form.get("average_time", "").strip()
                service.category = request.form.get("category", "General").strip() or "General"
                service.price_per_1000 = request.form.get("price_per_1000", type=float) or 0.0
                service.min_qty = request.form.get("min_qty", type=int) or 1
                service.max_qty = request.form.get("max_qty", type=int) or 1
                service.status = request.form.get("status", "active").strip().lower()
                service.provider_refill = bool(request.form.get("provider_refill"))
                service.provider_cancel = bool(request.form.get("provider_cancel"))

                if not service.name:
                    flash("Service name is required.", "danger")
                    return redirect(url_for("admin_services"))
                if service.max_qty < service.min_qty:
                    flash("Max quantity cannot be less than min quantity.", "danger")
                    return redirect(url_for("admin_services"))
                if service.status not in allowed_statuses:
                    flash("Invalid status.", "danger")
                    return redirect(url_for("admin_services"))

                db.session.commit()
                flash(f"Service #{service.id} updated.", "success")
            else:
                provider_id = request.form.get("provider_id", type=int)
                provider_service_id = request.form.get("provider_service_id", "").strip() or None
                provider = None
                if provider_id:
                    provider = ServiceProvider.query.get(provider_id)
                    if not provider:
                        flash("Selected provider is invalid.", "danger")
                        return redirect(url_for("admin_services"))

                service = Service(
                    name=request.form.get("name", "").strip(),
                    description=request.form.get("description", "").strip(),
                    average_time=request.form.get("average_time", "").strip(),
                    category=request.form.get("category", "General").strip() or "General",
                    price_per_1000=request.form.get("price_per_1000", type=float) or 0.0,
                    min_qty=request.form.get("min_qty", type=int) or 1,
                    max_qty=request.form.get("max_qty", type=int) or 1,
                    status=request.form.get("status", "active"),
                    provider_id=provider.id if provider else None,
                    provider_service_id=provider_service_id,
                    provider_refill=bool(request.form.get("provider_refill")),
                    provider_cancel=bool(request.form.get("provider_cancel")),
                )

                if not service.name:
                    flash("Service name is required.", "danger")
                    return redirect(url_for("admin_services"))
                if service.max_qty < service.min_qty:
                    flash("Max quantity cannot be less than min quantity.", "danger")
                    return redirect(url_for("admin_services"))

                db.session.add(service)
                db.session.commit()
                flash("Service added.", "success")
            return redirect(url_for("admin_services"))

        service_q = request.args.get("q", "").strip()
        service_status = request.args.get("status", "all").strip().lower()
        service_provider = request.args.get("provider", "all").strip().lower()
        service_category = request.args.get("category", "all").strip()
        services_query = Service.query.outerjoin(
            ServiceProvider,
            Service.provider_id == ServiceProvider.id,
        )

        if service_q:
            if service_q.isdigit():
                services_query = services_query.filter(Service.id == int(service_q))
            else:
                term = f"%{service_q}%"
                services_query = services_query.filter(
                    or_(
                        Service.name.ilike(term),
                        Service.description.ilike(term),
                        Service.category.ilike(term),
                        Service.provider_service_id.ilike(term),
                        ServiceProvider.name.ilike(term),
                    )
                )

        if service_status in {"active", "paused"}:
            services_query = services_query.filter(Service.status == service_status)
        else:
            service_status = "all"

        providers = ServiceProvider.query.order_by(ServiceProvider.name.asc()).all()
        if service_provider == "manual":
            services_query = services_query.filter(Service.provider_id.is_(None))
        elif service_provider != "all":
            try:
                provider_id = int(service_provider)
                services_query = services_query.filter(Service.provider_id == provider_id)
            except ValueError:
                service_provider = "all"

        category_rows = (
            db.session.query(Service.category)
            .filter(Service.category.isnot(None))
            .distinct()
            .order_by(Service.category.asc())
            .all()
        )
        categories = [row[0] for row in category_rows if row[0]]
        if service_category != "all":
            if service_category in categories:
                services_query = services_query.filter(Service.category == service_category)
            else:
                service_category = "all"

        service_list = services_query.order_by(Service.id.desc()).all()
        completion_avg_map = _average_completion_time_map([service.id for service in service_list])
        for service in service_list:
            service.display_average_time = (
                (service.average_time or "").strip()
                or completion_avg_map.get(service.id, "")
            )
        return render_template(
            "admin/services.html",
            title="Manage Services",
            services=service_list,
            service_q=service_q,
            service_status=service_status,
            service_provider=service_provider,
            service_category=service_category,
            providers=providers,
            categories=categories,
        )

    @app.route("/admin/providers", methods=["GET", "POST"])
    @app.route("/admin-panel/providers", methods=["GET", "POST"])
    @login_required
    @admin_required
    def admin_providers():
        if request.method == "POST":
            action = request.form.get("action", "").strip()
            if action == "add_provider":
                provider_name = request.form.get("provider_name", "").strip()
                provider_api_url = request.form.get("provider_api_url", "").strip()
                provider_api_key = request.form.get("provider_api_key", "").strip()

                if not provider_name or not provider_api_url or not provider_api_key:
                    flash("Provider name, API URL, and API key are required.", "danger")
                    return redirect(url_for("admin_providers"))
                if not provider_api_url.lower().startswith(("http://", "https://")):
                    flash("Provider API URL must start with http:// or https://", "danger")
                    return redirect(url_for("admin_providers"))
                if ServiceProvider.query.filter(ServiceProvider.name.ilike(provider_name)).first():
                    flash("Provider name already exists.", "danger")
                    return redirect(url_for("admin_providers"))

                provider = ServiceProvider(
                    name=provider_name,
                    api_url=provider_api_url,
                    api_key=provider_api_key,
                    is_active=True,
                )
                db.session.add(provider)
                db.session.commit()
                flash(f"Provider {provider.name} added.", "success")
            elif action == "toggle_provider":
                provider_id = request.form.get("provider_id", type=int)
                provider = ServiceProvider.query.get(provider_id)
                if not provider:
                    flash("Provider not found.", "danger")
                    return redirect(url_for("admin_providers"))
                provider.is_active = not provider.is_active
                db.session.commit()
                flash(
                    f"Provider {provider.name} is now {'active' if provider.is_active else 'paused'}.",
                    "success",
                )
            elif action == "delete_provider":
                provider_id = request.form.get("provider_id", type=int)
                provider = ServiceProvider.query.get(provider_id)
                if not provider:
                    flash("Provider not found.", "danger")
                    return redirect(url_for("admin_providers"))
                linked_services = Service.query.filter_by(provider_id=provider.id).count()
                if linked_services > 0:
                    flash(
                        f"Cannot delete provider {provider.name}; {linked_services} services are still linked.",
                        "warning",
                    )
                    return redirect(url_for("admin_providers"))
                db.session.delete(provider)
                db.session.commit()
                flash(f"Provider {provider.name} deleted.", "success")
            elif action == "sync_provider":
                provider_id = request.form.get("provider_id", type=int)
                provider = ServiceProvider.query.get(provider_id)
                if not provider:
                    flash("Provider not found.", "danger")
                    return redirect(url_for("admin_providers"))
                try:
                    updated, skipped_new, total = sync_provider_services(provider)
                    flash(
                        f"{provider.name} synced. Checked {total} services "
                        f"({updated} updated, {skipped_new} not yet imported).",
                        "success",
                    )
                except ValueError as exc:
                    flash(f"Sync failed for {provider.name}: {exc}", "danger")
            elif action == "sync_all_providers":
                providers = ServiceProvider.query.filter_by(is_active=True).order_by(ServiceProvider.id.asc()).all()
                if not providers:
                    flash("No active providers found.", "warning")
                    return redirect(url_for("admin_providers"))

                total_updated = 0
                total_skipped_new = 0
                total_services = 0
                failed = []
                for provider in providers:
                    try:
                        updated, skipped_new, total = sync_provider_services(provider)
                        total_updated += updated
                        total_skipped_new += skipped_new
                        total_services += total
                    except ValueError as exc:
                        failed.append(f"{provider.name}: {exc}")

                if total_services > 0:
                    flash(
                        f"Synced providers. Checked {total_services} services "
                        f"({total_updated} updated, {total_skipped_new} not yet imported).",
                        "success",
                    )
                if failed:
                    flash("Some providers failed: " + "; ".join(failed), "warning")

            return redirect(url_for("admin_providers"))

        provider_q = request.args.get("q", "").strip()
        providers_query = ServiceProvider.query
        if provider_q:
            if provider_q.isdigit():
                providers_query = providers_query.filter(ServiceProvider.id == int(provider_q))
            else:
                term = f"%{provider_q}%"
                providers_query = providers_query.filter(
                    or_(
                        ServiceProvider.name.ilike(term),
                        ServiceProvider.api_url.ilike(term),
                    )
                )

        providers = providers_query.order_by(ServiceProvider.name.asc()).all()
        return render_template(
            "admin/providers.html",
            title="Manage Providers",
            providers=providers,
            provider_q=provider_q,
        )

    @app.route("/admin/services/<int:service_id>/edit", methods=["GET", "POST"])
    @app.route("/admin-panel/services/<int:service_id>/edit", methods=["GET", "POST"])
    @login_required
    @admin_required
    def admin_edit_service(service_id):
        service = Service.query.get(service_id)
        if not service:
            flash("Service not found.", "danger")
            return redirect(url_for("admin_services"))

        if request.method == "POST":
            allowed_statuses = {"active", "paused"}
            provider_id = request.form.get("provider_id", type=int)
            provider_service_id = request.form.get("provider_service_id", "").strip() or None
            provider = None
            if provider_id:
                provider = ServiceProvider.query.get(provider_id)
                if not provider:
                    flash("Selected provider is invalid.", "danger")
                    return redirect(url_for("admin_edit_service", service_id=service.id))

            service.name = request.form.get("name", "").strip()
            service.description = request.form.get("description", "").strip()
            service.average_time = request.form.get("average_time", "").strip()
            service.category = request.form.get("category", "General").strip() or "General"
            service.price_per_1000 = request.form.get("price_per_1000", type=float) or 0.0
            service.min_qty = request.form.get("min_qty", type=int) or 1
            service.max_qty = request.form.get("max_qty", type=int) or 1
            service.status = request.form.get("status", "active").strip().lower()
            service.provider_id = provider.id if provider else None
            service.provider_service_id = provider_service_id
            service.provider_refill = bool(request.form.get("provider_refill"))
            service.provider_cancel = bool(request.form.get("provider_cancel"))

            if not service.name:
                flash("Service name is required.", "danger")
                return redirect(url_for("admin_edit_service", service_id=service.id))
            if service.max_qty < service.min_qty:
                flash("Max quantity cannot be less than min quantity.", "danger")
                return redirect(url_for("admin_edit_service", service_id=service.id))
            if service.status not in allowed_statuses:
                flash("Invalid status.", "danger")
                return redirect(url_for("admin_edit_service", service_id=service.id))

            db.session.commit()
            flash(f"Service #{service.id} updated.", "success")
            return redirect(url_for("admin_services"))

        providers = ServiceProvider.query.order_by(ServiceProvider.name.asc()).all()
        return render_template(
            "admin/service_edit.html",
            title="Edit Service",
            service=service,
            providers=providers,
        )

    @app.route("/admin/services/new", methods=["GET", "POST"])
    @app.route("/admin-panel/services/new", methods=["GET", "POST"])
    @login_required
    @admin_required
    def admin_add_service():
        providers = ServiceProvider.query.order_by(ServiceProvider.name.asc()).all()
        if request.method == "POST":
            provider_id = request.form.get("provider_id", type=int)
            provider_service_id = request.form.get("provider_service_id", "").strip() or None
            provider = None
            if provider_id:
                provider = ServiceProvider.query.get(provider_id)
                if not provider:
                    flash("Selected provider is invalid.", "danger")
                    return redirect(url_for("admin_add_service"))

            service = Service(
                name=request.form.get("name", "").strip(),
                description=request.form.get("description", "").strip(),
                average_time=request.form.get("average_time", "").strip(),
                category=request.form.get("category", "General").strip() or "General",
                price_per_1000=request.form.get("price_per_1000", type=float) or 0.0,
                min_qty=request.form.get("min_qty", type=int) or 1,
                max_qty=request.form.get("max_qty", type=int) or 1,
                status=request.form.get("status", "active").strip().lower(),
                provider_id=provider.id if provider else None,
                provider_service_id=provider_service_id,
                provider_refill=bool(request.form.get("provider_refill")),
                provider_cancel=bool(request.form.get("provider_cancel")),
            )

            if not service.name:
                flash("Service name is required.", "danger")
                return redirect(url_for("admin_add_service"))
            if service.max_qty < service.min_qty:
                flash("Max quantity cannot be less than min quantity.", "danger")
                return redirect(url_for("admin_add_service"))
            if service.status not in {"active", "paused"}:
                flash("Invalid status.", "danger")
                return redirect(url_for("admin_add_service"))

            db.session.add(service)
            db.session.commit()
            flash("Service added.", "success")
            return redirect(url_for("admin_services"))

        return render_template(
            "admin/service_add.html",
            title="Add Service",
            providers=providers,
        )

    @app.route("/admin/services/import", methods=["GET", "POST"])
    @app.route("/admin-panel/services/import", methods=["GET", "POST"])
    @login_required
    @admin_required
    def admin_import_services():
        providers = ServiceProvider.query.order_by(ServiceProvider.name.asc()).all()
        provider_id = request.values.get("provider_id", type=int)
        provider = ServiceProvider.query.get(provider_id) if provider_id else None
        preview_services = []
        markup_percent = request.args.get("markup_percent", type=float)
        if markup_percent is None:
            markup_percent = 0.0

        if request.method == "POST":
            if not provider:
                flash("Provider is required for import.", "danger")
                return redirect(url_for("admin_add_service"))

            raw_markup_percent = request.form.get("markup_percent", "").strip()
            if not raw_markup_percent:
                markup_percent = 0.0
            else:
                try:
                    markup_percent = float(raw_markup_percent)
                except (TypeError, ValueError):
                    flash("Markup percent must be a valid number.", "danger")
                    return redirect(url_for("admin_import_services", provider_id=provider.id))

            if markup_percent < 0:
                flash("Markup percent cannot be negative.", "danger")
                return redirect(url_for("admin_import_services", provider_id=provider.id))
            if markup_percent > 1000:
                flash("Markup percent is too high. Use a value up to 1000%.", "danger")
                return redirect(url_for("admin_import_services", provider_id=provider.id))

            markup_multiplier = 1 + (markup_percent / 100.0)

            selected_ids = {
                value.strip()
                for value in request.form.getlist("provider_service_ids")
                if value and value.strip()
            }
            if not selected_ids:
                flash("Select at least one provider service to import.", "warning")
                return redirect(url_for("admin_import_services", provider_id=provider.id))

            try:
                provider_services = fetch_provider_services(provider)
            except ValueError as exc:
                flash(f"Import failed: {exc}", "danger")
                return redirect(url_for("admin_import_services", provider_id=provider.id))

            services_by_id = {item["provider_service_id"]: item for item in provider_services}
            imported = 0
            skipped = 0

            for provider_service_id in selected_ids:
                item = services_by_id.get(provider_service_id)
                if not item:
                    skipped += 1
                    continue

                exists = Service.query.filter_by(
                    provider_id=provider.id,
                    provider_service_id=provider_service_id,
                ).first()
                if exists:
                    skipped += 1
                    continue

                db.session.add(
                    Service(
                        name=item["name"],
                        description=item["description"],
                        average_time=item["average_time"],
                        category=item["category"],
                        price_per_1000=round((item["price_per_1000"] or 0.0) * markup_multiplier, 4),
                        min_qty=item["min_qty"],
                        max_qty=item["max_qty"],
                        status=item["status"],
                        provider_id=provider.id,
                        provider_service_id=provider_service_id,
                        provider_refill=item["provider_refill"],
                        provider_cancel=item["provider_cancel"],
                    )
                )
                imported += 1

            provider.last_synced_at = datetime.utcnow()
            db.session.commit()
            flash(
                f"Imported {imported} services from {provider.name} with +{markup_percent:.2f}% markup. "
                f"Skipped: {skipped}.",
                "success",
            )
            return redirect(
                url_for(
                    "admin_import_services",
                    provider_id=provider.id,
                    markup_percent=f"{markup_percent:.2f}",
                )
            )

        if provider:
            try:
                preview_services = fetch_provider_services(provider)
            except ValueError as exc:
                flash(f"Could not load services from {provider.name}: {exc}", "danger")
                preview_services = []

        return render_template(
            "admin/service_import.html",
            title="Import Provider Services",
            providers=providers,
            provider=provider,
            preview_services=preview_services,
            markup_percent=markup_percent,
        )

    @app.route("/admin/orders", methods=["GET", "POST"])
    @app.route("/admin-panel/orders", methods=["GET", "POST"])
    @login_required
    @admin_required
    def admin_orders():
        if request.method == "POST":
            admin_action = request.form.get("admin_action", "update_status")
            if admin_action == "sync_all_provider_orders":
                provider_orders = (
                    Order.query.filter(Order.provider_id.isnot(None), Order.provider_order_id.isnot(None))
                    .order_by(Order.id.desc())
                    .limit(300)
                    .all()
                )
                synced = 0
                failed = 0
                for provider_order in provider_orders:
                    try:
                        if refresh_provider_order_status(provider_order):
                            synced += 1
                    except ValueError:
                        failed += 1
                db.session.commit()
                flash(f"Synced {synced} provider orders. Failed: {failed}.", "info")
                return redirect(url_for("admin_orders"))

            order_id = request.form.get("order_id", type=int)
            order = Order.query.get(order_id)
            if not order:
                flash("Order not found.", "danger")
                return redirect(url_for("admin_orders"))

            if admin_action == "sync_status":
                try:
                    if refresh_provider_order_status(order):
                        db.session.commit()
                        flash(f"Order #{order.id} synced from provider.", "success")
                    else:
                        flash("Order is not linked to an active provider.", "warning")
                except ValueError as exc:
                    db.session.rollback()
                    flash(f"Sync failed: {exc}", "danger")
            elif admin_action == "cancel_provider":
                if not order.provider_id or not order.provider_order_id:
                    flash("Order is not linked to a provider.", "warning")
                    return redirect(url_for("admin_orders"))
                if not order.service.provider_cancel:
                    flash("Cancel is not supported for this service.", "warning")
                    return redirect(url_for("admin_orders"))
                provider = ServiceProvider.query.get(order.provider_id)
                if not provider or not provider.is_active:
                    flash("Provider is unavailable.", "danger")
                    return redirect(url_for("admin_orders"))
                try:
                    provider_api_request(provider, "cancel", order=order.provider_order_id)
                    order.cancel_requested = True
                    previous_status = order.status
                    order.status = "canceled"
                    refunded = apply_cancellation_refund(order, previous_status=previous_status)
                    db.session.commit()
                    if refunded:
                        flash(
                            f"Order #{order.id} canceled and refunded ${order.charge:.4f}.",
                            "success",
                        )
                    else:
                        flash(f"Cancel requested for order #{order.id}.", "success")
                except ValueError as exc:
                    db.session.rollback()
                    flash(f"Cancel failed: {exc}", "danger")
            elif admin_action == "refill_provider":
                if not order.provider_id or not order.provider_order_id:
                    flash("Order is not linked to a provider.", "warning")
                    return redirect(url_for("admin_orders"))
                if not order.service.provider_refill:
                    flash("Refill is not supported for this service.", "warning")
                    return redirect(url_for("admin_orders"))
                provider = ServiceProvider.query.get(order.provider_id)
                if not provider or not provider.is_active:
                    flash("Provider is unavailable.", "danger")
                    return redirect(url_for("admin_orders"))
                try:
                    response = provider_api_request(provider, "refill", order=order.provider_order_id)
                    refill_id = None
                    if isinstance(response, dict):
                        refill_id = response.get("refill")
                    order.refill_status = f"requested#{refill_id}" if refill_id else "requested"
                    db.session.commit()
                    flash(f"Refill requested for order #{order.id}.", "success")
                except ValueError as exc:
                    db.session.rollback()
                    flash(f"Refill failed: {exc}", "danger")
            else:
                new_status = request.form.get("status", "pending")
                previous_status = order.status
                order.status = new_status
                refunded = apply_cancellation_refund(order, previous_status=previous_status)
                db.session.commit()
                if refunded:
                    flash(f"Order #{order.id} updated and refunded ${order.charge:.4f}.", "success")
                else:
                    flash(f"Order #{order.id} updated.", "success")
            return redirect(url_for("admin_orders"))

        order_q = request.args.get("q", "").strip()
        order_status = request.args.get("status", "all").strip().lower()
        orders_query = Order.query.join(User).join(Service)

        if order_q:
            term = f"%{order_q}%"
            filters = [
                User.username.ilike(term),
                Service.name.ilike(term),
                Service.category.ilike(term),
                Order.link.ilike(term),
                Order.provider_order_id.ilike(term),
            ]
            if order_q.isdigit():
                filters.append(Order.id == int(order_q))
            orders_query = orders_query.filter(or_(*filters))

        if order_status in {"pending", "processing", "completed", "canceled", "cancelled"}:
            orders_query = orders_query.filter(Order.status == order_status)
        else:
            order_status = "all"

        order_list = orders_query.order_by(Order.created_at.desc()).all()
        return render_template(
            "admin/orders.html",
            title="Manage Orders",
            orders=order_list,
            order_q=order_q,
            order_status=order_status,
        )

    @app.route("/admin/tickets", methods=["GET", "POST"])
    @app.route("/admin-panel/tickets", methods=["GET", "POST"])
    @login_required
    @admin_required
    def admin_tickets():
        if request.method == "POST":
            ticket_id = request.form.get("ticket_id", type=int)
            reply = request.form.get("admin_reply", "").strip()
            status = request.form.get("status", "open")
            ticket = Ticket.query.get(ticket_id)
            if ticket:
                ticket.admin_reply = reply
                ticket.status = status
                db.session.commit()
                flash(f"Ticket #{ticket.id} updated.", "success")
            return redirect(url_for("admin_tickets"))

        ticket_q = request.args.get("q", "").strip()
        ticket_status = request.args.get("status", "all").strip().lower()
        tickets_query = Ticket.query.join(User)

        if ticket_q:
            term = f"%{ticket_q}%"
            filters = [
                Ticket.subject.ilike(term),
                Ticket.message.ilike(term),
                Ticket.admin_reply.ilike(term),
                User.username.ilike(term),
            ]
            if ticket_q.isdigit():
                filters.append(Ticket.id == int(ticket_q))
            tickets_query = tickets_query.filter(or_(*filters))

        if ticket_status in {"open", "answered", "closed"}:
            tickets_query = tickets_query.filter(Ticket.status == ticket_status)
        else:
            ticket_status = "all"

        ticket_list = tickets_query.order_by(Ticket.created_at.desc()).all()
        return render_template(
            "admin/tickets.html",
            title="Manage Tickets",
            tickets=ticket_list,
            ticket_q=ticket_q,
            ticket_status=ticket_status,
        )

    @app.route("/admin/users", methods=["GET", "POST"])
    @app.route("/admin-panel/users", methods=["GET", "POST"])
    @login_required
    @admin_required
    def admin_users():
        if request.method == "POST":
            admin_action = request.form.get("admin_action", "adjust_balance")
            if admin_action == "create_user":
                username = request.form.get("username", "").strip()
                email = request.form.get("email", "").strip().lower()
                password = request.form.get("password", "")
                initial_balance = request.form.get("initial_balance", type=float) or 0.0

                if not username or not email or not password:
                    flash("Username, email, and password are required.", "danger")
                    return redirect(url_for("admin_users"))
                if len(password) < 6:
                    flash("Password must be at least 6 characters.", "danger")
                    return redirect(url_for("admin_users"))
                if User.query.filter((User.username == username) | (User.email == email)).first():
                    flash("Username or email already exists.", "danger")
                    return redirect(url_for("admin_users"))
                if initial_balance < 0:
                    flash("Initial balance cannot be negative.", "danger")
                    return redirect(url_for("admin_users"))

                user = User(
                    username=username,
                    email=email,
                    password_hash=generate_password_hash(password),
                    balance=round(initial_balance, 4),
                    is_admin=False,
                    is_banned=False,
                )
                db.session.add(user)
                db.session.commit()
                flash(f"User {user.username} created successfully.", "success")
            elif admin_action in {"ban_user", "unban_user", "delete_user"}:
                user_id = request.form.get("user_id", type=int)
                user = User.query.get(user_id)
                if not user or user.is_admin:
                    flash("User not found.", "danger")
                    return redirect(url_for("admin_users"))

                if admin_action == "ban_user":
                    user.is_banned = True
                    db.session.commit()
                    flash(f"User {user.username} has been banned.", "success")
                elif admin_action == "unban_user":
                    user.is_banned = False
                    db.session.commit()
                    flash(f"User {user.username} has been unbanned.", "success")
                else:
                    Order.query.filter_by(user_id=user.id).delete()
                    Ticket.query.filter_by(user_id=user.id).delete()
                    db.session.delete(user)
                    db.session.commit()
                    flash("User deleted successfully.", "success")
            else:
                user_id = request.form.get("user_id", type=int)
                amount = request.form.get("amount", type=float) or 0.0
                action = request.form.get("action", "add")
                user = User.query.get(user_id)
                if user and not user.is_admin and amount > 0:
                    if action == "deduct":
                        if amount > user.balance:
                            flash(
                                f"Cannot deduct {amount:.2f} from {user.username}; insufficient balance.",
                                "danger",
                            )
                            return redirect(url_for("admin_users"))
                        user.balance = round(user.balance - amount, 4)
                        db.session.commit()
                        flash(f"Deducted {amount:.2f} from {user.username}.", "success")
                    else:
                        user.balance = round(user.balance + amount, 4)
                        db.session.commit()
                        flash(f"Added {amount:.2f} balance to {user.username}.", "success")
            return redirect(url_for("admin_users"))

        user_q = request.args.get("q", "").strip()
        users_query = User.query.filter_by(is_admin=False)

        if user_q:
            if user_q.isdigit():
                users_query = users_query.filter(User.id == int(user_q))
            else:
                term = f"%{user_q}%"
                users_query = users_query.filter(
                    or_(
                        User.username.ilike(term),
                        User.email.ilike(term),
                    )
                )

        users = users_query.order_by(User.created_at.desc()).all()
        return render_template(
            "admin/users.html",
            title="Manage Users",
            users=users,
            user_q=user_q,
        )

    @app.route("/api/admin/users", methods=["GET"])
    @login_required
    @admin_required
    def api_admin_users():
        user_q = request.args.get("q", "").strip()
        users_query = User.query.filter_by(is_admin=False)

        if user_q:
            if user_q.isdigit():
                users_query = users_query.filter(User.id == int(user_q))
            else:
                term = f"%{user_q}%"
                users_query = users_query.filter(
                    or_(
                        User.username.ilike(term),
                        User.email.ilike(term),
                    )
                )

        users = users_query.order_by(User.created_at.desc()).all()
        return jsonify({"users": [user_to_dict(user) for user in users]})

    @app.route("/api/admin/users", methods=["POST"])
    @login_required
    @admin_required
    def api_admin_create_user():
        payload = request.get_json(silent=True) or {}
        username = str(payload.get("username", "")).strip()
        email = str(payload.get("email", "")).strip().lower()
        password = str(payload.get("password", ""))
        initial_balance = payload.get("initial_balance", 0.0)

        try:
            initial_balance = float(initial_balance)
        except (TypeError, ValueError):
            return jsonify({"error": "initial_balance must be a number"}), 400

        if not username or not email or not password:
            return jsonify({"error": "username, email, and password are required"}), 400
        if len(password) < 6:
            return jsonify({"error": "password must be at least 6 characters"}), 400
        if initial_balance < 0:
            return jsonify({"error": "initial_balance cannot be negative"}), 400
        if User.query.filter((User.username == username) | (User.email == email)).first():
            return jsonify({"error": "username or email already exists"}), 409

        user = User(
            username=username,
            email=email,
            password_hash=generate_password_hash(password),
            balance=round(initial_balance, 4),
            is_admin=False,
            is_banned=False,
        )
        db.session.add(user)
        db.session.commit()
        return jsonify({"message": "user created", "user": user_to_dict(user)}), 201

    @app.route("/api/admin/users/<int:user_id>", methods=["PATCH"])
    @login_required
    @admin_required
    def api_admin_update_user(user_id):
        user = User.query.get(user_id)
        if not user or user.is_admin:
            return jsonify({"error": "user not found"}), 404

        payload = request.get_json(silent=True) or {}

        if "username" in payload:
            username = str(payload.get("username", "")).strip()
            if not username:
                return jsonify({"error": "username cannot be empty"}), 400
            exists = User.query.filter(User.username == username, User.id != user.id).first()
            if exists:
                return jsonify({"error": "username already exists"}), 409
            user.username = username

        if "email" in payload:
            email = str(payload.get("email", "")).strip().lower()
            if not email:
                return jsonify({"error": "email cannot be empty"}), 400
            exists = User.query.filter(User.email == email, User.id != user.id).first()
            if exists:
                return jsonify({"error": "email already exists"}), 409
            user.email = email

        if "password" in payload:
            password = str(payload.get("password", ""))
            if len(password) < 6:
                return jsonify({"error": "password must be at least 6 characters"}), 400
            user.password_hash = generate_password_hash(password)

        if "is_banned" in payload:
            user.is_banned = bool(payload.get("is_banned"))

        if "balance" in payload:
            try:
                new_balance = float(payload.get("balance"))
            except (TypeError, ValueError):
                return jsonify({"error": "balance must be a number"}), 400
            if new_balance < 0:
                return jsonify({"error": "balance cannot be negative"}), 400
            user.balance = round(new_balance, 4)

        if "balance_delta" in payload:
            try:
                delta = float(payload.get("balance_delta"))
            except (TypeError, ValueError):
                return jsonify({"error": "balance_delta must be a number"}), 400
            candidate = round((user.balance or 0.0) + delta, 4)
            if candidate < 0:
                return jsonify({"error": "resulting balance cannot be negative"}), 400
            user.balance = candidate

        db.session.commit()
        return jsonify({"message": "user updated", "user": user_to_dict(user)})

    @app.route("/api/admin/users/<int:user_id>", methods=["DELETE"])
    @login_required
    @admin_required
    def api_admin_delete_user(user_id):
        user = User.query.get(user_id)
        if not user or user.is_admin:
            return jsonify({"error": "user not found"}), 404

        Order.query.filter_by(user_id=user.id).delete()
        Ticket.query.filter_by(user_id=user.id).delete()
        db.session.delete(user)
        db.session.commit()
        return jsonify({"message": "user deleted"})

    @app.route("/seed")
    def seed_route():
        db.create_all()
        seed_data()
        return "Seed complete."
