import os
import json
import logging
import time
import datetime
import requests
from flask import Flask, request, jsonify, render_template, Response, send_from_directory
from flask_cors import CORS
from werkzeug.utils import secure_filename
import yt_dlp

app = Flask(__name__, template_folder='.', static_folder='static')
app.secret_key = os.environ.get('SECRET_KEY', 'universal_save_super_secret_key')
CORS(app)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- PUBLIC LIFETIME PERSISTENCE CONFIG ---
# Render এ static/uploads ফোল্ডার পাবলিক, data.json সারা জীবন সেভ
DATA_FILE = os.environ.get('DATA_FILE', 'marketplace_data.json')
UPLOAD_FOLDER = os.path.join('static', 'uploads')
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 20 * 1024 * 1024

# ফোল্ডার তৈরি - না থাকলে বানাবে
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs('uploads', exist_ok=True)

def load_db():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                # ensure keys
                if 'products' not in data: data['products'] = []
                if 'orders' not in data: data['orders'] = []
                return data
        except Exception as e:
            logger.error(f"DB load error: {e}")
    return {"products": [], "orders": []}

def save_db(data):
    try:
        with open(DATA_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"DB save error: {e}")

@app.route('/')
def index():
    # GitHub এ ফাইল নাম Index-1.html / index_1.html / index.html যে কোনো একটা হতে পারে
    for fname in ['index_1.html', 'Index-1.html', 'index.html', 'Index_1.html']:
        if os.path.exists(fname):
            return render_template(fname)
    return "index_1.html not found", 404

@app.route('/static/uploads/<path:filename>')
def uploaded_files(filename):
    return send_from_directory(UPLOAD_FOLDER, filename)

@app.route('/uploads/<path:filename>')
def uploads_compat(filename):
    # পুরোনো লিংক /uploads/ সাপোর্ট
    if os.path.exists(os.path.join('uploads', filename)):
        return send_from_directory('uploads', filename)
    return send_from_directory(UPLOAD_FOLDER, filename)

# --- PUBLIC API - সবাই এখান থেকে দেখবে ---
@app.route('/api/data', methods=['GET'])
def get_data():
    db = load_db()
    # No cache so public sees instantly
    resp = jsonify(db)
    resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate'
    resp.headers['Pragma'] = 'no-cache'
    return resp

@app.route('/api/add-product', methods=['POST'])
def add_product():
    try:
        name = request.form.get('name')
        price = request.form.get('price')
        image_file = request.files.get('image')

        if not name or not price or not image_file:
            return jsonify({'success': False, 'error': 'সব তথ্য দিন'}), 400

        filename = secure_filename(image_file.filename)
        filename = f"{int(time.time())}_{filename}"
        image_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        image_file.save(image_path)

        db = load_db()
        new_product = {
            "id": int(time.time() * 1000),
            "name": name.strip(),
            "price": price.strip(),
            "img": f"/{image_path}",  # PUBLIC LINK - সবাই দেখবে
            "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "createdAt": datetime.datetime.now().isoformat()
        }
        # নতুনটা সবার উপরে - public এ সবাই প্রথমে দেখবে
        db['products'].insert(0, new_product)
        save_db(db)
        logger.info(f"[PUBLIC SAVE] {new_product['name']} -> {new_product['img']}")

        return jsonify({'success': True, 'product': new_product})
    except Exception as e:
        logger.error(f"Add product error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/delete-product/<int:prod_id>', methods=['DELETE'])
def delete_product(prod_id):
    try:
        db = load_db()
        for p in db['products']:
            if p['id'] == prod_id:
                img_path = p['img'].lstrip('/')
                if os.path.exists(img_path):
                    try: os.remove(img_path)
                    except: pass
                break
        db['products'] = [p for p in db['products'] if p['id'] != prod_id]
        save_db(db)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/place-order', methods=['POST'])
def place_order():
    try:
        data = request.get_json() or {}
        item = data.get('item')
        phone = data.get('phone')
        if not item or not phone:
            return jsonify({'success': False, 'error': 'তথ্য অসম্পূর্ণ'}), 400
        db = load_db()
        db['orders'].insert(0, {
            "item": item,
            "phone": phone,
            "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        })
        save_db(db)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

# --- Video Downloader ---
@app.route('/api/fetch', methods=['POST'])
def fetch_video_data():
    data = request.get_json() or {}
    url_or_keyword = data.get('url')
    if not url_or_keyword:
        return jsonify({'error': 'লিংক বা কিউওয়ার্ড দিন'}), 400
    ydl_opts = {
        'nocheckcertificate': True,
        'ignoreerrors': False,
        'no_warnings': False,
        'quiet': False,
        'format': 'best[ext=mp4]/best',
        'http_headers': {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36'}
    }
    if not url_or_keyword.startswith(('http://', 'https://')):
        url_or_keyword = f"ytsearch1:{url_or_keyword}"
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url_or_keyword, download=False)
            if 'entries' in info:
                entries = list(info['entries'])
                video_data = entries[0] if entries and entries[0] is not None else info
            else:
                video_data = info
            raw_video_url = None
            for f in reversed(video_data.get('formats', [])):
                if f.get('url') and f.get('acodec') != 'none' and f.get('vcodec') != 'none':
                    if "manifest" not in f['url']:
                        raw_video_url = f['url']
                        break
            if not raw_video_url:
                raw_video_url = video_data.get('url', '')
            if not raw_video_url:
                return jsonify({'error': 'ভিডিও লিংক পাওয়া যায়নি'}), 404
            proxied_video_url = f"/api/proxy_video?stream_url={requests.utils.quote(raw_video_url)}"
            return jsonify({
                'success': True,
                'title': video_data.get('title', 'Unknown'),
                'thumbnail': video_data.get('thumbnail', ''),
                'duration': video_data.get('duration', 0),
                'uploader': video_data.get('uploader', 'Unknown'),
                'video_url': proxied_video_url,
                'url': proxied_video_url,
                'filename': video_data.get('title', 'video') + '.mp4'
            })
    except Exception as e:
        logger.error(f"Fetch error: {e}")
        return jsonify({'error': f"ব্যর্থ: {str(e)}"}), 500

@app.route('/api/proxy_video')
def proxy_video():
    stream_url = request.args.get('stream_url')
    if not stream_url:
        return "Missing URL", 400
    try:
        req_headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': '*/*',
            'Range': request.headers.get('Range', '')
        }
        r = requests.get(stream_url, headers=req_headers, stream=True, timeout=20)
        response_headers = {
            'Content-Type': r.headers.get('Content-Type', 'video/mp4'),
            'Content-Length': r.headers.get('Content-Length', ''),
            'Accept-Ranges': 'bytes'
        }
        if r.headers.get('Content-Range'):
            response_headers['Content-Range'] = r.headers.get('Content-Range')
        def generate():
            for chunk in r.iter_content(chunk_size=256*1024):
                if chunk: yield chunk
        return Response(generate(), status=r.status_code, headers=response_headers)
    except Exception as e:
        return "Error streaming video", 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    print(f"\n✅ PUBLIC SERVER: http://localhost:{port}")
    print(f"📂 Uploads: {UPLOAD_FOLDER} - PUBLIC")
    print(f"💾 DB: {DATA_FILE} - Lifetime\n")
    app.run(host='0.0.0.0', port=port, debug=False)
