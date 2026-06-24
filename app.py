import threading
import time
import random
import requests
import os
import uuid
import json
from datetime import datetime, timedelta
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, flash, abort, get_flashed_messages, make_response
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy import inspect, text

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'super-secret-key')
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///app.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    balance = db.Column(db.Float, default=0.0)
    is_upgraded = db.Column(db.Boolean, default=False)
    is_admin = db.Column(db.Boolean, default=False)
    referral_code = db.Column(db.String(20), unique=True, nullable=False)
    referred_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    ccp_account = db.Column(db.String(50), nullable=True)
    ccp_last_changed = db.Column(db.DateTime, nullable=True)
    auto_withdraw_threshold = db.Column(db.Integer, nullable=True)
    instagram_username = db.Column(db.String(50), nullable=True)
    instagram_last_changed = db.Column(db.DateTime, nullable=True)
    tiktok_username = db.Column(db.String(50), nullable=True)
    tiktok_last_changed = db.Column(db.DateTime, nullable=True)
    membership_type = db.Column(db.String(20), default='free') # free, vip_10_days, vip_lifetime
    membership_expires_at = db.Column(db.DateTime, nullable=True)
    gender = db.Column(db.String(10), default='male') # male, female
    age = db.Column(db.Integer, default=18)

    referrals = db.relationship('User', backref=db.backref('referrer', remote_side=[id]))

    @property
    def is_upgraded(self):
        if self.membership_type == 'vip_lifetime':
            return True
        if self.membership_type == 'vip_10_days':
            if self.membership_expires_at and self.membership_expires_at > datetime.utcnow():
                return True
        return False

class Task(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=False)
    link = db.Column(db.String(500), nullable=True)
    reward_normal = db.Column(db.Float, default=0.1)
    reward_upgraded = db.Column(db.Float, default=1.0)
    max_completions = db.Column(db.Integer, nullable=True)
    target_gender = db.Column(db.String(10), default='all') # all, male, female
    min_age = db.Column(db.Integer, nullable=True)
    max_age = db.Column(db.Integer, nullable=True)
    is_boosted = db.Column(db.Boolean, default=False)

class CompletedTask(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    task_id = db.Column(db.Integer, db.ForeignKey('task.id'), nullable=False)
    completed_at = db.Column(db.DateTime, default=datetime.utcnow)

class WithdrawalRequest(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    ccp_account = db.Column(db.String(50), nullable=False)
    status = db.Column(db.String(20), default='pending') # pending, approved, rejected
    rejection_reason = db.Column(db.String(200), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    processed_at = db.Column(db.DateTime, nullable=True)

    user = db.relationship('User', backref=db.backref('withdrawals', lazy=True))

class AppConfig(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    normal_daily_limit = db.Column(db.Integer, default=4)
    upgraded_daily_limit = db.Column(db.Integer, default=10)
    telegram_agent_link = db.Column(db.String(200), default='https://t.me/YourAgent')
    total_revenue = db.Column(db.Float, default=0.0)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or not getattr(current_user, 'is_admin', False):
            abort(403)
        return f(*args, **kwargs)
    return decorated_function

def model_to_dict(obj):
    """Convert SQLAlchemy model instance to dictionary."""
    d = {}
    for column in obj.__table__.columns:
        val = getattr(obj, column.name)
        if isinstance(val, datetime):
            d[column.name] = val.isoformat()
        else:
            d[column.name] = val
    return d

def check_auto_withdraw(user):
    """Check if user has reached their auto withdraw threshold and create a request if so."""
    if user.auto_withdraw_threshold and user.ccp_account:
        if user.balance >= user.auto_withdraw_threshold:
            # Check for withdrawal conditions: 40 tasks completed, 100 active referrals
            completed_tasks_count = CompletedTask.query.filter_by(user_id=user.id).count()
            all_referred = User.query.filter_by(referred_by=user.id).all()
            active_referrals_count = 0
            for r_user in all_referred:
                if CompletedTask.query.filter_by(user_id=r_user.id).count() >= 10:
                    active_referrals_count += 1
            
            if completed_tasks_count >= 40 and active_referrals_count >= 100:
                # Check if they already have a pending request
                existing_request = WithdrawalRequest.query.filter_by(user_id=user.id, status='pending').first()
                if not existing_request:
                    amount = user.balance
                    user.balance = 0.0
                    new_request = WithdrawalRequest(user_id=user.id, amount=amount, ccp_account=user.ccp_account)
                    db.session.add(new_request)

with app.app_context():
    db.create_all()
    
    # Auto-migrate new columns
    try:
        inspector = inspect(db.engine)
        if 'user' in inspector.get_table_names():
            columns = [col['name'] for col in inspector.get_columns('user')]
            if 'tiktok_username' not in columns:
                db.session.execute(text('ALTER TABLE "user" ADD COLUMN tiktok_username VARCHAR(50)'))
            if 'tiktok_last_changed' not in columns:
                db.session.execute(text('ALTER TABLE "user" ADD COLUMN tiktok_last_changed TIMESTAMP'))
            if 'membership_type' not in columns:
                db.session.execute(text("ALTER TABLE \"user\" ADD COLUMN membership_type VARCHAR(20) DEFAULT 'free'"))
                db.session.execute(text("UPDATE \"user\" SET membership_type = 'vip_lifetime' WHERE is_upgraded = 1"))
            if 'membership_expires_at' not in columns:
                db.session.execute(text('ALTER TABLE "user" ADD COLUMN membership_expires_at TIMESTAMP'))
            if 'gender' not in columns:
                db.session.execute(text("ALTER TABLE \"user\" ADD COLUMN gender VARCHAR(10) DEFAULT 'male'"))
            if 'age' not in columns:
                db.session.execute(text("ALTER TABLE \"user\" ADD COLUMN age INTEGER DEFAULT 18"))
        if 'task' in inspector.get_table_names():
            columns = [col['name'] for col in inspector.get_columns('task')]
            if 'link' not in columns:
                db.session.execute(text('ALTER TABLE task ADD COLUMN link VARCHAR(500)'))
            if 'max_completions' not in columns:
                db.session.execute(text('ALTER TABLE task ADD COLUMN max_completions INTEGER'))
            if 'target_gender' not in columns:
                db.session.execute(text("ALTER TABLE task ADD COLUMN target_gender VARCHAR(10) DEFAULT 'all'"))
            if 'min_age' not in columns:
                db.session.execute(text('ALTER TABLE task ADD COLUMN min_age INTEGER'))
            if 'max_age' not in columns:
                db.session.execute(text('ALTER TABLE task ADD COLUMN max_age INTEGER'))
            if 'is_boosted' not in columns:
                db.session.execute(text('ALTER TABLE task ADD COLUMN is_boosted BOOLEAN DEFAULT 0'))
        if 'completed_task' in inspector.get_table_names():
            columns = [col['name'] for col in inspector.get_columns('completed_task')]
            if 'completed_at' not in columns:
                db.session.execute(text('ALTER TABLE completed_task ADD COLUMN completed_at TIMESTAMP'))
                # Update existing records to current time so they don't have NULL
                db.session.execute(text("UPDATE completed_task SET completed_at = CURRENT_TIMESTAMP WHERE completed_at IS NULL"))
        if 'withdrawal_request' in inspector.get_table_names():
            columns = [col['name'] for col in inspector.get_columns('withdrawal_request')]
            if 'rejection_reason' not in columns:
                db.session.execute(text('ALTER TABLE withdrawal_request ADD COLUMN rejection_reason VARCHAR(200)'))
        
        if 'app_config' in inspector.get_table_names():
            columns = [col['name'] for col in inspector.get_columns('app_config')]
            if 'telegram_agent_link' not in columns:
                db.session.execute(text("ALTER TABLE app_config ADD COLUMN telegram_agent_link VARCHAR(200) DEFAULT 'https://t.me/YourAgent'"))
            if 'total_revenue' not in columns:
                db.session.execute(text("ALTER TABLE app_config ADD COLUMN total_revenue FLOAT DEFAULT 0.0"))
        db.session.commit()
    except Exception as e:
        print(f"Migration error: {e}")
        db.session.rollback()

    # Create an AppConfig automatically if none exists
    if not AppConfig.query.first():
        config = AppConfig(normal_daily_limit=4, upgraded_daily_limit=10)
        db.session.add(config)
        db.session.commit()

    # Create an admin user automatically if none exists
    if not User.query.filter_by(is_admin=True).first():
        admin_user = User(
            username='admin',
            email='admin@admin.com',
            password_hash=generate_password_hash('admin123'),
            is_admin=True,
            is_upgraded=True,
            membership_type='vip_lifetime',
            referral_code=str(uuid.uuid4())[:8]
        )
        db.session.add(admin_user)
        db.session.commit()

@app.route('/')
def index():
    return render_template('index.html')

@app.before_request
def check_referral():
    ref_code = request.args.get('ref')
    if ref_code:
        request.new_ref_code = ref_code

@app.after_request
def save_referral(response):
    if hasattr(request, 'new_ref_code') and request.new_ref_code:
        expires = datetime.utcnow() + timedelta(hours=48)
        response.set_cookie('ref_code', request.new_ref_code, expires=expires)
    return response

@app.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    
    ref_code = request.args.get('ref')
    if not ref_code:
        ref_code = request.cookies.get('ref_code', '')

    if request.method == 'POST':
        username = request.form.get('username')
        email = request.form.get('email')
        password = request.form.get('password')
        gender = request.form.get('gender', 'male')
        age = request.form.get('age', 18, type=int)
        ref_code_post = request.form.get('ref_code', '')

        user_exists = User.query.filter((User.username == username) | (User.email == email)).first()
        if user_exists:
            flash('اسم المستخدم أو البريد الإلكتروني موجود بالفعل.', 'danger')
            return redirect(url_for('register', ref=ref_code_post))

        hashed_password = generate_password_hash(password)
        new_referral_code = str(uuid.uuid4())[:8]

        referred_by_id = None
        if ref_code_post:
            referrer = User.query.filter_by(referral_code=ref_code_post).first()
            if referrer:
                referred_by_id = referrer.id
                # Note: We no longer reward immediately. The reward is given when the referred user completes 10 tasks.

        new_user = User(
            username=username,
            email=email,
            password_hash=hashed_password,
            gender=gender,
            age=age,
            referral_code=new_referral_code,
            referred_by=referred_by_id
        )
        db.session.add(new_user)
        db.session.commit()

        flash('تم التسجيل بنجاح! يمكنك الآن تسجيل الدخول.', 'success')
        resp = make_response(redirect(url_for('login')))
        resp.delete_cookie('ref_code')
        if hasattr(request, 'new_ref_code'):
            delattr(request, 'new_ref_code')
        return resp

    return render_template('register.html', ref_code=ref_code)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')

        user = User.query.filter_by(email=email).first()
        if user and check_password_hash(user.password_hash, password):
            login_user(user)
            return redirect(url_for('dashboard'))
        else:
            flash('البريد الإلكتروني أو كلمة المرور غير صحيحة.', 'danger')

    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('index'))

@app.route('/dashboard')
@login_required
def dashboard():
    all_referred = User.query.filter_by(referred_by=current_user.id).all()
    pending_referrals_count = 0
    active_referrals_count = 0
    
    for r_user in all_referred:
        completed_count = CompletedTask.query.filter_by(user_id=r_user.id).count()
        if completed_count >= 10:
            active_referrals_count += 1
        else:
            pending_referrals_count += 1
            
    referrals_count = len(all_referred)
    all_tasks = Task.query.all()
    completed_task_ids = [ct.task_id for ct in CompletedTask.query.filter_by(user_id=current_user.id).all()]
    
    # Calculate tasks stats
    total_tasks = len(all_tasks)
    completed_tasks_count = len(completed_task_ids)
    
    config = AppConfig.query.first()
    
    return render_template('dashboard.html', 
                           user=current_user, 
                           referrals_count=referrals_count,
                           active_referrals_count=active_referrals_count,
                           pending_referrals_count=pending_referrals_count,
                           tasks=all_tasks,
                           completed_task_ids=completed_task_ids,
                           total_tasks=total_tasks,
                           completed_tasks_count=completed_tasks_count,
                           config=config)

@app.route('/settings', methods=['GET', 'POST'])
@login_required
def settings():
    if request.method == 'POST':
        ccp_account = request.form.get('ccp_account')
        threshold = request.form.get('auto_withdraw_threshold')
        instagram = request.form.get('instagram_username')
        tiktok = request.form.get('tiktok_username')

        now = datetime.utcnow()
        
        # CCP Account Logic (60 days rule)
        if ccp_account and ccp_account != current_user.ccp_account:
            existing_ccp = User.query.filter_by(ccp_account=ccp_account).first()
            if existing_ccp:
                flash('رقم الحساب (CCP) مستخدم من قبل حساب آخر.', 'danger')
            else:
                if current_user.ccp_last_changed:
                    last_changed_ccp = current_user.ccp_last_changed
                    if last_changed_ccp.tzinfo is not None:
                        last_changed_ccp = last_changed_ccp.replace(tzinfo=None)
                    days_since_ccp = (now - last_changed_ccp).days
                    if days_since_ccp < 60:
                        flash(f'لا يمكنك تغيير حساب CCP الآن. يرجى الانتظار {60 - days_since_ccp} يوماً.', 'danger')
                    else:
                        current_user.ccp_account = ccp_account
                        current_user.ccp_last_changed = now
                else:
                    current_user.ccp_account = ccp_account
                    current_user.ccp_last_changed = now
        elif not ccp_account:
            pass
        
        # Auto withdraw threshold
        if threshold and threshold in ['40', '80', '120']:
            current_user.auto_withdraw_threshold = int(threshold)
        else:
            # Default or disabled if not provided
            current_user.auto_withdraw_threshold = None

        # Instagram Logic (60 days rule)
        if instagram and instagram != current_user.instagram_username:
            existing_ig = User.query.filter_by(instagram_username=instagram).first()
            if existing_ig:
                flash('حساب انستغرام مستخدم من قبل حساب آخر.', 'danger')
            else:
                if current_user.instagram_last_changed:
                    # Convert to offset-naive datetime before subtraction
                    last_changed = current_user.instagram_last_changed
                    if last_changed.tzinfo is not None:
                        last_changed = last_changed.replace(tzinfo=None)
                    days_since = (now - last_changed).days
                    if days_since < 60:
                        flash(f'لا يمكنك تغيير حساب انستغرام الآن. يرجى الانتظار {60 - days_since} يوماً.', 'danger')
                    else:
                        current_user.instagram_username = instagram
                        current_user.instagram_last_changed = now
                else:
                    current_user.instagram_username = instagram
                    current_user.instagram_last_changed = now
        elif not instagram:
            pass

        # TikTok Logic (60 days rule)
        if tiktok and tiktok != current_user.tiktok_username:
            existing_tk = User.query.filter_by(tiktok_username=tiktok).first()
            if existing_tk:
                flash('حساب تيك توك مستخدم من قبل حساب آخر.', 'danger')
            else:
                if current_user.tiktok_last_changed:
                    last_changed_tk = current_user.tiktok_last_changed
                    if last_changed_tk.tzinfo is not None:
                        last_changed_tk = last_changed_tk.replace(tzinfo=None)
                    days_since_tk = (now - last_changed_tk).days
                    if days_since_tk < 60:
                        flash(f'لا يمكنك تغيير حساب تيك توك الآن. يرجى الانتظار {60 - days_since_tk} يوماً.', 'danger')
                    else:
                        current_user.tiktok_username = tiktok
                        current_user.tiktok_last_changed = now
                else:
                    current_user.tiktok_username = tiktok
                    current_user.tiktok_last_changed = now
        elif not tiktok:
            pass

        db.session.commit()
        if not get_flashed_messages(category_filter=['danger']):
            flash('تم حفظ الإعدادات بنجاح.', 'success')
        return redirect(url_for('settings'))

    # Calculate remaining days for Instagram
    can_change_ig = True
    ig_days_remaining = 0
    if current_user.instagram_last_changed:
        last_changed = current_user.instagram_last_changed
        if last_changed.tzinfo is not None:
            last_changed = last_changed.replace(tzinfo=None)
        days_since = (datetime.utcnow() - last_changed).days
        if days_since < 60:
            can_change_ig = False
            ig_days_remaining = 60 - days_since

    # Calculate remaining days for TikTok
    can_change_tk = True
    tk_days_remaining = 0
    if current_user.tiktok_last_changed:
        last_changed_tk = current_user.tiktok_last_changed
        if last_changed_tk.tzinfo is not None:
            last_changed_tk = last_changed_tk.replace(tzinfo=None)
        days_since_tk = (datetime.utcnow() - last_changed_tk).days
        if days_since_tk < 60:
            can_change_tk = False
            tk_days_remaining = 60 - days_since_tk

    # Calculate remaining days for CCP
    can_change_ccp = True
    ccp_days_remaining = 0
    if current_user.ccp_last_changed:
        last_changed_ccp = current_user.ccp_last_changed
        if last_changed_ccp.tzinfo is not None:
            last_changed_ccp = last_changed_ccp.replace(tzinfo=None)
        days_since_ccp = (datetime.utcnow() - last_changed_ccp).days
        if days_since_ccp < 60:
            can_change_ccp = False
            ccp_days_remaining = 60 - days_since_ccp

    return render_template('settings.html', 
                           user=current_user, 
                           can_change_ig=can_change_ig, 
                           ig_days_remaining=ig_days_remaining,
                           can_change_tk=can_change_tk,
                           tk_days_remaining=tk_days_remaining,
                           can_change_ccp=can_change_ccp,
                           ccp_days_remaining=ccp_days_remaining)

@app.route('/upgrade_instructions/<plan>')
@login_required
def upgrade_instructions(plan):
    if plan not in ['vip_10_days', 'vip_lifetime']:
        abort(400)
    
    config = AppConfig.query.first()
    return render_template('agent_upgrade.html', plan=plan, telegram_link=config.telegram_agent_link)

@app.route('/upgrade/<plan>', methods=['POST'])
@login_required
def upgrade(plan):
    if current_user.membership_type == 'vip_lifetime':
        flash('حسابك مطور مدى الحياة بالفعل.', 'info')
        return redirect(url_for('dashboard'))
        
    if plan == 'vip_10_days':
        price = 10.0
    elif plan == 'vip_lifetime':
        price = 40.0
    else:
        abort(400)
        
    if current_user.balance < price:
        flash(f'رصيدك غير كافٍ للترقية. تحتاج إلى {price}$', 'danger')
        return redirect(url_for('dashboard'))
        
    # Deduct balance
    current_user.balance -= price
    
    current_user.membership_type = plan
    config = AppConfig.query.first()
    
    if plan == 'vip_10_days':
        if config:
            config.total_revenue += 10.0
        if current_user.membership_expires_at and current_user.membership_expires_at > datetime.utcnow():
            current_user.membership_expires_at += timedelta(days=10)
        else:
            current_user.membership_expires_at = datetime.utcnow() + timedelta(days=10)
    else:
        if config:
            config.total_revenue += 40.0
        current_user.membership_expires_at = None
        
    db.session.commit()
    flash('تم ترقية حسابك بنجاح!', 'success')
    return redirect(url_for('dashboard'))

@app.route('/merchant/boost', methods=['GET', 'POST'])
@login_required
def merchant_boost():
    if request.method == 'POST':
        plan = request.form.get('plan')
        platform = request.form.get('platform')
        username = request.form.get('username')
        target_gender = request.form.get('target_gender', 'all')
        min_age = request.form.get('min_age')
        max_age = request.form.get('max_age')
        
        # Build the link based on platform
        if platform == 'tiktok':
            link = f'https://tiktok.com/@{username}' if not username.startswith('@') else f'https://tiktok.com/{username}'
            title = 'متابعة حساب تيك توك (مدعوم)'
        else: # default to instagram
            link = f'https://instagram.com/{username}'
            title = 'متابعة حساب انستغرام (مدعوم)'
        
        min_age = int(min_age) if min_age else None
        max_age = int(max_age) if max_age else None
        
        if plan == '1000':
            price = 10.0
            followers = 1000
            reward = 0.005 # Platform profit
        elif plan == '5000':
            price = 40.0
            followers = 5000
            reward = 0.005 # Platform profit
        elif plan == '10000':
            price = 70.0
            followers = 10000
            reward = 0.005
        elif plan == '50000':
            price = 300.0
            followers = 50000
            reward = 0.005
        elif plan == '100000':
            price = 500.0
            followers = 100000
            reward = 0.005
        else:
            flash('خطة غير صالحة', 'danger')
            return redirect(url_for('merchant_boost'))
            
        if current_user.balance < price:
            flash('رصيدك غير كافٍ. يرجى إنجاز المهام لجمع الرصيد المطلوب.', 'danger')
            return redirect(url_for('merchant_boost'))
            
        current_user.balance -= price
        
        new_task = Task(
            title=title,
            description='يرجى متابعة هذا الحساب لدعمه.',
            link=link,
            reward_normal=reward,
            reward_upgraded=reward,
            max_completions=followers,
            target_gender=target_gender,
            min_age=min_age,
            max_age=max_age,
            is_boosted=True
        )
        db.session.add(new_task)
        
        config = AppConfig.query.first()
        if config:
            config.total_revenue += price
            
        db.session.commit()
        
        flash('تمت إضافة حملتك بنجاح! ستظهر مهمتك في أعلى قائمة المهام.', 'success')
        return redirect(url_for('dashboard'))
        
    config = AppConfig.query.first()
    return render_template('merchant_boost.html', user=current_user, config=config)

@app.route('/tasks')
@login_required
def tasks():
    completed_task_ids = [ct.task_id for ct in CompletedTask.query.filter_by(user_id=current_user.id).all()]
    
    # Get recent completions in last 24 hours
    twenty_four_hours_ago = datetime.utcnow() - timedelta(hours=24)
    recent_completions = CompletedTask.query.filter(
        CompletedTask.user_id == current_user.id,
        CompletedTask.completed_at >= twenty_four_hours_ago
    ).count()
    
    config = AppConfig.query.first()
    if current_user.is_upgraded:
        limit = config.upgraded_daily_limit
    else:
        # Check and downgrade if expired
        if current_user.membership_type == 'vip_10_days' and current_user.membership_expires_at and current_user.membership_expires_at <= datetime.utcnow():
            current_user.membership_type = 'free'
            current_user.membership_expires_at = None
            db.session.commit()
        limit = config.normal_daily_limit
        
    available_slots = max(0, limit - recent_completions)
    
    # Build query for available tasks
    query = Task.query
    if completed_task_ids:
        query = query.filter(~Task.id.in_(completed_task_ids))
        
    # Filter by gender
    if current_user.gender:
        query = query.filter((Task.target_gender == 'all') | (Task.target_gender == current_user.gender))
        
    # Filter by age
    if current_user.age:
        query = query.filter((Task.min_age == None) | (Task.min_age <= current_user.age))
        query = query.filter((Task.max_age == None) | (Task.max_age >= current_user.age))
        
    uncompleted_tasks_raw = query.order_by(Task.is_boosted.desc(), Task.id.desc()).all()
    
    # Filter out tasks that reached max_completions
    uncompleted_tasks = []
    for t in uncompleted_tasks_raw:
        if t.max_completions:
            c_count = CompletedTask.query.filter_by(task_id=t.id).count()
            if c_count >= t.max_completions:
                continue
        uncompleted_tasks.append(t)
        
    uncompleted_tasks = uncompleted_tasks[:available_slots]
    
    if completed_task_ids:
        completed_tasks = Task.query.filter(Task.id.in_(completed_task_ids)).all()
    else:
        completed_tasks = []
        
    all_tasks_to_show = uncompleted_tasks + completed_tasks
    
    return render_template('tasks.html', 
                           tasks=all_tasks_to_show, 
                           completed_task_ids=completed_task_ids,
                           limit=limit,
                           recent_completions=recent_completions)

@app.route('/tasks/complete/<int:task_id>', methods=['POST'])
@login_required
def complete_task(task_id):
    # Verify daily limit first
    twenty_four_hours_ago = datetime.utcnow() - timedelta(hours=24)
    recent_completions = CompletedTask.query.filter(
        CompletedTask.user_id == current_user.id,
        CompletedTask.completed_at >= twenty_four_hours_ago
    ).count()
    
    config = AppConfig.query.first()
    limit = config.upgraded_daily_limit if current_user.is_upgraded else config.normal_daily_limit
    if recent_completions >= limit:
        flash('لقد وصلت للحد الأقصى من المهام المتاحة لك خلال 24 ساعة.', 'danger')
        return redirect(url_for('tasks'))

    task = Task.query.get_or_404(task_id)
    
    # Check max completions limit
    if task.max_completions:
        c_count = CompletedTask.query.filter_by(task_id=task.id).count()
        if c_count >= task.max_completions:
            flash('عذراً، هذه المهمة وصلت للحد الأقصى من الإنجازات ولم تعد متاحة.', 'danger')
            return redirect(url_for('tasks'))
            
    # Check gender targeting
    if task.target_gender != 'all' and task.target_gender != current_user.gender:
        flash('هذه المهمة غير متاحة لك بناءً على متطلبات الاستهداف (الجنس).', 'danger')
        return redirect(url_for('tasks'))
        
    # Check age targeting
    if current_user.age:
        if task.min_age and current_user.age < task.min_age:
            flash('هذه المهمة غير متاحة لك بناءً على متطلبات الاستهداف (العمر).', 'danger')
            return redirect(url_for('tasks'))
        if task.max_age and current_user.age > task.max_age:
            flash('هذه المهمة غير متاحة لك بناءً على متطلبات الاستهداف (العمر).', 'danger')
            return redirect(url_for('tasks'))

    if CompletedTask.query.filter_by(user_id=current_user.id, task_id=task.id).first():
        flash('لقد قمت بإنجاز هذه المهمة مسبقاً.', 'danger')
        return redirect(url_for('tasks'))
    
    new_completion = CompletedTask(user_id=current_user.id, task_id=task.id)
    db.session.add(new_completion)
    
    reward = task.reward_upgraded if current_user.is_upgraded else task.reward_normal
    current_user.balance += reward
    check_auto_withdraw(current_user)
    db.session.commit()
    
    # Check if this user just reached 10 tasks to reward their referrer
    user_completed_count = CompletedTask.query.filter_by(user_id=current_user.id).count()
    if user_completed_count == 10 and current_user.referred_by:
        referrer = User.query.get(current_user.referred_by)
        if referrer:
            if referrer.is_upgraded:
                referrer.balance += 0.2
            else:
                referrer.balance += 0.05
            check_auto_withdraw(referrer)
            db.session.commit()
            
    # If boosted task and reached max_completions, delete it
    if task.is_boosted and task.max_completions:
        current_count = CompletedTask.query.filter_by(task_id=task.id).count()
        if current_count >= task.max_completions:
            db.session.delete(task)
            db.session.commit()
    
    flash(f'تم إنجاز المهمة بنجاح! تمت إضافة {reward}$ إلى رصيدك.', 'success')
    return redirect(url_for('tasks'))

# Admin Routes
@app.route('/admin')
@admin_required
def admin_dashboard():
    search_query = request.args.get('q', '')
    if search_query:
        users = User.query.filter(User.username.ilike(f'%{search_query}%') | User.email.ilike(f'%{search_query}%')).all()
    else:
        users = User.query.all()
        
    for u in users:
        u.completed_tasks_count = CompletedTask.query.filter_by(user_id=u.id).count()
        referrals = User.query.filter_by(referred_by=u.id).all()
        u.total_invites = len(referrals)
        
        u.active_invites = 0
        u.inactive_invites = 0
        u.upgraded_invites = 0
        
        for r in referrals:
            if r.is_upgraded:
                u.upgraded_invites += 1
                
            r_completed = CompletedTask.query.filter_by(user_id=r.id).count()
            if r_completed >= 10:
                u.active_invites += 1
            else:
                u.inactive_invites += 1
                
        u.upgraded_percentage = (u.upgraded_invites / u.total_invites * 100) if u.total_invites > 0 else 0
        
    task_query = request.args.get('tq', '')
    if task_query:
        tasks = Task.query.filter(Task.title.ilike(f'%{task_query}%') | Task.link.ilike(f'%{task_query}%')).all()
    else:
        tasks = Task.query.all()
        
    # Add stats to tasks
    for task in tasks:
        task.completions_count = CompletedTask.query.filter_by(task_id=task.id).count()
        
    config = AppConfig.query.first()
    
    # Withdrawal Requests
    pending_withdrawals = WithdrawalRequest.query.filter_by(status='pending').order_by(WithdrawalRequest.created_at.desc()).all()
    completed_withdrawals = WithdrawalRequest.query.filter(WithdrawalRequest.status.in_(['approved', 'rejected'])).order_by(WithdrawalRequest.processed_at.desc()).limit(15).all()
    
    # Stats
    total_users = User.query.count()
    # Updated calculation for VIP users using is_upgraded property
    upgraded_users = sum(1 for u in User.query.all() if u.is_upgraded)
    total_paid_result = db.session.query(db.func.sum(WithdrawalRequest.amount)).filter_by(status='approved').scalar()
    total_paid = total_paid_result if total_paid_result else 0.0
    pending_amount_result = db.session.query(db.func.sum(WithdrawalRequest.amount)).filter_by(status='pending').scalar()
    pending_amount = pending_amount_result if pending_amount_result else 0.0

    return render_template('admin_dashboard.html', 
                           users=users, 
                           tasks=tasks, 
                           config=config,
                           pending_withdrawals=pending_withdrawals,
                           completed_withdrawals=completed_withdrawals,
                           total_users=total_users,
                           upgraded_users=upgraded_users,
                           total_paid=total_paid,
                           pending_amount=pending_amount)

@app.route('/admin/withdrawals/<int:req_id>/<action>', methods=['POST'])
@admin_required
def admin_process_withdrawal(req_id, action):
    req = WithdrawalRequest.query.get_or_404(req_id)
    if req.status != 'pending':
        flash('هذا الطلب تمت معالجته مسبقاً.', 'warning')
        return redirect(url_for('admin_dashboard'))
        
    if action == 'approve':
        req.status = 'approved'
        req.processed_at = datetime.utcnow()
        flash(f'تمت الموافقة على سحب {req.amount}$ للمستخدم {req.user.username}.', 'success')
    elif action == 'reject':
        reason = request.form.get('reason', 'سبب غير محدد')
        req.status = 'rejected'
        req.rejection_reason = reason
        req.processed_at = datetime.utcnow()
        # Refund 50% of the amount (deduct 50%)
        req.user.balance += (req.amount * 0.5)
        flash(f'تم رفض طلب السحب بسبب "{reason}" وتم تصفير 50% من الأموال للمستخدم {req.user.username}.', 'info')
        
    db.session.commit()
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/config/update', methods=['POST'])
@admin_required
def admin_update_config():
    config = AppConfig.query.first()
    normal_limit = request.form.get('normal_daily_limit')
    upgraded_limit = request.form.get('upgraded_daily_limit')
    telegram_agent_link = request.form.get('telegram_agent_link')
    
    if normal_limit and upgraded_limit:
        config.normal_daily_limit = int(normal_limit)
        config.upgraded_daily_limit = int(upgraded_limit)
        
    if telegram_agent_link:
        config.telegram_agent_link = telegram_agent_link
        
    db.session.commit()
    flash('تم تحديث الإعدادات بنجاح.', 'success')
        
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/tasks/add', methods=['POST'])
@admin_required
def admin_add_task():
    task_type = request.form.get('task_type', 'normal')
    
    if task_type == 'follow':
        platform = request.form.get('platform')
        username = request.form.get('target_username')
        if platform == 'tiktok':
            title = 'متابعة حساب تيك توك'
            description = 'قم بمتابعة هذا الحساب على تيك توك'
            link = f'https://tiktok.com/@{username}' if not username.startswith('@') else f'https://tiktok.com/{username}'
        elif platform == 'instagram':
            title = 'متابعة حساب انستغرام'
            description = 'قم بمتابعة هذا الحساب على انستغرام'
            link = f'https://instagram.com/{username}'
        else:
            flash('منصة غير صالحة.', 'danger')
            return redirect(url_for('admin_dashboard'))
    elif task_type == 'comment':
        platform = request.form.get('platform')
        post_link = request.form.get('target_link')
        if platform == 'tiktok':
            title = 'إعجاب وتعليق على بوست تيك توك'
        elif platform == 'instagram':
            title = 'إعجاب وتعليق على بوست انستغرام'
        else:
            title = 'إعجاب وتعليق على بوست'
            
        description = 'إعجاب وتعليق ايجابي'
        link = post_link
    else:
        title = request.form.get('title')
        description = request.form.get('description')
        link = request.form.get('link')
        
    max_completions = request.form.get('max_completions')
    max_completions = int(max_completions) if max_completions else None
    target_gender = request.form.get('target_gender', 'all')
    min_age = request.form.get('min_age')
    min_age = int(min_age) if min_age else None
    max_age = request.form.get('max_age')
    max_age = int(max_age) if max_age else None
    
    new_task = Task(
        title=title, 
        description=description, 
        link=link,
        max_completions=max_completions,
        target_gender=target_gender,
        min_age=min_age,
        max_age=max_age
    )
    db.session.add(new_task)
    db.session.commit()
    
    flash('تمت إضافة المهمة بنجاح.', 'success')
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/tasks/delete/<int:task_id>', methods=['POST'])
@admin_required
def admin_delete_task(task_id):
    task = Task.query.get_or_404(task_id)
    CompletedTask.query.filter_by(task_id=task.id).delete()
    db.session.delete(task)
    db.session.commit()
    flash('تم حذف المهمة بنجاح.', 'success')
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/users/update/<int:user_id>', methods=['POST'])
@admin_required
def admin_update_user(user_id):
    user = User.query.get_or_404(user_id)
    balance = request.form.get('balance')
    if balance is not None:
        try:
            user.balance = float(balance)
            check_auto_withdraw(user)
            db.session.commit()
            flash(f'تم تحديث رصيد المستخدم {user.username} بنجاح.', 'success')
        except ValueError:
            flash('الرصيد المدخل غير صالح.', 'danger')
            
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/user/<int:user_id>/update_membership', methods=['POST'])
@admin_required
def admin_update_user_membership(user_id):
    user = User.query.get_or_404(user_id)
    membership_type = request.form.get('membership_type')
    
    if membership_type in ['free', 'vip_10_days', 'vip_lifetime']:
        user.membership_type = membership_type
        config = AppConfig.query.first()
        
        if membership_type == 'vip_10_days':
            if config:
                config.total_revenue += 10.0
            user.membership_expires_at = datetime.utcnow() + timedelta(days=10)
        elif membership_type == 'vip_lifetime':
            if config:
                config.total_revenue += 40.0
            user.membership_expires_at = None
        else:
            user.membership_expires_at = None
            
        db.session.commit()
        flash(f'تم تحديث باقة المستخدم {user.username} بنجاح واحتساب العوائد.', 'success')
    else:
        flash('الباقة المحددة غير صالحة.', 'danger')
        
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/users/delete/<int:user_id>', methods=['POST'])
@admin_required
def admin_delete_user(user_id):
    user = User.query.get_or_404(user_id)
    if user.is_admin:
        flash('لا يمكن حذف حساب المسؤول.', 'danger')
        return redirect(url_for('admin_dashboard'))
        
    # Remove references to this user
    User.query.filter_by(referred_by=user.id).update({User.referred_by: None})
    CompletedTask.query.filter_by(user_id=user.id).delete()
    WithdrawalRequest.query.filter_by(user_id=user.id).delete()
    
    db.session.delete(user)
    db.session.commit()
    flash(f'تم حذف المستخدم {user.username} بنجاح.', 'success')
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/users/reset_password/<int:user_id>', methods=['POST'])
@admin_required
def admin_reset_password(user_id):
    user = User.query.get_or_404(user_id)
    new_password = request.form.get('new_password')
    
    if new_password and len(new_password) >= 6:
        user.password_hash = generate_password_hash(new_password)
        db.session.commit()
        flash(f'تم تعيين كلمة مرور جديدة للمستخدم {user.username} بنجاح.', 'success')
    else:
        flash('كلمة المرور يجب أن تكون 6 أحرف على الأقل.', 'danger')
        
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/backup/export')
@admin_required
def export_backup():
    data = {
        'users': [model_to_dict(u) for u in User.query.all()],
        'tasks': [model_to_dict(t) for t in Task.query.all()],
        'completed_tasks': [model_to_dict(c) for c in CompletedTask.query.all()],
        'withdrawals': [model_to_dict(w) for w in WithdrawalRequest.query.all()],
        'config': [model_to_dict(c) for c in AppConfig.query.all()]
    }
    
    response = app.response_class(
        response=json.dumps(data, ensure_ascii=False, indent=2),
        status=200,
        mimetype='application/json'
    )
    filename = f'backup_{datetime.utcnow().strftime("%Y%m%d_%H%M%S")}.json'
    response.headers['Content-Disposition'] = f'attachment; filename={filename}'
    return response

@app.route('/admin/backup/import', methods=['POST'])
@admin_required
def import_backup():
    if 'backup_file' not in request.files:
        flash('لم يتم تحديد ملف.', 'danger')
        return redirect(url_for('admin_dashboard') + '?tab=tasks')
        
    file = request.files['backup_file']
    if file.filename == '':
        flash('لم يتم تحديد ملف.', 'danger')
        return redirect(url_for('admin_dashboard') + '?tab=tasks')
        
    if not file.filename.endswith('.json'):
        flash('يجب أن يكون الملف بصيغة JSON.', 'danger')
        return redirect(url_for('admin_dashboard') + '?tab=tasks')
        
    try:
        data = json.load(file)
        
        # Clear existing data
        WithdrawalRequest.query.delete()
        CompletedTask.query.delete()
        Task.query.delete()
        AppConfig.query.delete()
        
        # Remove foreign keys before deleting users
        User.query.update({User.referred_by: None})
        User.query.delete()
        
        db.session.commit()
        
        def parse_dt(val):
            return datetime.fromisoformat(val) if val else None

        # Restore Users
        for u_data in data.get('users', []):
            u = User(**{k: v for k, v in u_data.items() if k not in ['ccp_last_changed', 'instagram_last_changed', 'tiktok_last_changed', 'membership_expires_at']})
            u.ccp_last_changed = parse_dt(u_data.get('ccp_last_changed'))
            u.instagram_last_changed = parse_dt(u_data.get('instagram_last_changed'))
            u.tiktok_last_changed = parse_dt(u_data.get('tiktok_last_changed'))
            u.membership_expires_at = parse_dt(u_data.get('membership_expires_at'))
            db.session.add(u)
        db.session.commit()
        
        # Restore Tasks
        for t_data in data.get('tasks', []):
            t = Task(**t_data)
            db.session.add(t)
            
        # Restore Config
        for c_data in data.get('config', []):
            c = AppConfig(**c_data)
            db.session.add(c)
            
        # Restore Completed Tasks
        for ct_data in data.get('completed_tasks', []):
            ct = CompletedTask(**{k: v for k, v in ct_data.items() if k != 'completed_at'})
            ct.completed_at = parse_dt(ct_data.get('completed_at'))
            db.session.add(ct)
            
        # Restore Withdrawals
        for w_data in data.get('withdrawals', []):
            w = WithdrawalRequest(**{k: v for k, v in w_data.items() if k not in ['created_at', 'processed_at']})
            w.created_at = parse_dt(w_data.get('created_at'))
            w.processed_at = parse_dt(w_data.get('processed_at'))
            db.session.add(w)
            
        db.session.commit()
        flash('تم استعادة النسخة الاحتياطية بنجاح! يرجى تسجيل الدخول مجدداً.', 'success')
        logout_user()
        return redirect(url_for('login'))
        
    except Exception as e:
        db.session.rollback()
        flash(f'حدث خطأ أثناء الاستعادة: {str(e)}', 'danger')
        return redirect(url_for('admin_dashboard') + '?tab=tasks')

def ping_server():
    """Ping the server every 30-40 seconds to keep it awake."""
    url = "https://reversed-unz3.onrender.com/"
    while True:
        try:
            time.sleep(random.randint(30, 40))
            requests.get(url)
        except Exception as e:
            pass

# Start the ping thread as a daemon so it exits when the main process exits
ping_thread = threading.Thread(target=ping_server, daemon=True)
ping_thread.start()

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=8000)
