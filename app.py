import os
import json
from datetime import datetime, timezone, timedelta
from flask import Flask, request, jsonify, render_template, send_file, redirect, url_for, session, make_response
from functools import wraps
import io

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'clave_secreta_cambiar')

ARG = timezone(timedelta(hours=-3))

# ── Configuración ──────────────────────────────────────────
WEB_USER     = os.environ.get('WEB_USER', 'admin')
WEB_PASSWORD = os.environ.get('WEB_PASSWORD', 'admin123')

# Base de datos en memoria
# { device_id: { nombre, ultima_actualizacion, contenido } }
relojes = {}

# Archivos JSON pendientes por device_id
# { device_id: contenido_json_string }
archivos_pendientes = {}

# ── Login requerido ────────────────────────────────────────
def login_required(f):
    from functools import wraps
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
    for device_id, info in relojes.items():
        try:
            ultima = datetime.fromisoformat(info['ultima_actualizacion'])
            if ultima.tzinfo is None:
                ultima = ultima.replace(tzinfo=ARG)
            diff = (ahora - ultima).total_seconds()
            online = diff < 600
        except Exception:
            online = False
        lista.append({
            'device_id': device_id,
            'nombre': info.get('nombre', device_id),
            'ultima_actualizacion': info.get('ultima_actualizacion', '---'),
            'online': online,
            'tiene_datos': bool(info.get('contenido')),
            'huellas_pendientes': bool(archivos_pendientes.get(device_id))
        })
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
        return jsonify({'error': 'archivo vacio'}), 400

    # Leer contenido del archivo
    contenido_nuevo = archivo.read().decode('utf-8', errors='replace')

    # Acumular contenido al existente
    contenido_anterior = relojes.get(device_id, {}).get('contenido', '')
    contenido_acumulado = contenido_anterior + contenido_nuevo

    # Guardar en memoria
    relojes[device_id] = {
        'nombre': nombre,
        'ultima_actualizacion': datetime.now(ARG).isoformat(),
        'contenido': contenido_acumulado
    }

    print(f"[{datetime.now(ARG).strftime('%d/%m/%Y %H:%M:%S')}] Archivo recibido de {nombre} ({device_id})")
    return jsonify({'ok': True, 'mensaje': 'Archivo recibido correctamente'}), 200

# ── Descargar archivo de un reloj ──────────────────────────
@app.route('/descargar/<device_id>')
@login_required
def descargar(device_id):
    if device_id not in relojes:
        return 'Reloj no encontrado', 404

    contenido = relojes[device_id].get('contenido', '')

    # Enviar como archivo descargable
    response = make_response(contenido)
    response.headers['Content-Disposition'] = f'attachment; filename=aramis_GN_{device_id}.dat'
    response.headers['Content-Type'] = 'text/plain; charset=utf-8'

    # Poner en cero el contenido tras la descarga
    relojes[device_id]['contenido'] = ''
    print(f"Archivo de {device_id} descargado y puesto en cero.")

    return response


# ── API: subir JSON de usuarios desde programa externo ─────
@app.route('/api/subir_json', methods=['POST'])
def subir_json():
    device_id = request.form.get('device_id')
    if not device_id:
        return jsonify({'error': 'device_id requerido'}), 400
    if 'archivo' not in request.files:
        return jsonify({'error': 'archivo requerido'}), 400

    archivo = request.files['archivo']
    contenido = archivo.read().decode('utf-8', errors='replace')

    # Validar que sea JSON válido
    try:
        json.loads(contenido)
    except Exception:
        return jsonify({'error': 'El archivo no es un JSON válido'}), 400

    archivos_pendientes[device_id] = contenido
    print(f"JSON subido para device_id {device_id}")
    return jsonify({'ok': True, 'mensaje': f'JSON guardado para dispositivo {device_id}'}), 200


# ── API: consultar si hay JSON pendiente para un device_id ──
@app.route('/api/consultar_json/<device_id>', methods=['GET'])
def consultar_json(device_id):
    if device_id in archivos_pendientes and archivos_pendientes[device_id]:
        return jsonify({'disponible': True}), 200
    return jsonify({'disponible': False}), 200


# ── API: descargar JSON pendiente ──────────────────────────
@app.route('/api/descargar_json/<device_id>', methods=['GET'])
def descargar_json(device_id):
    if device_id not in archivos_pendientes or not archivos_pendientes[device_id]:
        return jsonify({'error': 'No hay archivo pendiente'}), 404

    contenido = archivos_pendientes[device_id]

    # Borrar del servidor una vez descargado
    archivos_pendientes[device_id] = ''
    print(f"JSON de {device_id} descargado y eliminado del servidor.")

    response = make_response(contenido)
    response.headers['Content-Disposition'] = f'attachment; filename=users_{device_id}.json'
    response.headers['Content-Type'] = 'application/json; charset=utf-8'
    return response

if __name__ == '__main__':
    app.run(debug=False, host='0.0.0.0', port=5000)
