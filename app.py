import os
import json
from datetime import datetime, timezone, timedelta

ARG = timezone(timedelta(hours=-3))
from flask import Flask, request, jsonify, render_template, send_file, redirect, url_for, session
from functools import wraps

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'clave_secreta_cambiar')

# ── Configuración ──────────────────────────────────────────
WEB_USER     = os.environ.get('WEB_USER', 'admin')
WEB_PASSWORD = os.environ.get('WEB_PASSWORD', 'admin123')

# Carpeta donde se guardan los archivos de cada reloj
UPLOAD_FOLDER = 'archivos'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# Base de datos en memoria (se reinicia al reiniciar el servidor)
# Para producción se puede usar PostgreSQL
relojes = {}  # { device_id: { nombre, ultima_actualizacion, archivo } }

# Cargar estado desde archivo JSON si existe
STATE_FILE = 'state.json'

def load_state():
    global relojes
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, 'r') as f:
                relojes = json.load(f)
        except Exception:
            relojes = {}

def save_state():
    try:
        with open(STATE_FILE, 'w') as f:
            json.dump(relojes, f)
    except Exception as e:
        print(f"Error guardando estado: {e}")

load_state()

# ── Login requerido ────────────────────────────────────────
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('logged_in'):
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

# ── Rutas de autenticación ─────────────────────────────────
@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        if (request.form['username'] == WEB_USER and
                request.form['password'] == WEB_PASSWORD):
            session['logged_in'] = True
            return redirect(url_for('index'))
        error = 'Usuario o contraseña incorrectos'
    return render_template('login.html', error=error)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

# ── Panel principal ────────────────────────────────────────
@app.route('/')
@login_required
def index():
    ahora = datetime.now()
    lista = []
    for device_id, info in relojes.items():
        try:
            ultima = datetime.fromisoformat(info['ultima_actualizacion'])
            diff = (ahora - ultima).total_seconds()
            online = diff < 600  # online si actualizó en los últimos 10 minutos
        except Exception:
            online = False
        lista.append({
            'device_id': device_id,
            'nombre': info.get('nombre', device_id),
            'ultima_actualizacion': info.get('ultima_actualizacion', '---'),
            'online': online
        })
    # Ordenar: online primero
    lista.sort(key=lambda x: x['online'], reverse=True)
    return render_template('index.html', relojes=lista)

# ── API: recibir archivo desde el programa Python ──────────
@app.route('/api/upload', methods=['POST'])
def upload():
    device_id = request.form.get('device_id')
    nombre    = request.form.get('nombre', device_id)

    if not device_id:
        return jsonify({'error': 'device_id requerido'}), 400

    if 'archivo' not in request.files:
        return jsonify({'error': 'archivo requerido'}), 400

    archivo = request.files['archivo']
    if archivo.filename == '':
        return jsonify({'error': 'archivo vacío'}), 400

    # Guardar archivo
    filename = f"aramis_GN_{device_id}.dat"
    filepath = os.path.join(UPLOAD_FOLDER, filename)
    archivo.save(filepath)

    # Actualizar estado del reloj
    relojes[device_id] = {
        'nombre': nombre,
        'ultima_actualizacion': datetime.now().isoformat(),
        'archivo': filename
    }
    save_state()

    print(f"[{datetime.now().strftime('%d/%m/%Y %H:%M:%S')}] Archivo recibido de {nombre} ({device_id})")
    return jsonify({'ok': True, 'mensaje': 'Archivo recibido correctamente'}), 200

# ── Descargar archivo de un reloj ──────────────────────────
@app.route('/descargar/<device_id>')
@login_required
def descargar(device_id):
    if device_id not in relojes:
        return 'Reloj no encontrado', 404

    filename = relojes[device_id].get('archivo')
    if not filename:
        return 'Archivo no disponible', 404

    filepath = os.path.join(UPLOAD_FOLDER, filename)
    if not os.path.exists(filepath):
        return 'Archivo no encontrado en el servidor', 404

    return send_file(filepath, as_attachment=True, download_name=f'aramis_GN_{device_id}.dat')

if __name__ == '__main__':
    app.run(debug=False, host='0.0.0.0', port=5000)
