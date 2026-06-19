import os
import uuid
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, flash, abort
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash

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

    referrals = db.relationship('User', backref=db.backref('referrer', remote_side=[id]))

class Task(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=False)
    reward_normal = db.Column(db.Float, default=0.05)
    reward_upgraded = db.Column(db.Float, default=0.5)

class CompletedTask(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    task_id = db.Column(db.Integer, db.ForeignKey('task.id'), nullable=False)

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

with app.app_context():
    db.create_all()
    # Create an admin user automatically if none exists
    if not User.query.filter_by(is_admin=True).first():
        admin_user = User(
            username='admin',
            email='admin@admin.com',
            password_hash=generate_password_hash('admin123'),
            is_admin=True,
            is_upgraded=True,
            referral_code=str(uuid.uuid4())[:8]
        )
        db.session.add(admin_user)
        db.session.commit()

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    
    ref_code = request.args.get('ref', '')

    if request.method == 'POST':
        username = request.form.get('username')
        email = request.form.get('email')
        password = request.form.get('password')
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
                
                # Reward the referrer
                if referrer.is_upgraded:
                    referrer.balance += 0.1
                else:
                    referrer.balance += 0.01
                db.session.add(referrer)

        new_user = User(
            username=username,
            email=email,
            password_hash=hashed_password,
            referral_code=new_referral_code,
            referred_by=referred_by_id
        )
        db.session.add(new_user)
        db.session.commit()

        flash('تم التسجيل بنجاح! يمكنك الآن تسجيل الدخول.', 'success')
        return redirect(url_for('login'))

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
    referrals_count = User.query.filter_by(referred_by=current_user.id).count()
    return render_template('dashboard.html', user=current_user, referrals_count=referrals_count)

@app.route('/upgrade', methods=['POST'])
@login_required
def upgrade():
    if current_user.is_upgraded:
        flash('حسابك مطور بالفعل.', 'info')
        return redirect(url_for('dashboard'))
    
    current_user.is_upgraded = True
    db.session.commit()
    flash('تم ترقية حسابك بنجاح! ستحصل الآن على 0.1 دولار لكل إحالة و 0.5 دولار للمهمة.', 'success')
    return redirect(url_for('dashboard'))

@app.route('/tasks')
@login_required
def tasks():
    all_tasks = Task.query.all()
    completed_task_ids = [ct.task_id for ct in CompletedTask.query.filter_by(user_id=current_user.id).all()]
    return render_template('tasks.html', tasks=all_tasks, completed_task_ids=completed_task_ids)

@app.route('/tasks/complete/<int:task_id>', methods=['POST'])
@login_required
def complete_task(task_id):
    task = Task.query.get_or_404(task_id)
    if CompletedTask.query.filter_by(user_id=current_user.id, task_id=task.id).first():
        flash('لقد قمت بإنجاز هذه المهمة مسبقاً.', 'danger')
        return redirect(url_for('tasks'))
    
    new_completion = CompletedTask(user_id=current_user.id, task_id=task.id)
    db.session.add(new_completion)
    
    reward = task.reward_upgraded if current_user.is_upgraded else task.reward_normal
    current_user.balance += reward
    db.session.commit()
    
    flash(f'تم إنجاز المهمة بنجاح! تمت إضافة {reward}$ إلى رصيدك.', 'success')
    return redirect(url_for('tasks'))

# Admin Routes
@app.route('/admin')
@admin_required
def admin_dashboard():
    users = User.query.all()
    tasks = Task.query.all()
    return render_template('admin_dashboard.html', users=users, tasks=tasks)

@app.route('/admin/tasks/add', methods=['POST'])
@admin_required
def admin_add_task():
    title = request.form.get('title')
    description = request.form.get('description')
    
    new_task = Task(title=title, description=description)
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
            db.session.commit()
            flash(f'تم تحديث رصيد المستخدم {user.username} بنجاح.', 'success')
        except ValueError:
            flash('الرصيد المدخل غير صالح.', 'danger')
            
    return redirect(url_for('admin_dashboard'))

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=8000)
