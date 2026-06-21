from flask import Flask, render_template, request, redirect, url_for, session, make_response
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import text
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
from functools import wraps
import csv
import io
import os

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'gyomu-diary-secret-key-setouchi-2024')

# postgres:// → postgresql:// に変換（Railway/Render対応）
_db_url = os.environ.get('DATABASE_URL', 'sqlite:///gyomu_diary.db')
if _db_url.startswith('postgres://'):
    _db_url = _db_url.replace('postgres://', 'postgresql://', 1)
# Supabase は SSL 必須
if 'supabase' in _db_url and 'sslmode' not in _db_url:
    _db_url += '?sslmode=require'
app.config['SQLALCHEMY_DATABASE_URI'] = _db_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

# ===== 定数 =====

ROLE_LABELS = {
    'admin': 'システム管理者',
    'senmu': '専務理事',
    'jimukyokucho': '事務局長',
    'kacho': '課長',
    'shukan': '主幹',
    'kacho_hosa': '課長補佐',
    'shunin': '主任',
    'shuji': '主事',
    'shujiho': '主事補',
}

DEPARTMENTS = ['指導課', '総務共済課']

# 承認権限を持つ役職（admin は承認しない）
APPROVER_ROLES = {'senmu', 'jimukyokucho', 'kacho'}

CHAMBER_NAMES = [
    '東かがわ市', 'さぬき市', '小豆島町', '土庄町', '三木町',
    '高松市牟礼庵治', '高松市中央', '直島町', '綾川町', '丸亀市飯綾',
    '宇多津', 'まんのう町', '琴平町', '三豊市', '観音寺市大豊',
]

GUIDANCE_CATEGORIES = {
    '組織運営指導': [
        '1. 総会・理事会・委員会等',
        '2. 青年部・女性部・部会',
        '3. 組織強化・会員増強',
        '4. 適正化指導',
        '5. 自主財源確保・財政力強化',
        '6. 県交付金関係',
        '7. 法令・定款・規程等',
        '8. 許認可・届出関係',
        '9. 事業計画・収支予算',
        '10. 事業報告・収支決算',
        '11. 人事・労務・表彰関係',
        '12. 経理・税務関係',
        '13. 要望・陳情支援',
        '14. 上記以外の組織運営指導',
    ],
    '事業指導': [
        '1. 提案型経営支援（巡回訪問・指導推進）',
        '2. 経営発達支援事業',
        '3. 事業継続力強化支援事業',
        '4. 経営計画策定・実行支援（補助金・助成金・法認定等）',
        '5. 専門家派遣活用支援（エキスパート・サポート事業等）',
        '6. 経営安定特別相談事業',
        '7. 制度改正等の課題解決環境整備事業',
        '8. 事業環境変化対応型支援事業',
        '9. 販路開拓支援（展示会・商談会・海外展開等）',
        '10. 金融支援',
        '11. 事業承継支援事業',
        '12. 情報化推進事業',
        '13. 施策普及・情報発信事業',
        '14. 商工会役職員研修事業',
        '15. スーパーバイザー事業',
        '16. 婚活支援事業',
        '17. 地方創生推進事業（小規模企業振興条例等）',
        '18. 中小企業景況調査事業',
        '19. 受託事業（容器包装リサイクル）',
        '20. 受託事業（商工会カード）',
        '21. その他の受託事業',
        '22. 商工貯蓄共済事業',
        '23. 会員福祉共済事業',
        '24. 特定退職金共済',
        '25. その他共済事業',
        '26. 上記以外の事業指導',
    ],
}

# ===== モデル =====

class User(db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    full_name = db.Column(db.String(100), nullable=False)
    role = db.Column(db.String(20), nullable=False, default='shuji')
    department = db.Column(db.String(50), default='')
    created_at = db.Column(db.DateTime, default=datetime.now)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def role_label(self):
        return ROLE_LABELS.get(self.role, self.role)

    @property
    def is_approver(self):
        return self.role in APPROVER_ROLES

    def can_view_entry(self, entry):
        """自分の日誌、または承認ライン上にある日誌のみ閲覧可能"""
        if self.role == 'admin':
            return True
        if self.id == entry.user_id:
            return True
        if self.role in ('senmu', 'jimukyokucho'):
            return True
        if self.role == 'kacho' and self.department and self.department == entry.user.department:
            return True
        return False


class DiaryEntry(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    date = db.Column(db.Date, nullable=False)
    visitors = db.Column(db.Text, default='')
    meetings = db.Column(db.Text, default='')
    business_content = db.Column(db.Text, default='')
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now)

    user = db.relationship('User', backref='diary_entries')
    guidance_records = db.relationship('GuidanceRecord', backref='diary_entry', cascade='all, delete-orphan')
    approvals = db.relationship('Approval', backref='diary_entry', cascade='all, delete-orphan')

    def get_required_approvers(self):
        role = self.user.role
        if role == 'senmu':
            return []
        elif role == 'jimukyokucho':
            return ['senmu']
        elif role == 'kacho':
            return ['jimukyokucho', 'senmu']
        else:
            return ['kacho', 'jimukyokucho', 'senmu']

    def get_approval_status(self):
        return {a.approver_role: a for a in self.approvals}

    def is_fully_approved(self):
        required = self.get_required_approvers()
        if not required:
            return True
        approved = self.get_approval_status()
        return all(r in approved for r in required)

    def can_be_approved_by(self, user):
        if user.id == self.user_id:
            return False
        required = self.get_required_approvers()
        if user.role not in required:
            return False
        approved = self.get_approval_status()
        if user.role in approved:
            return False
        # 課長は同じ所属の職員のみ承認可能
        if user.role == 'kacho':
            if not user.department or user.department != self.user.department:
                return False
        return True

    def next_approver_role(self):
        required = self.get_required_approvers()
        approved = self.get_approval_status()
        for r in required:
            if r not in approved:
                return r
        return None


class GuidanceRecord(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    diary_entry_id = db.Column(db.Integer, db.ForeignKey('diary_entry.id'), nullable=False)
    chamber_name = db.Column(db.String(100), nullable=False)
    staff_name = db.Column(db.String(100), default='')
    guidance_category = db.Column(db.String(50), default='')
    guidance_item = db.Column(db.String(200), default='')
    guidance_content = db.Column(db.Text, default='')


class Approval(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    diary_entry_id = db.Column(db.Integer, db.ForeignKey('diary_entry.id'), nullable=False)
    approver_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    approver_role = db.Column(db.String(20), nullable=False)
    comment = db.Column(db.Text, default='')
    approved_at = db.Column(db.DateTime, default=datetime.now)

    approver = db.relationship('User')

# ===== 認証ヘルパー =====

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated


def role_required(*roles):
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if 'user_id' not in session:
                return redirect(url_for('login'))
            u = db.session.get(User, session['user_id'])
            if u.role not in roles:
                return render_template('error.html', message='この操作を行う権限がありません。'), 403
            return f(*args, **kwargs)
        return decorated
    return decorator


def get_current_user():
    if 'user_id' in session:
        return db.session.get(User, session['user_id'])
    return None

# ===== ルート =====

@app.route('/')
def index():
    return redirect(url_for('dashboard') if 'user_id' in session else url_for('login'))


@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        u = User.query.filter_by(username=request.form['username']).first()
        if u and u.check_password(request.form['password']):
            session['user_id'] = u.id
            return redirect(url_for('dashboard'))
        error = 'ユーザー名またはパスワードが正しくありません'
    return render_template('login.html', error=error)


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


@app.route('/dashboard')
@login_required
def dashboard():
    user = get_current_user()
    page = request.args.get('page', 1, type=int)
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')
    author_id = request.args.get('author_id', '')

    query = DiaryEntry.query
    if user.role in ('admin', 'senmu', 'jimukyokucho'):
        # 全件表示
        if author_id:
            query = query.filter_by(user_id=int(author_id))
    elif user.role == 'kacho':
        # 同じ所属の職員のみ
        dept_ids = [u.id for u in User.query.filter_by(department=user.department).all()]
        query = query.filter(DiaryEntry.user_id.in_(dept_ids))
        if author_id and int(author_id) in dept_ids:
            query = query.filter_by(user_id=int(author_id))
    else:
        # 自分の日誌のみ
        query = query.filter_by(user_id=user.id)

    if date_from:
        query = query.filter(DiaryEntry.date >= datetime.strptime(date_from, '%Y-%m-%d').date())
    if date_to:
        query = query.filter(DiaryEntry.date <= datetime.strptime(date_to, '%Y-%m-%d').date())

    pagination = query.order_by(DiaryEntry.date.desc()).paginate(page=page, per_page=20)

    # 承認待ち一覧
    pending = []
    if user.is_approver:
        for entry in DiaryEntry.query.all():
            if entry.can_be_approved_by(user):
                pending.append(entry)

    # 絞り込み用ユーザー一覧（表示範囲内のみ）
    if user.role in ('admin', 'senmu', 'jimukyokucho'):
        all_users = User.query.order_by(User.full_name).all()
    elif user.role == 'kacho':
        all_users = User.query.filter_by(department=user.department).order_by(User.full_name).all()
    else:
        all_users = []

    return render_template('dashboard.html',
                           user=user,
                           entries=pagination.items,
                           pagination=pagination,
                           pending=pending,
                           all_users=all_users,
                           date_from=date_from,
                           date_to=date_to,
                           author_id=author_id,
                           ROLE_LABELS=ROLE_LABELS)


@app.route('/diary/new', methods=['GET', 'POST'])
@login_required
def diary_new():
    user = get_current_user()
    if request.method == 'POST':
        entry = DiaryEntry(
            user_id=user.id,
            date=datetime.strptime(request.form['date'], '%Y-%m-%d').date(),
            visitors=request.form.get('visitors', ''),
            meetings=request.form.get('meetings', ''),
            business_content=request.form.get('business_content', ''),
        )
        db.session.add(entry)
        db.session.flush()

        chambers = request.form.getlist('chamber_name[]')
        staff_names = request.form.getlist('staff_name[]')
        categories = request.form.getlist('guidance_category[]')
        items = request.form.getlist('guidance_item[]')
        contents = request.form.getlist('guidance_content[]')

        for i, chamber in enumerate(chambers):
            if chamber.strip():
                db.session.add(GuidanceRecord(
                    diary_entry_id=entry.id,
                    chamber_name=chamber,
                    staff_name=staff_names[i] if i < len(staff_names) else '',
                    guidance_category=categories[i] if i < len(categories) else '',
                    guidance_item=items[i] if i < len(items) else '',
                    guidance_content=contents[i] if i < len(contents) else '',
                ))
        db.session.commit()
        return redirect(url_for('diary_detail', entry_id=entry.id))

    return render_template('diary_form.html',
                           user=user,
                           entry=None,
                           today=datetime.now().strftime('%Y-%m-%d'),
                           CHAMBER_NAMES=CHAMBER_NAMES,
                           GUIDANCE_CATEGORIES=GUIDANCE_CATEGORIES)


@app.route('/diary/<int:entry_id>/edit', methods=['GET', 'POST'])
@login_required
def diary_edit(entry_id):
    user = get_current_user()
    entry = db.get_or_404(DiaryEntry, entry_id)

    if entry.user_id != user.id:
        return render_template('error.html', message='この日誌を編集する権限がありません。'), 403
    if entry.is_fully_approved():
        return render_template('error.html', message='承認済みのため編集できません。'), 403

    if request.method == 'POST':
        entry.date = datetime.strptime(request.form['date'], '%Y-%m-%d').date()
        entry.visitors = request.form.get('visitors', '')
        entry.meetings = request.form.get('meetings', '')
        entry.business_content = request.form.get('business_content', '')
        entry.updated_at = datetime.now()

        GuidanceRecord.query.filter_by(diary_entry_id=entry.id).delete()
        db.session.flush()

        chambers = request.form.getlist('chamber_name[]')
        staff_names = request.form.getlist('staff_name[]')
        categories = request.form.getlist('guidance_category[]')
        items = request.form.getlist('guidance_item[]')
        contents = request.form.getlist('guidance_content[]')

        for i, chamber in enumerate(chambers):
            if chamber.strip():
                db.session.add(GuidanceRecord(
                    diary_entry_id=entry.id,
                    chamber_name=chamber,
                    staff_name=staff_names[i] if i < len(staff_names) else '',
                    guidance_category=categories[i] if i < len(categories) else '',
                    guidance_item=items[i] if i < len(items) else '',
                    guidance_content=contents[i] if i < len(contents) else '',
                ))
        db.session.commit()
        return redirect(url_for('diary_detail', entry_id=entry.id))

    return render_template('diary_form.html',
                           user=user,
                           entry=entry,
                           today=entry.date.strftime('%Y-%m-%d'),
                           CHAMBER_NAMES=CHAMBER_NAMES,
                           GUIDANCE_CATEGORIES=GUIDANCE_CATEGORIES)


@app.route('/diary/<int:entry_id>')
@login_required
def diary_detail(entry_id):
    user = get_current_user()
    entry = db.get_or_404(DiaryEntry, entry_id)

    if not user.can_view_entry(entry):
        return render_template('error.html', message='この日誌を閲覧する権限がありません。'), 403

    return render_template('diary_detail.html',
                           user=user,
                           entry=entry,
                           approval_status=entry.get_approval_status(),
                           required_approvers=entry.get_required_approvers(),
                           can_approve=entry.can_be_approved_by(user),
                           ROLE_LABELS=ROLE_LABELS)


@app.route('/diary/<int:entry_id>/approve', methods=['POST'])
@login_required
def diary_approve(entry_id):
    user = get_current_user()
    entry = db.get_or_404(DiaryEntry, entry_id)

    if not entry.can_be_approved_by(user):
        return render_template('error.html', message='承認権限がありません。'), 403

    db.session.add(Approval(
        diary_entry_id=entry.id,
        approver_id=user.id,
        approver_role=user.role,
        comment=request.form.get('comment', ''),
        approved_at=datetime.now(),
    ))
    db.session.commit()
    return redirect(url_for('diary_detail', entry_id=entry.id))


@app.route('/diary/<int:entry_id>/print')
@login_required
def diary_print(entry_id):
    user = get_current_user()
    entry = db.get_or_404(DiaryEntry, entry_id)
    if not user.can_view_entry(entry):
        return render_template('error.html', message='権限がありません。'), 403
    return render_template('diary_print.html',
                           entry=entry,
                           approval_status=entry.get_approval_status(),
                           required_approvers=entry.get_required_approvers(),
                           ROLE_LABELS=ROLE_LABELS)


@app.route('/guidance_record')
@login_required
def guidance_record():
    user = get_current_user()
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')
    chamber = request.args.get('chamber', '')
    author_id = request.args.get('author_id', '')

    query = GuidanceRecord.query.join(DiaryEntry)
    if user.role in ('admin', 'senmu', 'jimukyokucho'):
        if author_id:
            query = query.filter(DiaryEntry.user_id == int(author_id))
    elif user.role == 'kacho':
        dept_ids = [u.id for u in User.query.filter_by(department=user.department).all()]
        query = query.filter(DiaryEntry.user_id.in_(dept_ids))
    else:
        query = query.filter(DiaryEntry.user_id == user.id)

    if date_from:
        query = query.filter(DiaryEntry.date >= datetime.strptime(date_from, '%Y-%m-%d').date())
    if date_to:
        query = query.filter(DiaryEntry.date <= datetime.strptime(date_to, '%Y-%m-%d').date())
    if chamber:
        query = query.filter(GuidanceRecord.chamber_name == chamber)

    records = query.order_by(DiaryEntry.date).all()
    if user.role in ('admin', 'senmu', 'jimukyokucho'):
        all_users = User.query.order_by(User.full_name).all()
    elif user.role == 'kacho':
        all_users = User.query.filter_by(department=user.department).order_by(User.full_name).all()
    else:
        all_users = []

    return render_template('guidance_record.html',
                           user=user,
                           records=records,
                           CHAMBER_NAMES=CHAMBER_NAMES,
                           all_users=all_users,
                           date_from=date_from,
                           date_to=date_to,
                           chamber=chamber,
                           author_id=author_id)


@app.route('/export/diary_csv')
@login_required
def export_diary_csv():
    user = get_current_user()
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')

    query = DiaryEntry.query
    if user.role in ('admin', 'senmu', 'jimukyokucho'):
        pass
    elif user.role == 'kacho':
        dept_ids = [u.id for u in User.query.filter_by(department=user.department).all()]
        query = query.filter(DiaryEntry.user_id.in_(dept_ids))
    else:
        query = query.filter_by(user_id=user.id)
    if date_from:
        query = query.filter(DiaryEntry.date >= datetime.strptime(date_from, '%Y-%m-%d').date())
    if date_to:
        query = query.filter(DiaryEntry.date <= datetime.strptime(date_to, '%Y-%m-%d').date())

    entries = query.order_by(DiaryEntry.date).all()
    output = io.StringIO()
    w = csv.writer(output)
    w.writerow(['日付', '氏名', '役職', '来訪者', '会議', '業務内容', '承認状況'])

    for e in entries:
        status = '承認済' if e.is_fully_approved() else ('承認中' if e.approvals else '未承認')
        w.writerow([
            e.date.strftime('%Y/%m/%d'),
            e.user.full_name,
            e.user.role_label,
            e.visitors,
            e.meetings,
            e.business_content,
            status,
        ])

    resp = make_response(output.getvalue().encode('utf-8-sig'))
    resp.headers['Content-Type'] = 'text/csv; charset=utf-8-sig'
    resp.headers['Content-Disposition'] = 'attachment; filename=gyomu_diary.csv'
    return resp


@app.route('/export/guidance_csv')
@login_required
def export_guidance_csv():
    user = get_current_user()
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')
    chamber = request.args.get('chamber', '')
    author_id = request.args.get('author_id', '')

    query = GuidanceRecord.query.join(DiaryEntry)
    if user.role in ('admin', 'senmu', 'jimukyokucho'):
        if author_id:
            query = query.filter(DiaryEntry.user_id == int(author_id))
    elif user.role == 'kacho':
        dept_ids = [u.id for u in User.query.filter_by(department=user.department).all()]
        query = query.filter(DiaryEntry.user_id.in_(dept_ids))
    else:
        query = query.filter(DiaryEntry.user_id == user.id)
    if date_from:
        query = query.filter(DiaryEntry.date >= datetime.strptime(date_from, '%Y-%m-%d').date())
    if date_to:
        query = query.filter(DiaryEntry.date <= datetime.strptime(date_to, '%Y-%m-%d').date())
    if chamber:
        query = query.filter(GuidanceRecord.chamber_name == chamber)

    records = query.order_by(DiaryEntry.date).all()
    output = io.StringIO()
    w = csv.writer(output)
    w.writerow(['日付', '担当者', '商工会名', '在籍職員名', '指導区分', '指導項目', '指導内容'])

    for r in records:
        w.writerow([
            r.diary_entry.date.strftime('%Y/%m/%d'),
            r.diary_entry.user.full_name,
            r.chamber_name,
            r.staff_name,
            r.guidance_category,
            r.guidance_item,
            r.guidance_content,
        ])

    resp = make_response(output.getvalue().encode('utf-8-sig'))
    resp.headers['Content-Type'] = 'text/csv; charset=utf-8-sig'
    resp.headers['Content-Disposition'] = 'attachment; filename=shido_kiroku.csv'
    return resp


# ===== 管理画面 =====

@app.route('/admin/users')
@role_required('admin')
def admin_users():
    user = get_current_user()
    users = User.query.order_by(User.full_name).all()
    return render_template('admin_users.html', user=user, users=users,
                           ROLE_LABELS=ROLE_LABELS, DEPARTMENTS=DEPARTMENTS)


@app.route('/admin/users/new', methods=['GET', 'POST'])
@role_required('admin')
def admin_user_new():
    user = get_current_user()
    error = None
    if request.method == 'POST':
        if User.query.filter_by(username=request.form['username']).first():
            error = 'そのユーザー名は既に使用されています'
        else:
            new_user = User(
                username=request.form['username'],
                full_name=request.form['full_name'],
                role=request.form['role'],
                department=request.form.get('department', ''),
            )
            new_user.set_password(request.form['password'])
            db.session.add(new_user)
            db.session.commit()
            return redirect(url_for('admin_users'))
    return render_template('admin_user_form.html',
                           user=user, edit_user=None, error=error,
                           ROLE_LABELS=ROLE_LABELS, DEPARTMENTS=DEPARTMENTS)


@app.route('/admin/users/<int:uid>/edit', methods=['GET', 'POST'])
@role_required('admin')
def admin_user_edit(uid):
    user = get_current_user()
    edit_user = db.get_or_404(User, uid)
    error = None
    if request.method == 'POST':
        edit_user.full_name = request.form['full_name']
        edit_user.role = request.form['role']
        edit_user.department = request.form.get('department', '')
        if request.form.get('password'):
            edit_user.set_password(request.form['password'])
        db.session.commit()
        return redirect(url_for('admin_users'))
    return render_template('admin_user_form.html',
                           user=user, edit_user=edit_user, error=error,
                           ROLE_LABELS=ROLE_LABELS, DEPARTMENTS=DEPARTMENTS)


@app.route('/admin/users/<int:uid>/delete', methods=['POST'])
@role_required('admin')
def admin_user_delete(uid):
    u = db.get_or_404(User, uid)
    if u.id == session['user_id']:
        return render_template('error.html', message='自分自身は削除できません。'), 400
    db.session.delete(u)
    db.session.commit()
    return redirect(url_for('admin_users'))


# ===== コンテキストプロセッサ =====

@app.context_processor
def inject_globals():
    return dict(
        current_user=get_current_user(),
        ROLE_LABELS=ROLE_LABELS,
    )


def init_db():
    db.create_all()
    # SQLite の既存DBにdepartmentカラムがなければ追加（PostgreSQLは不要）
    if 'sqlite' in app.config['SQLALCHEMY_DATABASE_URI']:
        with db.engine.connect() as conn:
            try:
                conn.execute(text('ALTER TABLE user ADD COLUMN department VARCHAR(50) DEFAULT ""'))
                conn.commit()
            except Exception:
                pass
    if User.query.count() == 0:
        admin = User(username='admin', full_name='システム管理者', role='admin')
        admin.set_password('admin123')
        db.session.add(admin)
        db.session.commit()
        print('初期ユーザーを作成しました: admin / admin123')


with app.app_context():
    init_db()


if __name__ == '__main__':
    print('サーバー起動中... http://localhost:5000')
    app.run(debug=False, host='0.0.0.0', port=5000)
