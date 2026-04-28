# admin/app.py
import sys
import os

# --- تثبيت مجلد العمل على جذر المشروع ---
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(PROJECT_ROOT)
sys.path.insert(0, PROJECT_ROOT)

from flask import Flask, render_template, request, redirect, url_for, flash, session
from functools import wraps
from sqlalchemy import func
import random
import requests
from contextlib import contextmanager
from datetime import datetime, timedelta

from core.database import engine, Base, SessionLocal
import core.models
Base.metadata.create_all(bind=engine)

from core.bot_state import is_bot_active, set_bot_active
from core.models import Product, ProductsPrice, Order, DepositOrder, User, ExchangeRate
from config import settings

app = Flask(__name__, template_folder="templates")
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'your_secret_key')

ADMIN_IDS = [str(i) for i in settings.ADMIN_IDS]
BOT_TOKEN = settings.BOT_TOKEN
CODE_EXPIRY_MINUTES = 5

@contextmanager
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def paginate(query, page, per_page=20):
    total = query.count()
    items = query.offset((page - 1) * per_page).limit(per_page).all()
    total_pages = (total + per_page - 1) // per_page
    return {
        'items': items,
        'page': page,
        'per_page': per_page,
        'total': total,
        'total_pages': total_pages,
        'has_prev': page > 1,
        'has_next': page < total_pages
    }

def notify_user_deposit_approved(user_telegram_id, amount):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    text = f"✅ تمت الموافقة على طلب الشحن الخاص بك بقيمة {amount} ل.س\nتمت إضافة المبلغ إلى رصيدك."
    try:
        requests.post(url, data={"chat_id": user_telegram_id, "text": text}, timeout=5)
    except Exception as e:
        print(f"Failed to send deposit notification: {e}")

def send_telegram_code(telegram_id, code):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    text = f"رمز الدخول للوحة الإدارة: {code}\n(صالح لمدة {CODE_EXPIRY_MINUTES} دقائق)"
    try:
        requests.post(url, data={"chat_id": telegram_id, "text": text}, timeout=5)
    except Exception as e:
        print(f"Failed to send code: {e}")

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'admin_id' not in session or session['admin_id'] not in ADMIN_IDS:
            return redirect(url_for('login', next=request.url))
        return f(*args, **kwargs)
    return decorated_function

def dollar_to_syp(dollar, rate):
    return round(float(dollar) * float(rate))

# ------------------- Routes -------------------
@app.route('/admin')
@login_required
def admin_dashboard():
    with get_db() as db:
        total_users = db.query(User).count()
        pending_orders = db.query(Order).filter(Order.status == 'pending').count()
        pending_deposits = db.query(DepositOrder).filter(DepositOrder.status == 'pending').count()
        total_revenue = db.query(func.sum(Order.total_price_syp)).filter(Order.status == 'completed').scalar() or 0

        recent_orders = db.query(Order).order_by(Order.created_at.desc()).limit(5).all()
        recent_deposits = db.query(DepositOrder).order_by(DepositOrder.created_at.desc()).limit(5).all()

        recent_order_list = []
        for order in recent_orders:
            user = db.query(User).filter_by(id=order.user_id).first()
            product = db.query(Product).filter_by(id=order.product_id).first()
            recent_order_list.append({
                'id': order.id,
                'user_name': f"{user.first_name or ''} {user.last_name or ''}".strip() if user else 'غير معروف',
                'product_name': product.name_ar if product else '-',
                'total': order.total_price_syp,
                'status': order.status,
                'created_at': order.created_at
            })

        recent_deposit_list = []
        for dep in recent_deposits:
            user = db.query(User).filter_by(id=dep.user_id).first()
            recent_deposit_list.append({
                'id': dep.id,
                'user_name': f"{user.first_name or ''} {user.last_name or ''}".strip() if user else 'غير معروف',
                'amount': dep.amount,
                'status': dep.status,
                'created_at': dep.created_at
            })

        return render_template('dashboard.html', active_page='dashboard',
                               total_users=total_users, pending_orders=pending_orders,
                               pending_deposits=pending_deposits, total_revenue=total_revenue,
                               recent_orders=recent_order_list, recent_deposits=recent_deposit_list)

@app.route('/admin/bot-control', methods=['GET', 'POST'])
@login_required
def bot_control():
    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'start':
            set_bot_active(True)
            flash('✅ تم تشغيل البوت بنجاح', 'success')
        elif action == 'stop':
            set_bot_active(False)
            flash('⏸️ تم إيقاف البوت مؤقتاً', 'success')
        return redirect(url_for('bot_control'))
    current_status = is_bot_active()
    return render_template('bot_control.html', bot_active=current_status, active_page='bot_control')

@app.route('/admin/login', methods=['GET', 'POST'])
def login():
    error = None
    step = session.get('login_step', 1)
    code_time = session.get('code_timestamp')
    if code_time and datetime.fromisoformat(code_time) + timedelta(minutes=CODE_EXPIRY_MINUTES) < datetime.utcnow():
        session.pop('login_code', None)
        session.pop('code_timestamp', None)
        session['login_step'] = 1
        step = 1
        error = 'انتهت صلاحية الرمز، يرجى طلب رمز جديد.'

    if request.method == 'POST':
        if step == 1:
            admin_id = request.form.get('admin_id')
            if admin_id in ADMIN_IDS:
                code = str(random.randint(100000, 999999))
                session['pending_admin_id'] = admin_id
                session['login_code'] = code
                session['code_timestamp'] = datetime.utcnow().isoformat()
                session['login_step'] = 2
                send_telegram_code(admin_id, code)
                return render_template('login.html', step=2, error=None)
            else:
                error = 'رقم حساب تيليجرام غير مصرح.'
        elif step == 2:
            code_entered = request.form.get('code')
            if code_entered == session.get('login_code'):
                session['admin_id'] = session['pending_admin_id']
                for key in ['login_code', 'pending_admin_id', 'login_step', 'code_timestamp']:
                    session.pop(key, None)
                return redirect(url_for('manage_prices'))
            else:
                error = 'رمز التحقق غير صحيح.'
                return render_template('login.html', step=2, error=error)
    else:
        session['login_step'] = 1
        for key in ['pending_admin_id', 'login_code', 'code_timestamp']:
            session.pop(key, None)
    return render_template('login.html', step=step, error=error)

@app.route('/admin/logout')
def logout():
    session.pop('admin_id', None)
    return redirect(url_for('login'))

# ------------------- Price Management (مُصحَّحة) -------------------
@app.route('/admin/prices', methods=['GET', 'POST'])
@login_required
def manage_prices():
    with get_db() as db:
        products = db.query(Product).all()
        prices = db.query(ProductsPrice).all()
        edit_id = request.args.get('edit')
        edit_price = None
        if edit_id:
            edit_price = db.query(ProductsPrice).filter_by(id=edit_id).first()

        if request.method == 'POST':
            price_id = request.form.get('price_id')
            product_id = int(request.form['product_id'])
            option_value = request.form['option_value'].strip()
            price_usd_str = request.form['price_usd']
            exchange_rate_str = request.form['exchange_rate']

            errors = []
            try:
                price_usd = float(price_usd_str)
                exchange_rate = float(exchange_rate_str)
                if price_usd <= 0:
                    errors.append("السعر بالدولار يجب أن يكون أكبر من صفر")
                if exchange_rate <= 0:
                    errors.append("سعر الصرف يجب أن يكون أكبر من صفر")
            except ValueError:
                errors.append("قيم رقمية غير صالحة")

            if errors:
                for err in errors:
                    flash(err, 'error')
                return redirect(url_for('manage_prices', edit=price_id if price_id else None))

            price_syp = dollar_to_syp(price_usd, exchange_rate)

            if price_id:  # تعديل سعر موجود
                existing_price = db.query(ProductsPrice).filter_by(id=price_id).first()
                if existing_price:
                    existing_price.product_id = product_id
                    existing_price.option_value = option_value
                    # ✅ لا نُعيِّن price_usd ولا exchange_rate لأنهما غير موجودين
                    existing_price.price_syp = price_syp
                    db.commit()
                    flash('تم تحديث السعر بنجاح', 'success')
                else:
                    flash('السعر المطلوب غير موجود', 'error')
            else:  # إضافة سعر جديد
                new_price = ProductsPrice(
                    product_id=product_id,
                    option_value=option_value,
                    price_syp=price_syp
                    # لا نُمرِّر price_usd أو exchange_rate - غير موجودَين
                )
                db.add(new_price)
                db.commit()
                flash('تمت إضافة السعر بنجاح', 'success')
            return redirect(url_for('manage_prices'))

    # GET request
    with get_db() as db:
        products = db.query(Product).all()
        prices = db.query(ProductsPrice).all()
        edit_id = request.args.get('edit')
        edit_price = None
        if edit_id:
            edit_price = db.query(ProductsPrice).filter_by(id=edit_id).first()
        latest_rate = db.query(ExchangeRate).order_by(ExchangeRate.effective_date.desc()).first()
        current_exchange_rate = latest_rate.rate if latest_rate else 10000
        return render_template('prices.html',
                               products=products,
                               prices=prices,
                               edit_price=edit_price,
                               current_exchange_rate=current_exchange_rate,
                               active_page='prices')

@app.route('/admin/prices/update-markup', methods=['POST'])
@login_required
def update_product_markup():
    product_id = int(request.form['product_id'])
    new_markup = float(request.form['markup_percentage'])
    with get_db() as db:
        product = db.query(Product).filter_by(id=product_id).first()
        if product:
            product.markup_percentage = new_markup
            db.commit()
            flash('تم تحديث نسبة الربح بنجاح', 'success')
        else:
            flash('المنتج غير موجود', 'error')
    return redirect(url_for('manage_prices'))

@app.route('/admin/prices/delete/<int:price_id>', methods=['POST'])
@login_required
def delete_price(price_id):
    with get_db() as db:
        price = db.query(ProductsPrice).filter_by(id=price_id).first()
        if price:
            db.delete(price)
            db.commit()
            flash('تم حذف السعر بنجاح', 'success')
        else:
            flash('السعر غير موجود', 'error')
    return redirect(url_for('manage_prices'))

# ------------------- Orders Management -------------------
@app.route('/admin/orders')
@login_required
def manage_orders():
    page = request.args.get('page', 1, type=int)
    per_page = 15
    with get_db() as db:
        query = db.query(Order).order_by(Order.id.desc())
        pagination = paginate(query, page, per_page)
        order_list = []
        for order in pagination['items']:
            user = db.query(User).filter_by(id=order.user_id).first()
            product = db.query(Product).filter_by(id=order.product_id).first()
            product_name = product.name_ar if product else f"منتج {order.product_id}"
            order_list.append({
                'id': order.id,
                'user_name': f"{user.first_name or ''} {user.last_name or ''}".strip() if user else 'غير معروف',
                'username': user.username if user else '',
                'product_name': product_name,
                'total_price_syp': order.total_price_syp,
                'status': order.status,
                'created_at': order.created_at,
                'field_answers': order.field_answers
            })
        return render_template('orders.html', orders=order_list, pagination=pagination, active_page='orders')

@app.route('/admin/orders/update/<int:order_id>', methods=['POST'])
@login_required
def update_order_status(order_id):
    new_status = request.form.get('status')
    with get_db() as db:
        order = db.query(Order).filter_by(id=order_id).first()
        if order:
            order.status = new_status
            db.commit()
            flash(f'تم تحديث حالة الطلب #{order_id} إلى {new_status}', 'success')
        else:
            flash('الطلب غير موجود', 'error')
    return redirect(url_for('manage_orders'))

# ------------------- Deposits Management -------------------
@app.route('/admin/deposits')
@login_required
def manage_deposits():
    page = request.args.get('page', 1, type=int)
    per_page = 15
    with get_db() as db:
        query = db.query(DepositOrder).order_by(DepositOrder.id.desc())
        pagination = paginate(query, page, per_page)
        deposit_list = []
        for dep in pagination['items']:
            user = db.query(User).filter_by(id=dep.user_id).first()
            deposit_list.append({
                'id': dep.id,
                'user_name': f"{user.first_name or ''} {user.last_name or ''}".strip() if user else 'غير معروف',
                'username': user.username if user else '',
                'amount': dep.amount,
                'status': dep.status,
                'screenshot_path': dep.screenshot_path,
                'created_at': dep.created_at
            })
        all_deposits = db.query(DepositOrder).all()
        pending_count = sum(1 for d in all_deposits if d.status == 'pending')
        approved_count = sum(1 for d in all_deposits if d.status == 'approved')
        total_amount = sum(d.amount for d in all_deposits if d.status == 'approved')
        return render_template('deposits.html', deposits=deposit_list, pagination=pagination,
                               pending_count=pending_count, approved_count=approved_count,
                               total_amount=total_amount, active_page='deposits')

@app.route('/admin/deposits/update/<int:deposit_id>', methods=['POST'])
@login_required
def update_deposit_status(deposit_id):
    new_status = request.form.get('status')
    with get_db() as db:
        deposit = db.query(DepositOrder).filter_by(id=deposit_id).first()
        if not deposit:
            flash('طلب الشحن غير موجود', 'error')
            return redirect(url_for('manage_deposits'))
        old_status = deposit.status
        deposit.status = new_status
        if new_status == 'approved' and old_status != 'approved':
            user = db.query(User).filter_by(id=deposit.user_id).first()
            if user:
                user.balance = (user.balance or 0) + deposit.amount
                notify_user_deposit_approved(user.telegram_id, deposit.amount)
                flash(f'تمت إضافة {deposit.amount} ل.س إلى رصيد المستخدم', 'success')
        if new_status == 'approved' and 'admin_id' in DepositOrder.__table__.columns:
            deposit.admin_id = session.get('admin_id')
        db.commit()
        flash(f'تم تحديث حالة الشحن #{deposit_id} إلى {new_status}', 'success')
    return redirect(url_for('manage_deposits'))

@app.route('/admin/exchange-rate', methods=['GET', 'POST'])
@login_required
def manage_exchange_rate():
    with get_db() as db:
        current_rate = db.query(ExchangeRate).order_by(ExchangeRate.effective_date.desc()).first()
        if request.method == 'POST':
            new_rate = int(request.form['rate'])
            rate_entry = ExchangeRate(rate=new_rate, created_by_admin_id=int(session['admin_id']))
            db.add(rate_entry)
            db.commit()
            flash(f'تم تحديث سعر الصرف إلى {new_rate} ل.س', 'success')
            return redirect(url_for('manage_exchange_rate'))
        return render_template('exchange-rate.html', current_rate=current_rate)

if __name__ == '__main__':
    app.run(debug=False,host='0.0.0.0',port=5000)