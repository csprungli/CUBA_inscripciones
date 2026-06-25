from flask import Flask, render_template, request, redirect, url_for, jsonify, send_from_directory
import mercadopago
from flask import session, flash
import os
import logging
import urllib.parse
import json
import tempfile
import uuid
from werkzeug.utils import secure_filename
from dotenv import load_dotenv
import re

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-key")
app.logger.setLevel(logging.INFO)

BASE_DIR = os.path.dirname(__file__)
DATA_DIR = os.environ.get("DATA_DIR", "/var/data")

try:
    os.makedirs(DATA_DIR, exist_ok=True)
    test_path = os.path.join(DATA_DIR, ".writetest")
    with open(test_path, "w", encoding="utf-8") as _f:
        _f.write("ok")
    os.remove(test_path)
    SETTINGS_DIR = DATA_DIR
except Exception:
    SETTINGS_DIR = BASE_DIR

SETTINGS_PATH = os.path.join(SETTINGS_DIR, "settings.json")
LOGOS_DIR = os.path.join(DATA_DIR, "logos")
os.makedirs(LOGOS_DIR, exist_ok=True)
REPO_SETTINGS_PATH = os.path.join(BASE_DIR, "settings.json")

DEFAULT_GOOGLE_FORMS = {
    "competitors_id": "",
    "trainers_id": "",
    "entry_id_num_operacion": "entry.1161481877",
    "entry_id_clase_barco": "entry.1553765108"
}

DEFAULT_SETTINGS = {
    "cuba_logo": "static/images/Metropolitano.png",
    "site_closed": False,
    "campeonatos": []
}

def make_default_campeonato(name="Metropolitano", camp_id=None):
    return {
        "id": camp_id or str(uuid.uuid4())[:8],
        "name": name,
        "active": True,
        "logo": "static/images/Metropolitano.png",
        "title_main": "Inscripciones",
        "title_strong": name,
        "allow_cash_payments": True,
        "google_forms": dict(DEFAULT_GOOGLE_FORMS),
        "discount_enabled": False,
        "discount_percentage": 0,
        "discount_description": "",
        "camp_prefix": "",
        "classes": []
    }

def _migrate_old_settings(old):
    if "campeonatos" in old:
        return old
    new = dict(DEFAULT_SETTINGS)
    new["cuba_logo"] = old.get("logo", "static/images/Metropolitano.png")
    new["site_closed"] = old.get("site_closed", False)
    camp = make_default_campeonato()
    camp["logo"] = old.get("logo", "static/images/Metropolitano.png")
    camp["title_main"] = old.get("title_main", "Inscripciones")
    camp["title_strong"] = old.get("title_strong", "Metropolitano")
    camp["name"] = old.get("title_strong", "Metropolitano")
    camp["allow_cash_payments"] = old.get("allow_cash_payments", True)
    camp["google_forms"] = old.get("google_forms", dict(DEFAULT_GOOGLE_FORMS))
    camp["discount_enabled"] = old.get("discount_enabled", False)
    camp["discount_percentage"] = old.get("discount_percentage", 0)
    camp["discount_description"] = old.get("discount_description", "")
    camp["camp_prefix"] = old.get("camp_prefix", "")
    camp["classes"] = old.get("classes", [])
    new["campeonatos"] = [camp]
    return new

def _bootstrap_settings_file():
    if not os.path.exists(SETTINGS_PATH):
        src = REPO_SETTINGS_PATH if os.path.exists(REPO_SETTINGS_PATH) else None
        if src:
            import shutil
            shutil.copyfile(src, SETTINGS_PATH)
        else:
            with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
                json.dump(DEFAULT_SETTINGS, f, ensure_ascii=False, indent=2)

_bootstrap_settings_file()

ALLOWED_LOGO_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}
URL_BASE = os.environ.get("URL_BASE", "https://metropolitanopagos-inscripciones.onrender.com")

def extract_form_id(url_or_id):
    match = re.search(r'/d/e/([a-zA-Z0-9_-]+)(?:/viewform|/edit|/formResponse)?', url_or_id)
    if match:
        return match.group(1)
    match = re.search(r'/d/([a-zA-Z0-9_-]+)(?:/viewform|/edit|/formResponse)?', url_or_id)
    if match:
        return match.group(1)
    return url_or_id.strip()

def load_settings():
    try:
        with open(SETTINGS_PATH, 'r', encoding='utf-8') as f:
            raw = json.load(f)
        needs_save = "campeonatos" not in raw
        settings = _migrate_old_settings(raw)
        if needs_save:
            save_settings(settings)
        for camp in settings.get("campeonatos", []):
            camp.setdefault("id", str(uuid.uuid4())[:8])
            camp.setdefault("name", "Campeonato")
            camp.setdefault("active", True)
            camp.setdefault("logo", settings.get("cuba_logo", "static/images/Metropolitano.png"))
            camp.setdefault("title_main", "Inscripciones")
            camp.setdefault("title_strong", camp["name"])
            camp.setdefault("allow_cash_payments", True)
            camp.setdefault("google_forms", dict(DEFAULT_GOOGLE_FORMS))
            camp["google_forms"].setdefault("entry_id_num_operacion", "entry.1161481877")
            camp["google_forms"].setdefault("entry_id_clase_barco", "entry.1553765108")
            camp.setdefault("discount_enabled", False)
            camp.setdefault("discount_percentage", 0)
            camp.setdefault("discount_description", "")
            camp.setdefault("camp_prefix", "")
            camp.setdefault("classes", [])
            for cls in camp["classes"]:
                cls.setdefault("discount_price", None)
        return settings
    except Exception:
        return dict(DEFAULT_SETTINGS)

def save_settings(data):
    dir_ = os.path.dirname(SETTINGS_PATH)
    os.makedirs(dir_, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix="settings.", suffix=".json", dir=dir_)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, SETTINGS_PATH)
    finally:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass

def allowed_logo(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_LOGO_EXTENSIONS

MERCADO_PAGO_ACCESS_TOKEN = os.environ.get("MERCADO_PAGO_ACCESS_TOKEN")
sdk = mercadopago.SDK(MERCADO_PAGO_ACCESS_TOKEN)
app.logger.info(f"DEBBUGING: {MERCADO_PAGO_ACCESS_TOKEN[:10]}")

def get_camp_or_none(settings, camp_id):
    return next((c for c in settings.get("campeonatos", []) if c["id"] == camp_id), None)

# ÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂ Rutas publicas ÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂ


@app.route('/logos/<path:filename>')
def serve_logo(filename):
    return send_from_directory(LOGOS_DIR, filename)

@app.route('/')
def index():
    settings = load_settings()
    if settings.get("site_closed"):
        return render_template('cerrada.html', page_title="Inscripcion cerrada")
    return redirect(url_for('inscripciones'))

@app.route('/inscripciones')
def inscripciones():
    settings = load_settings()
    if settings.get("site_closed"):
        return render_template('cerrada.html', page_title="Inscripcion cerrada")
    activos = [c for c in settings.get("campeonatos", []) if c.get("active", True)]
    if not activos:
        return render_template('cerrada.html', page_title="No hay campeonatos activos")
    if len(activos) == 1:
        return redirect(url_for('inscripcion_campeonato', camp_id=activos[0]['id']))
    return render_template('select_campeonato.html',
                           cuba_logo=settings.get("cuba_logo", "static/images/Metropolitano.png"),
                           campeonatos=activos)

@app.route('/inscripciones/<camp_id>')
def inscripcion_campeonato(camp_id):
    settings = load_settings()
    if settings.get("site_closed"):
        return render_template('cerrada.html', page_title="Inscripcion cerrada")
    camp = get_camp_or_none(settings, camp_id)
    if not camp or not camp.get("active", True):
        return redirect(url_for('inscripciones'))
    classes = camp.get("classes", [])
    sorted_classes = sorted(classes, key=lambda c: c.get("closed", False))
    enabled_classes = [c["name"] for c in sorted_classes if not c.get("closed", False)]
    return render_template('index.html',
        page_title=f"{camp.get('title_main', 'Inscripciones')} {camp.get('title_strong', '')}",
        logo_path=camp.get("logo", "static/images/Metropolitano.png"),
        title_main=camp.get("title_main", "Inscripciones"),
        title_strong=camp.get("title_strong", ""),
        classes=sorted_classes,
        enabled_classes=enabled_classes,
        discount_enabled=camp.get("discount_enabled", False),
        discount_description=camp.get("discount_description", ""),
        camp_id=camp_id,
        campeonato_name=camp.get("name", ""),
        multiple_campeonatos=True
    )

@app.route('/process_inscription', methods=['POST'])
def process_inscription():
    camp_id = request.form.get('camp_id')
    settings = load_settings()
    if settings.get("site_closed"):
        return render_template('cerrada.html', page_title="Inscripcion cerrada"), 403
    camp = get_camp_or_none(settings, camp_id)
    if not camp or not camp.get("active", True):
        return "Campeonato no disponible.", 404
    rol = request.form.get('rol')
    clase_barco = request.form.get('clase_barco')
    apply_discount = request.form.get('apply_discount') == 'on'
    if rol == 'entrenador':
        google_forms_id = camp["google_forms"]["trainers_id"]
        google_forms_url = f"https://docs.google.com/forms/d/e/{google_forms_id}/viewform?usp=pp_url"
        app.logger.info(f"Entrenador -> {google_forms_url}")
        return redirect(google_forms_url)
    elif rol == 'competidor':
        clases_habilitadas = [c["name"] for c in camp.get("classes", []) if not c.get("closed", False)]
        if clase_barco not in clases_habilitadas:
            return "La inscripcion para esta clase esta cerrada.", 403
        class_info = next((c for c in camp.get("classes", []) if c.get("name") == clase_barco), None)
        if class_info is None:
            return "Error: clase no valida.", 400
        is_discounted = (camp.get("discount_enabled") and apply_discount and class_info.get("price") is not None)
        camp_prefix = camp.get("camp_prefix", "").strip()
        clase_label = f"{camp_prefix}_{clase_barco}" if camp_prefix else clase_barco
        if is_discounted:
            original_price = int(class_info["price"])
            discount_pct = int(camp.get("discount_percentage", 0))
            total_price = max(1, round(original_price * (1 - discount_pct / 100), 2))
            item_title = f"Inscripcion Competidor - {clase_label} ({camp.get('discount_description', 'Descuento')})"
        else:
            total_price = max(1, int(class_info["price"]))
            item_title = f"Inscripcion Competidor - {clase_label}"
        encoded_clase_barco = urllib.parse.quote_plus(clase_barco)
        encoded_camp_id = urllib.parse.quote_plus(camp_id)
        excluded_payment_types = []
        if not camp.get("allow_cash_payments", True):
            excluded_payment_types.append({"id": "ticket"})
        preference_data = {
            "items": [{"title": item_title, "quantity": 1, "unit_price": float(total_price), "currency_id": "ARS"}],
            "back_urls": {
                "success": f"{URL_BASE}/payment_success?clase_barco={encoded_clase_barco}&camp_id={encoded_camp_id}",
                "pending": f"{URL_BASE}/payment_pending?clase_barco={encoded_clase_barco}&camp_id={encoded_camp_id}",
                "failure": f"{URL_BASE}/payment_failure?clase_barco={encoded_clase_barco}&camp_id={encoded_camp_id}",
            },
            "auto_return": "approved",
            "external_reference": f"{clase_label}_{camp_id}",
            "payment_methods": {"excluded_payment_types": excluded_payment_types}
        }
        try:
            resp = sdk.preference().create(preference_data)
            pref = resp["response"]
            if resp["status"] == 201:
                app.logger.info(f"Preferencia creada -> {pref['init_point']}")
                return redirect(pref["init_point"])
            else:
                app.logger.error(f"Error MP: {resp['status']}")
                return "Hubo un error al procesar el pago. Intenta de nuevo."
        except Exception as e:
            app.logger.error(f"Excepcion MP: {e}")
            return "Error inesperado al procesar tu solicitud."
    return "Error: Rol no valido.", 400

@app.route('/payment_success')
def payment_success():
    payment_id = request.args.get('payment_id')
    clase_barco = request.args.get('clase_barco')
    camp_id = request.args.get('camp_id')
    settings = load_settings()
    camp = get_camp_or_none(settings, camp_id) if camp_id else None
    if camp:
        gf = camp.get("google_forms", {})
        google_forms_id = gf.get("competitors_id", "")
        entry_id_num_op = gf.get("entry_id_num_operacion", "entry.1161481877")
        entry_id_clase = gf.get("entry_id_clase_barco", "entry.1553765108")
    else:
        google_forms_id = ""
        entry_id_num_op = "entry.1161481877"
        entry_id_clase = "entry.1553765108"
    google_forms_url = f"https://docs.google.com/forms/d/e/{google_forms_id}/viewform?usp=pp_url&{entry_id_num_op}={payment_id}"
    if clase_barco:
        google_forms_url += f"&{entry_id_clase}={urllib.parse.quote_plus(clase_barco)}"
    return render_template('success.html',
        message="Tu pago fue procesado con exito! Por favor, completa el formulario.",
        payment_id=payment_id,
        google_forms_url=google_forms_url)

@app.route('/payment_pending')
def payment_pending():
    return render_template('payment_status.html', status="pendiente",
        message="Tu pago esta pendiente de aprobacion.")

@app.route('/payment_failure')
def payment_failure():
    return render_template('payment_status.html', status="fallido",
        message="Tu pago no pudo ser procesado. Verifica tus datos o intenta con otro medio.")

@app.route('/mercadopago-webhook', methods=['POST'])
def mercadopago_webhook():
    data = request.json
    topic = data.get('topic')
    resource_id = data.get('id')
    app.logger.info(f"Webhook: topic={topic}, id={resource_id}")
    if topic == 'payment':
        try:
            payment_info = sdk.payment().get(resource_id)
            if payment_info and payment_info["status"] == 200:
                payment = payment_info["response"]
                app.logger.info(f"Pago {payment['id']} - estado: {payment['status']}")
        except Exception as e:
            app.logger.error(f"Error webhook: {e}")
    return jsonify({"status": "ok"}), 200

@app.before_request
def site_closed_gate():
    try:
        if request.path.startswith('/admin') or request.path.startswith('/static') or request.path == '/':
            return
        settings = load_settings()
        if settings.get('site_closed'):
            return render_template('cerrada.html', page_title="Inscripcion cerrada")
    except Exception:
        pass

# ÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂ Admin ÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂ

@app.route('/admin', methods=['GET'])
def admin_home():
    if not session.get('is_admin'):
        return render_template('admin_login.html')
    settings = load_settings()
    return render_template('admin.html', settings=settings, current_url_base=URL_BASE)

@app.route('/admin/login', methods=['POST'])
def admin_login():
    password = request.form.get('password', '')
    expected = os.environ.get('ADMIN_PASSWORD')
    if expected and password == expected:
        session['is_admin'] = True
        return redirect(url_for('admin_home'))
    flash('Contrasena incorrecta', 'danger')
    return redirect(url_for('admin_home'))

@app.route('/admin/logout', methods=['POST'])
def admin_logout():
    session.clear()
    return redirect(url_for('admin_home'))

@app.route('/admin/site_state', methods=['POST'])
def admin_site_state():
    if not session.get('is_admin'):
        return redirect(url_for('admin_home'))
    action = request.form.get('action')
    settings = load_settings()
    settings['site_closed'] = (action == 'close')
    flash('Inscripciones cerradas' if settings['site_closed'] else 'Inscripciones abiertas',
          'warning' if settings['site_closed'] else 'success')
    save_settings(settings)
    return redirect(url_for('admin_home'))

@app.route('/admin/cuba_logo', methods=['POST'])
def admin_save_cuba_logo():
    if not session.get('is_admin'):
        return redirect(url_for('admin_home'))
    settings = load_settings()
    if 'cuba_logo' in request.files:
        file = request.files['cuba_logo']
        if file and file.filename and allowed_logo(file.filename):
            filename = secure_filename(file.filename)
            save_name = f"cuba_logo_{filename}"
            file.save(os.path.join(LOGOS_DIR, save_name))
            settings['cuba_logo'] = f"logos/{save_name}"
            save_settings(settings)
            flash('Logo de CUBA actualizado', 'success')
        else:
            flash('Formato de imagen no permitido', 'warning')
    return redirect(url_for('admin_home'))

# ÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂ Admin - Campeonatos CRUD ÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂ

@app.route('/admin/campeonato/new', methods=['POST'])
def admin_new_campeonato():
    if not session.get('is_admin'):
        return redirect(url_for('admin_home'))
    settings = load_settings()
    name = request.form.get('name', 'Nuevo Campeonato').strip() or 'Nuevo Campeonato'
    camp = make_default_campeonato(name)
    settings.setdefault("campeonatos", []).append(camp)
    save_settings(settings)
    flash('Campeonato creado', 'success')
    return redirect(url_for('admin_edit_campeonato', camp_id=camp['id']))

@app.route('/admin/campeonato/<camp_id>', methods=['GET'])
def admin_edit_campeonato(camp_id):
    if not session.get('is_admin'):
        return redirect(url_for('admin_home'))
    settings = load_settings()
    camp = get_camp_or_none(settings, camp_id)
    if not camp:
        flash('Campeonato no encontrado', 'danger')
        return redirect(url_for('admin_home'))
    return render_template('admin_campeonato.html', camp=camp, settings=settings)

@app.route('/admin/campeonato/<camp_id>/save', methods=['POST'])
def admin_save_campeonato(camp_id):
    if not session.get('is_admin'):
        return redirect(url_for('admin_home'))
    settings = load_settings()
    camp = get_camp_or_none(settings, camp_id)
    if not camp:
        flash('Campeonato no encontrado', 'danger')
        return redirect(url_for('admin_home'))
    delete_idx = request.form.get('delete')
    if delete_idx is not None:
        try:
            di = int(delete_idx)
            classes = camp.get('classes', [])
            if 0 <= di < len(classes):
                classes.pop(di)
                camp['classes'] = classes
                save_settings(settings)
                flash('Clase eliminada', 'success')
        except Exception:
            flash('No se pudo eliminar la clase', 'danger')
        return redirect(url_for('admin_edit_campeonato', camp_id=camp_id))
    camp['name'] = request.form.get('name', camp['name']).strip()
    camp['title_main'] = request.form.get('title_main', camp.get('title_main', 'Inscripciones')).strip()
    camp['title_strong'] = request.form.get('title_strong', camp.get('title_strong', '')).strip()
    camp['allow_cash_payments'] = request.form.get('allow_cash_payments') == 'on'
    camp['discount_enabled'] = request.form.get('discount_enabled') == 'on'
    camp['discount_description'] = request.form.get('discount_description', '').strip()
    try:
        camp['discount_percentage'] = int(request.form.get('discount_percentage', 0))
    except ValueError:
        flash('Porcentaje de descuento invalido', 'danger')
        return redirect(url_for('admin_edit_campeonato', camp_id=camp_id))
    gf = camp.setdefault('google_forms', {})
    gf['competitors_id'] = extract_form_id(request.form.get('google_forms_competitors_id', gf.get('competitors_id', '')).strip())
    gf['trainers_id'] = extract_form_id(request.form.get('google_forms_trainers_id', gf.get('trainers_id', '')).strip())
    gf['entry_id_num_operacion'] = request.form.get('entry_id_num_operacion', gf.get('entry_id_num_operacion', 'entry.1161481877')).strip()
    gf['entry_id_clase_barco'] = request.form.get('entry_id_clase_barco', gf.get('entry_id_clase_barco', 'entry.1553765108')).strip()
    camp['camp_prefix'] = request.form.get('camp_prefix', camp.get('camp_prefix', ''))[:10].strip()
    updated_classes = []
    for idx, cls in enumerate(camp.get('classes', [])):
        open_checked = request.form.get(f'open-{idx}') == 'on'
        name_cls = request.form.get(f'name-{idx}', cls.get('name', '')).strip()
        price_val = request.form.get(f'price-{idx}')
        try:
            price = int(price_val) if price_val else cls.get('price')
        except Exception:
            price = cls.get('price')
        if name_cls:
            updated_classes.append({"name": name_cls, "closed": not open_checked, "price": price, "discount_price": None})
    new_class = request.form.get('new_class', '').strip()
    if new_class:
        names_lower = {c['name'].lower() for c in updated_classes}
        if new_class.lower() not in names_lower:
            new_price_val = request.form.get('new_class_price')
            try:
                new_price = int(new_price_val) if new_price_val else None
            except Exception:
                new_price = None
            updated_classes.append({"name": new_class, "closed": False, "price": new_price, "discount_price": None})
    camp['classes'] = updated_classes
    if 'logo' in request.files:
        file = request.files['logo']
        if file and file.filename and allowed_logo(file.filename):
            filename = secure_filename(file.filename)
            save_name = f"camp_{camp_id}_{filename}"
            file.save(os.path.join(LOGOS_DIR, save_name))
            camp['logo'] = f"logos/{save_name}"
        elif file and file.filename:
            flash('Formato de imagen no permitido', 'warning')
    save_settings(settings)
    flash('Cambios guardados', 'success')
    return redirect(url_for('admin_edit_campeonato', camp_id=camp_id))

@app.route('/admin/campeonato/<camp_id>/toggle', methods=['POST'])
def admin_toggle_campeonato(camp_id):
    if not session.get('is_admin'):
        return redirect(url_for('admin_home'))
    settings = load_settings()
    camp = get_camp_or_none(settings, camp_id)
    if camp:
        camp['active'] = not camp.get('active', True)
        save_settings(settings)
        estado = 'activado' if camp['active'] else 'desactivado'
        flash(f"Campeonato {estado}", 'success' if camp['active'] else 'warning')
    return redirect(url_for('admin_home'))

@app.route('/admin/campeonato/<camp_id>/delete', methods=['POST'])
def admin_delete_campeonato(camp_id):
    if not session.get('is_admin'):
        return redirect(url_for('admin_home'))
    settings = load_settings()
    settings['campeonatos'] = [c for c in settings.get('campeonatos', []) if c['id'] != camp_id]
    save_settings(settings)
    flash('Campeonato eliminado', 'success')
    return redirect(url_for('admin_home'))

if __name__ == '__main__':
    app.run(debug=False, port=5000)
