import os
import json
import requests as http_requests
from datetime import datetime, timezone, timedelta
from flask import Flask, request, jsonify, render_template, send_file, redirect, url_for, session, make_response
from functools import wraps

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'clave_secreta_cambiar')

ARG = timezone(timedelta(hours=-3))

WEB_USER     = os.environ.get('WEB_USER', 'admin')
WEB_PASSWORD = os.environ.get('WEB_PASSWORD', 'admin123')

REDIS_URL   = os.environ.get('UPSTASH_REDIS_REST_URL')
REDIS_TOKEN = os.environ.get('UPSTASH_REDIS_REST_TOKEN')

# ── Redis helpers ──────────────────────────────────────────
REDIS_HEADERS = None

def get_headers():
    global REDIS_HEADERS
    if REDIS_HEADERS is None:
        REDIS_HEADERS = {
            "Authorization": f"Bearer {REDIS_TOKEN}",
            "Content-Type": "application/json"
        }
    return REDIS_HEADERS

def redis_cmd(*args):
    """Ejecuta un comando Redis via Upstash REST API."""
    try:
        r = http_requests.post(
            REDIS_URL,
            headers=get_headers(),
            json=list(args),
            timeout=5
        )
        return r.json().get('result')
    except Exception as e:
        print(f"Redis error: {e}")
        return None

def redis_get(key):
    try:
        result = redis_cmd("GET", key)
        if result is None:
            return None
        if isinstance(result, dict):
            return result
        if isinstance(result, str):
            try:
                return json.loads(result)
            except json.JSONDecodeError as je:
                print(f"JSON decode error: {je}, raw: {repr(result[:100])}")
                return None
        return None
    except Exception as e:
        print(f"Redis GET error: {e}")
        return None

def redis_set(key, value):
    try:
        serialized = json.dumps(value)
        result = redis_cmd("SET", key, serialized)
        print(f"redis_set key={key} result={result} len={len(serialized)}")
    except Exception as e:
        print(f"Redis SET error: {e}")

def redis_keys(pattern):
    try:
        result = redis_cmd("KEYS", pattern)
        return result or []
    except Exception as e:
        print(f"Redis KEYS error: {e}")
        return []

def redis_del(key):
    try:
        redis_cmd("DEL", key)
    except Exception as e:
        print(f"Redis DEL error: {e}")

def get_reloj(device_id):
    return redis_get(f"reloj:{device_id}") or {}

def save_reloj(device_id, data):
    redis_set(f"reloj:{device_id}", data)

def get_pendiente(device_id):
    return redis_get(f"pendiente:{device_id}")

def save_pendiente(device_id, contenido):
    redis_set(f"pendiente:{device_id}", contenido)

def del_pendiente(device_id):
    redis_del(f"pendiente:{device_id}")

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
    ahora = datetime.now(ARG)
    lista = []

    keys = redis_keys('reloj:*')
    device_ids = set(k.replace('reloj', '') for k in keys)

    pend_keys = redis_keys('pendiente:*')
    pend_ids = set(k.replace('pendiente:', '') for k in pend_keys)
    todos_ids = device_ids | pend_ids

    for device_id in todos_ids:
        info = get_reloj(device_id)
        try:
            ultimo_ping_str = info.get('ultimo_ping', '')
            if not ultimo_ping_str:
                online = False
            else:
                ultimo_ping = datetime.fromisoformat(ultimo_ping_str)
                # Convertir a UTC para comparar correctamente
                if ultimo_ping.tzinfo is None:
                    ultimo_ping = ultimo_ping.replace(tzinfo=timezone.utc)
                ahora_utc = datetime.now(timezone.utc)
                diff = (ahora_utc - ultimo_ping).total_seconds()
                online = diff < 120
        except Exception as e:
            print(f"Error calculando online: {e}")
            online = False

        lista.append({
            'device_id': device_id,
            'nombre': info.get('nombre', f' {device_id}'),
            'ultima_actualizacion': info.get('ultima_actualizacion', '---'),
            'remoto_online': online,
            'reloj_online': info.get('reloj_online', False),
            'tiene_datos': bool(info.get('contenido')),
            'huellas_pendientes': device_id in pend_ids,
            'tiene_backup': bool(info.get('dat_backup'))
        })

    lista.sort(key=lambda x: (x['remoto_online'], x['huellas_pendientes']), reverse=True)
    return render_template('index.html', relojes=lista)

# ── API: ping ──────────────────────────────────────────────
@app.route('/api/ping', methods=['POST'])
def ping():
    device_id = request.form.get('device_id')
    nombre    = request.form.get('nombre', device_id)
    if not device_id:
        return jsonify({'error': 'device_id requerido'}), 400

    info = get_reloj(device_id) or {}
    info['nombre'] = nombre
    info['ultimo_ping'] = datetime.now(timezone.utc).isoformat()
    save_reloj(device_id, info)

    verificar = get_reloj(device_id)
    print(f"Ping OK - ultimo_ping: {verificar.get('ultimo_ping') if verificar else 'ERROR'}")

    return jsonify({'ok': True}), 200

# ── API: recibir fichadas ──────────────────────────────────
@app.route('/api/upload', methods=['POST'])
def upload():
    device_id = request.form.get('device_id')
    nombre    = request.form.get('nombre', device_id)
    if not device_id:
        return jsonify({'error': 'device_id requerido'}), 400
    if 'archivo' not in request.files:
        return jsonify({'error': 'archivo requerido'}), 400

    archivo = request.files['archivo']
    contenido_nuevo = archivo.read().decode('utf-8', errors='replace')

    info = get_reloj(device_id)
    contenido_anterior = info.get('contenido', '')
    info['nombre'] = nombre
    info['ultima_actualizacion'] = datetime.now(ARG).isoformat()
    info['contenido'] = contenido_anterior + contenido_nuevo
    info['reloj_online'] = True
    save_reloj(device_id, info)

    print(f"Fichadas recibidas de {nombre} ({device_id})")
    return jsonify({'ok': True, 'mensaje': 'Archivo recibido correctamente'}), 200

# ── API: recibir backup .dat ───────────────────────────────
@app.route('/api/subir_dat', methods=['POST'])
def subir_dat():
    device_id = request.form.get('device_id')
    if not device_id:
        return jsonify({'error': 'device_id requerido'}), 400
    if 'archivo' not in request.files:
        return jsonify({'error': 'archivo requerido'}), 400

    archivo = request.files['archivo']
    contenido = archivo.read().decode('utf-8', errors='replace')

    info = get_reloj(device_id)
    info['dat_backup'] = contenido
    save_reloj(device_id, info)

    print(f"Backup .dat recibido de {device_id}")
    return jsonify({'ok': True}), 200

# ── API: subir JSON de huellas ─────────────────────────────
@app.route('/api/subir_json', methods=['POST'])
def subir_json():
    device_id = request.form.get('device_id')
    if not device_id:
        return jsonify({'error': 'device_id requerido'}), 400
    if 'archivo' not in request.files:
        return jsonify({'error': 'archivo requerido'}), 400

    archivo = request.files['archivo']
    contenido = archivo.read().decode('utf-8', errors='replace')

    try:
        json.loads(contenido)
    except Exception:
        return jsonify({'error': 'JSON inválido'}), 400

    save_pendiente(device_id, contenido)
    print(f"JSON subido para device_id {device_id}")
    return jsonify({'ok': True, 'mensaje': f'JSON guardado para dispositivo {device_id}'}), 200

# ── API: consultar JSON pendiente ──────────────────────────
@app.route('/api/consultar_json/<device_id>', methods=['GET'])
def consultar_json(device_id):
    data = get_pendiente(device_id)
    return jsonify({'disponible': bool(data)}), 200

# ── API: descargar JSON pendiente ──────────────────────────
@app.route('/api/descargar_json/<device_id>', methods=['GET'])
def descargar_json(device_id):
    contenido = get_pendiente(device_id)
    if not contenido:
        return jsonify({'error': 'No hay archivo pendiente'}), 404

    del_pendiente(device_id)
    print(f"JSON de {device_id} descargado y eliminado.")

    response = make_response(contenido)
    response.headers['Content-Disposition'] = f'attachment; filename=users_{device_id}.json'
    response.headers['Content-Type'] = 'application/json; charset=utf-8'
    return response

# ── Descargar fichadas ─────────────────────────────────────
@app.route('/descargar/<device_id>')
@login_required
def descargar(device_id):
    info = get_reloj(device_id)
    if not info:
        return 'Reloj no encontrado', 404

    contenido = info.get('contenido', '')
    info['contenido'] = ''
    save_reloj(device_id, info)

    response = make_response(contenido)
    response.headers['Content-Disposition'] = f'attachment; filename=aramis_GN_{device_id}.dat'
    response.headers['Content-Type'] = 'text/plain; charset=utf-8'
    print(f"Fichadas de {device_id} descargadas y puestas en cero.")
    return response

# ── Descargar backup .dat ──────────────────────────────────
@app.route('/backup/<device_id>')
@login_required
def backup(device_id):
    info = get_reloj(device_id)
    if not info:
        return 'Reloj no encontrado', 404

    contenido = info.get('dat_backup', '')
    if not contenido:
        return 'Sin backup disponible', 404

    response = make_response(contenido)
    response.headers['Content-Disposition'] = f'attachment; filename=aramis_{device_id}.dat'
    response.headers['Content-Type'] = 'text/plain; charset=utf-8'
    return response


# ── API: estado del reloj ZKTeco ───────────────────────────
@app.route('/api/estado_reloj', methods=['POST'])
def estado_reloj():
    device_id = request.form.get('device_id')
    nombre    = request.form.get('nombre', device_id)
    conectado = request.form.get('conectado', '0') == '1'
    if not device_id:
        return jsonify({'error': 'device_id requerido'}), 400

    info = get_reloj(device_id)
    info['nombre'] = nombre
    info['reloj_online'] = conectado
    save_reloj(device_id, info)

    print(f"Estado reloj {device_id}: {'conectado' if conectado else 'sin conexion'}")
    return jsonify({'ok': True}), 200

if __name__ == '__main__':
    app.run(debug=False, host='0.0.0.0', port=5000)
