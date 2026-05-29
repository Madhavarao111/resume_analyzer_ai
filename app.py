from flask import Flask, render_template, request, session, redirect, url_for, jsonify
import sqlite3, os, json, re, traceback, hmac, hashlib
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from groq import Groq
from datetime import datetime, date
from functools import wraps

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "resumeai_pro_secret_2024")
UPLOAD_FOLDER = "uploads"
ALLOWED_EXTENSIONS = {"pdf", "docx", "txt"}
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

API_KEY = os.environ.get("GROQ_API_KEY", "")
client = Groq(api_key=API_KEY)

FREE_DAILY_LIMIT = 3
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "admin@resumeai.com")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")
RAZORPAY_KEY_ID = os.environ.get("RAZORPAY_KEY_ID", "")
RAZORPAY_KEY_SECRET = os.environ.get("RAZORPAY_KEY_SECRET", "")
PLAN_AMOUNT = 9900

# ── DATABASE ──────────────────────────────────────────────────────
def get_db():
    db = sqlite3.connect("resumeai_pro.db")
    db.row_factory = sqlite3.Row
    return db

def init_db():
    db = get_db()
    db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            plan TEXT DEFAULT 'free',
            is_active INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS analyses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            filename TEXT,
            overall_score INTEGER,
            ats_score INTEGER,
            skills_score INTEGER,
            experience_score INTEGER,
            content_score INTEGER,
            skills_found TEXT,
            skills_missing TEXT,
            strengths TEXT,
            improvements TEXT,
            quick_wins TEXT,
            keywords TEXT,
            summary TEXT,
            job_fit_reason TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS contact_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT, email TEXT, message TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    db.commit()
    db.close()

init_db()

# ── DECORATORS ────────────────────────────────────────────────────
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("is_admin"):
            return redirect(url_for("index"))
        return f(*args, **kwargs)
    return decorated

# ── HELPERS ───────────────────────────────────────────────────────
def allowed_file(f):
    return "." in f and f.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS

def extract_text(filepath):
    ext = filepath.rsplit(".", 1)[1].lower()
    if ext == "txt":
        with open(filepath, "r", errors="ignore") as f:
            return f.read()
    if ext == "pdf":
        try:
            import pdfplumber
            with pdfplumber.open(filepath) as pdf:
                return "\n".join(p.extract_text() or "" for p in pdf.pages)
        except: return ""
    if ext == "docx":
        try:
            from docx import Document
            return "\n".join(p.text for p in Document(filepath).paragraphs)
        except: return ""
    return ""

def get_today_count(user_id):
    db = get_db()
    today = date.today().isoformat()
    count = db.execute(
        "SELECT COUNT(*) as c FROM analyses WHERE user_id=? AND DATE(created_at)=?",
        (user_id, today)
    ).fetchone()["c"]
    db.close()
    return count

def analyze_with_ai(resume_text, job_description):
    prompt = f"""You are a world-class resume coach, ATS expert, and senior technical recruiter with 15+ years of experience at top companies like Google, Amazon, and Microsoft. Analyze the resume against the job description with extreme precision.

RESUME:
{resume_text[:4000]}

JOB DESCRIPTION:
{job_description[:2500]}

SCORING RULES (be strict and honest):
- overall_score: Weighted average — ATS(25%) + Skills(30%) + Experience(25%) + Content(20%)
- ats_score: keyword density, formatting, section headers
- skills_score: % of required skills present
- experience_score: years, domain match
- content_score: quantified achievements, action verbs, no filler

Return ONLY raw JSON, no markdown, no code fences, start directly with {{:
{{
  "overall_score": 68,
  "ats_score": 60,
  "skills_score": 72,
  "experience_score": 70,
  "content_score": 65,
  "skills_found": ["Python", "Flask", "SQL"],
  "skills_missing": ["Docker", "AWS", "React"],
  "strengths": ["Strong technical background", "Good project experience", "Clear education section", "Relevant tech stack", "Well structured resume"],
  "improvements": [
    {{"title": "Add missing ATS keywords", "category": "KEYWORDS", "description": "Add Docker, AWS, React — these appear in JD and ATS will filter without them.", "priority": "high"}},
    {{"title": "Quantify every achievement", "category": "IMPACT", "description": "Replace vague statements with metrics: Improved API speed by 40%.", "priority": "high"}},
    {{"title": "Add professional summary", "category": "SUMMARY", "description": "Missing 3-4 line summary at top. First thing recruiters read.", "priority": "high"}},
    {{"title": "Replace weak action verbs", "category": "BREVITY", "description": "Replace worked on, helped with: Built, Engineered, Deployed, Optimized.", "priority": "medium"}},
    {{"title": "Fix inconsistent dates", "category": "STYLE", "description": "Use consistent format: Jan 2023 - Dec 2023 throughout.", "priority": "medium"}},
    {{"title": "Add GitHub/portfolio links", "category": "FORMAT", "description": "Add clickable GitHub profile at top. Recruiters always check.", "priority": "medium"}},
    {{"title": "Tailor experience to JD", "category": "EXPERIENCE", "description": "Reorder bullets so most relevant experience appears first.", "priority": "high"}},
    {{"title": "Remove filler phrases", "category": "BREVITY", "description": "Remove team player, good communication — cliches that waste space.", "priority": "low"}}
  ],
  "quick_wins": [
    "Add Docker and AWS to skills section right now",
    "Write a 3-line professional summary and add to top",
    "Change responsible for building to Built in every bullet",
    "Add your GitHub link next to your email",
    "Make all dates consistent: Month YYYY format"
  ],
  "keywords": ["Docker", "AWS", "TypeScript", "CI/CD", "Microservices", "REST API", "Agile", "Unit Testing"],
  "job_fit_reason": "Strong Python foundation but missing cloud skills listed as required. Upskilling in Docker and AWS would make this very competitive.",
  "summary": "Your resume shows solid technical skills but critically misses several required keywords that ATS systems filter on. Adding a professional summary, quantifying achievements, and including missing keywords could push your score from 68 to 85+."
}}
Replace ALL values with real analysis of the actual resume and job description provided."""

    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=2500,
        temperature=0.3
    )
    raw = response.choices[0].message.content.strip()
    raw = re.sub(r"```json\s*", "", raw)
    raw = re.sub(r"```\s*", "", raw)
    raw = raw.strip()
    start = raw.find("{")
    end = raw.rfind("}") + 1
    if start != -1 and end > start:
        raw = raw[start:end]
    return json.loads(raw)

def score_color(score):
    if score >= 80: return "green"
    if score >= 60: return "orange"
    return "red"

def score_label(score):
    if score >= 85: return "Excellent"
    if score >= 70: return "Good"
    if score >= 50: return "Average"
    return "Needs Work"

app.jinja_env.globals.update(score_color=score_color, score_label=score_label)

# ── PUBLIC ROUTES ─────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/pricing")
def pricing():
    return render_template("pricing.html")

@app.route("/contact", methods=["GET", "POST"])
def contact():
    success = False
    if request.method == "POST":
        name = request.form.get("name","").strip()
        email = request.form.get("email","").strip()
        message = request.form.get("message","").strip()
        if name and email and message:
            db = get_db()
            db.execute("INSERT INTO contact_messages (name,email,message) VALUES (?,?,?)", (name,email,message))
            db.commit()
            db.close()
            success = True
    return render_template("contact.html", success=success)

@app.route("/register", methods=["GET", "POST"])
def register():
    error = None
    if request.method == "POST":
        name = request.form.get("name","").strip()
        email = request.form.get("email","").strip()
        password = request.form.get("password","").strip()
        if not name or not email or not password:
            error = "All fields are required."
        elif len(password) < 6:
            error = "Password must be at least 6 characters."
        else:
            db = get_db()
            try:
                db.execute("INSERT INTO users (name,email,password) VALUES (?,?,?)",
                           (name, email, generate_password_hash(password)))
                db.commit()
                user = db.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
                session["user_id"] = user["id"]
                session["user_name"] = user["name"]
                session["user_plan"] = user["plan"]
                return redirect(url_for("dashboard"))
            except sqlite3.IntegrityError:
                error = "Email already registered."
            finally:
                db.close()
    return render_template("register.html", error=error)

@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        email = request.form.get("email","").strip()
        password = request.form.get("password","").strip()
        if email == ADMIN_EMAIL and password == ADMIN_PASSWORD:
            session["is_admin"] = True
            session["user_name"] = "Admin"
            return redirect(url_for("admin_dashboard"))
        db = get_db()
        user = db.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        db.close()
        if user and check_password_hash(user["password"], password):
            if not user["is_active"]:
                error = "Your account has been deactivated. Contact support."
            else:
                session["user_id"] = user["id"]
                session["user_name"] = user["name"]
                session["user_plan"] = user["plan"]
                return redirect(url_for("dashboard"))
        else:
            error = "Invalid email or password."
    return render_template("login.html", error=error)

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))

# ── USER ROUTES ───────────────────────────────────────────────────
@app.route("/dashboard")
@login_required
def dashboard():
    db = get_db()
    analyses = db.execute(
        "SELECT * FROM analyses WHERE user_id=? ORDER BY created_at DESC LIMIT 5",
        (session["user_id"],)
    ).fetchall()
    total = db.execute("SELECT COUNT(*) as c FROM analyses WHERE user_id=?", (session["user_id"],)).fetchone()["c"]
    avg = db.execute("SELECT AVG(overall_score) as a FROM analyses WHERE user_id=?", (session["user_id"],)).fetchone()["a"]
    best = db.execute("SELECT MAX(overall_score) as b FROM analyses WHERE user_id=?", (session["user_id"],)).fetchone()["b"]
    db.close()
    today_count = get_today_count(session["user_id"])
    recent = [{"id": a["id"], "filename": a["filename"],
               "overall_score": a["overall_score"], "created_at": a["created_at"],
               "summary": a["summary"]} for a in analyses]
    return render_template("dashboard.html",
        user_name=session["user_name"],
        user_plan=session.get("user_plan","free"),
        recent=recent, total=total,
        avg=round(avg) if avg else 0,
        best=best or 0,
        today_count=today_count,
        free_limit=FREE_DAILY_LIMIT)

@app.route("/analyze", methods=["GET", "POST"])
@login_required
def analyze():
    error = None
    if session.get("user_plan","free") == "free":
        today_count = get_today_count(session["user_id"])
        if today_count >= FREE_DAILY_LIMIT:
            return render_template("analyze.html", error=None, limit_reached=True,
                                   free_limit=FREE_DAILY_LIMIT)
    if request.method == "POST":
        job_desc = request.form.get("job_description","").strip()
        resume_text = request.form.get("resume_text","").strip()
        filename = "pasted_text"
        if "resume_file" in request.files:
            f = request.files["resume_file"]
            if f and f.filename and allowed_file(f.filename):
                filename = secure_filename(f.filename)
                path = os.path.join(app.config["UPLOAD_FOLDER"], filename)
                f.save(path)
                extracted = extract_text(path)
                if extracted.strip():
                    resume_text = extracted
        if not resume_text:
            error = "Please upload a resume or paste resume text."
        elif not job_desc:
            error = "Please provide a job description."
        else:
            try:
                data = analyze_with_ai(resume_text, job_desc)
                db = get_db()
                cur = db.execute("""
                    INSERT INTO analyses
                    (user_id, filename, overall_score, ats_score, skills_score,
                     experience_score, content_score, skills_found, skills_missing,
                     strengths, improvements, quick_wins, keywords, summary, job_fit_reason)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, (
                    session["user_id"], filename,
                    data.get("overall_score",0), data.get("ats_score",0),
                    data.get("skills_score",0), data.get("experience_score",0),
                    data.get("content_score",0),
                    json.dumps(data.get("skills_found",[])),
                    json.dumps(data.get("skills_missing",[])),
                    json.dumps(data.get("strengths",[])),
                    json.dumps(data.get("improvements",[])),
                    json.dumps(data.get("quick_wins",[])),
                    json.dumps(data.get("keywords",[])),
                    data.get("summary",""),
                    data.get("job_fit_reason","")
                ))
                db.commit()
                analysis_id = cur.lastrowid
                db.close()
                return redirect(url_for("result", analysis_id=analysis_id))
            except json.JSONDecodeError:
                error = "AI response error. Please try again."
            except Exception as e:
                print(traceback.format_exc())
                error = f"Error: {str(e)}"
    return render_template("analyze.html", error=error, limit_reached=False,
                           free_limit=FREE_DAILY_LIMIT)

@app.route("/result/<int:analysis_id>")
@login_required
def result(analysis_id):
    db = get_db()
    a = db.execute("SELECT * FROM analyses WHERE id=? AND user_id=?",
                   (analysis_id, session["user_id"])).fetchone()
    db.close()
    if not a:
        return redirect(url_for("dashboard"))
    data = {
        "id": a["id"], "filename": a["filename"],
        "overall_score": a["overall_score"], "ats_score": a["ats_score"],
        "skills_score": a["skills_score"], "experience_score": a["experience_score"],
        "content_score": a["content_score"], "summary": a["summary"],
        "job_fit_reason": a["job_fit_reason"] if a["job_fit_reason"] else "",
        "created_at": a["created_at"],
        "skills_found": json.loads(a["skills_found"] or "[]"),
        "skills_missing": json.loads(a["skills_missing"] or "[]"),
        "strengths": json.loads(a["strengths"] or "[]"),
        "improvements": json.loads(a["improvements"] or "[]"),
        "quick_wins": json.loads(a["quick_wins"] or "[]"),
        "keywords": json.loads(a["keywords"] or "[]"),
    }
    return render_template("result.html", data=data, user_name=session["user_name"],
                           user_plan=session.get("user_plan","free"))

@app.route("/history")
@login_required
def history():
    db = get_db()
    analyses = db.execute(
        "SELECT * FROM analyses WHERE user_id=? ORDER BY created_at DESC",
        (session["user_id"],)
    ).fetchall()
    db.close()
    rows = [{"id": a["id"], "filename": a["filename"],
             "overall_score": a["overall_score"], "created_at": a["created_at"],
             "summary": a["summary"]} for a in analyses]
    return render_template("history.html", analyses=rows, user_name=session["user_name"],
                           user_plan=session.get("user_plan","free"))

# ── PAYMENT ROUTES ────────────────────────────────────────────────
@app.route("/upgrade")
@login_required
def upgrade():
    if session.get("user_plan") == "pro":
        return redirect(url_for("dashboard"))
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE id=?", (session["user_id"],)).fetchone()
    db.close()
    return render_template("payment.html",
        user_name=session["user_name"],
        user_email=user["email"],
        razorpay_key=RAZORPAY_KEY_ID)

@app.route("/create-order", methods=["POST"])
@login_required
def create_order():
    try:
        import requests as req
        from requests.auth import HTTPBasicAuth
        response = req.post(
            "https://api.razorpay.com/v1/orders",
            json={
                "amount": PLAN_AMOUNT,
                "currency": "INR",
                "payment_capture": 1
            },
            auth=HTTPBasicAuth(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET)
        )
        order = response.json()
        return jsonify({
            "order_id": order["id"],
            "amount": PLAN_AMOUNT,
            "key": RAZORPAY_KEY_ID
        })
    except Exception as e:
        print("Razorpay error:", traceback.format_exc())
        return jsonify({"error": str(e)}), 500

@app.route("/verify-payment", methods=["POST"])
@login_required
def verify_payment():
    try:
        data = request.get_json()
        payment_id = data["razorpay_payment_id"]
        order_id = data["razorpay_order_id"]
        signature = data["razorpay_signature"]

        msg = f"{order_id}|{payment_id}"
        expected = hmac.new(
            RAZORPAY_KEY_SECRET.encode("utf-8"),
            msg.encode("utf-8"),
            hashlib.sha256
        ).hexdigest()

        if expected == signature:
            db = get_db()
            db.execute("UPDATE users SET plan='pro' WHERE id=?", (session["user_id"],))
            db.commit()
            db.close()
            session["user_plan"] = "pro"
            return jsonify({"success": True})
        else:
            return jsonify({"success": False, "error": "Invalid signature"})
    except Exception as e:
        print("Verify error:", traceback.format_exc())
        return jsonify({"success": False, "error": str(e)})

@app.route("/payment-success")
@login_required
def payment_success():
    return render_template("payment_success.html", user_name=session["user_name"])

# ── ADMIN ROUTES ──────────────────────────────────────────────────
@app.route("/admin")
@admin_required
def admin_dashboard():
    db = get_db()
    total_users = db.execute("SELECT COUNT(*) as c FROM users").fetchone()["c"]
    total_analyses = db.execute("SELECT COUNT(*) as c FROM analyses").fetchone()["c"]
    pro_users = db.execute("SELECT COUNT(*) as c FROM users WHERE plan='pro'").fetchone()["c"]
    today = date.today().isoformat()
    today_analyses = db.execute("SELECT COUNT(*) as c FROM analyses WHERE DATE(created_at)=?", (today,)).fetchone()["c"]
    avg_score = db.execute("SELECT AVG(overall_score) as a FROM analyses").fetchone()["a"]
    recent_users = db.execute("SELECT * FROM users ORDER BY created_at DESC LIMIT 10").fetchall()
    recent_analyses = db.execute("""
        SELECT a.*, u.name as user_name, u.email
        FROM analyses a JOIN users u ON a.user_id = u.id
        ORDER BY a.created_at DESC LIMIT 10
    """).fetchall()
    score_dist = {"excellent": 0, "good": 0, "average": 0, "poor": 0}
    all_scores = db.execute("SELECT overall_score FROM analyses").fetchall()
    for s in all_scores:
        sc = s["overall_score"]
        if sc >= 85: score_dist["excellent"] += 1
        elif sc >= 70: score_dist["good"] += 1
        elif sc >= 50: score_dist["average"] += 1
        else: score_dist["poor"] += 1
    messages = db.execute("SELECT * FROM contact_messages ORDER BY created_at DESC LIMIT 5").fetchall()
    db.close()
    return render_template("admin/dashboard.html",
        total_users=total_users, total_analyses=total_analyses,
        pro_users=pro_users, today_analyses=today_analyses,
        avg_score=round(avg_score) if avg_score else 0,
        recent_users=recent_users, recent_analyses=recent_analyses,
        score_dist=score_dist, messages=messages)

@app.route("/admin/users")
@admin_required
def admin_users():
    db = get_db()
    users = db.execute("SELECT * FROM users ORDER BY created_at DESC").fetchall()
    db.close()
    return render_template("admin/users.html", users=users)

@app.route("/admin/user/<int:user_id>/toggle", methods=["POST"])
@admin_required
def admin_toggle_user(user_id):
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    new_status = 0 if user["is_active"] else 1
    db.execute("UPDATE users SET is_active=? WHERE id=?", (new_status, user_id))
    db.commit()
    db.close()
    return redirect(url_for("admin_users"))

@app.route("/admin/user/<int:user_id>/upgrade", methods=["POST"])
@admin_required
def admin_upgrade_user(user_id):
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    new_plan = "free" if user["plan"] == "pro" else "pro"
    db.execute("UPDATE users SET plan=? WHERE id=?", (new_plan, user_id))
    db.commit()
    db.close()
    return redirect(url_for("admin_users"))

@app.route("/admin/user/<int:user_id>/delete", methods=["POST"])
@admin_required
def admin_delete_user(user_id):
    db = get_db()
    db.execute("DELETE FROM analyses WHERE user_id=?", (user_id,))
    db.execute("DELETE FROM users WHERE id=?", (user_id,))
    db.commit()
    db.close()
    return redirect(url_for("admin_users"))

@app.route("/admin/analyses")
@admin_required
def admin_analyses():
    db = get_db()
    analyses = db.execute("""
        SELECT a.*, u.name as user_name, u.email
        FROM analyses a JOIN users u ON a.user_id = u.id
        ORDER BY a.created_at DESC
    """).fetchall()
    db.close()
    return render_template("admin/analyses.html", analyses=analyses)

@app.route("/admin/messages")
@admin_required
def admin_messages():
    db = get_db()
    messages = db.execute("SELECT * FROM contact_messages ORDER BY created_at DESC").fetchall()
    db.close()
    return render_template("admin/messages.html", messages=messages)

if __name__ == "__main__":
    app.run(debug=True)
