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
    for fname in ['index_1.html', 'Index-1.html', 'index.html', 'Index_1.html']:
        if os.path.exists(fname):
            return render_template(fname)
    return "index_1.html not found", 404

@app.route('/static/uploads/<path:filename>')
def uploaded_files(filename):
    return send_from_directory(UPLOAD_FOLDER, filename)

@app.route('/uploads/<path:filename>')
def uploads_compat(filename):
    if os.path.exists(os.path.join('uploads', filename)):
        return send_from_directory('uploads', filename)
    return send_from_directory(UPLOAD_FOLDER, filename)

# --- PUBLIC API ---
@app.route('/api/data', methods=['GET'])
def get_data():
    db = load_db()
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
            "img": f"/{image_path}",
            "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "createdAt": datetime.datetime.now().isoformat()
        }
        db['products'].insert(0, new_product)
        save_db(db)
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

# --- LIBRETUBE / PIPED API INTEGRATION FOR YOUTUBE ---
def fetch_via_libretube(url_or_keyword):
    """
    LibreTube ও Piped প্রজেক্টের ওপেন সোর্স ব্যাকএন্ড এপিআই ব্যবহার করে ইউটিউব ডেটা ফেচ করার ফাংশন।
    """
    try:
        # যদি এটি সার্চ কিউওয়ার্ড হয়
        if not url_or_keyword.startswith(('http://', 'https://')):
            piped_search_url = f"https://pipedapi.kavin.rocks/search?q={requests.utils.quote(url_or_keyword)}&filter=videos"
            res = requests.get(piped_search_url, timeout=10)
            if res.status_code == 200:
                items = res.json().get('items', [])
                if items:
                    vid = items[0].get('url', '').split('=')[-1]
                    url_or_keyword = f"https://www.youtube.com/watch?v={vid}"
                else:
                    return None

        # ভিডিও আইডি বা লিংক এক্সট্রাক্ট করা
        vid_id = ""
        if "youtu.be/" in url_or_keyword:
            vid_id = url_or_keyword.split("youtu.be/")[1].split("?")[0]
        elif "watch?v=" in url_or_keyword:
            vid_id = url_or_keyword.split("watch?v=")[1].split("&")[0]

        if not vid_id:
            return None

        # LibreTube/Piped ওপেন সোর্স ইনস্ট্যান্স থেকে স্ট্রিম ইনফো নেওয়া
        piped_info_url = f"https://pipedapi.kavin.rocks/streams/{vid_id}"
        r = requests.get(piped_info_url, timeout=15)
        if r.status_code == 200:
            data = r.json()
            # ভিডিও স্ট্রিম লিংক খুঁজে বের করা
            video_streams = data.get('videoStreams', [])
            best_stream_url = None
            for s in video_streams:
                if s.get('videoOnly') == False and s.get('url'):
                    best_stream_url = s.get('url')
                    break
            
            if not best_stream_url and video_streams:
                best_stream_url = video_streams[0].get('url')

            title = data.get('title', 'YouTube Video')
            safe_title = "".join([c for c in title if c.isalnum() or c in (' ', '-', '_')]).strip()[:80]

            return {
                'success': True,
                'title': title,
                'thumbnail': data.get('thumbnailUrl', ''),
                'duration': data.get('duration', 0),
                'uploader': data.get('uploader', 'Unknown'),
                'ext': 'mp4',
                'platform': 'youtube_libretube',
                'video_url': best_stream_url,
                'url': best_stream_url,
                'original_url': url_or_keyword,
                'filename': f"{safe_title}.mp4"
            }
    except Exception as e:
        logger.error(f"LibreTube API Error: {e}")
    return None

# --- UNIVERSAL Video Downloader (YouTube via LibreTube/Piped + Others via yt-dlp) ---
@app.route('/api/fetch', methods=['POST'])
def fetch_video_data():
    data = request.get_json() or {}
    url_or_keyword = data.get('url', '').strip()
    if not url_or_keyword:
        return jsonify({'error': 'লিংক বা কিউওয়ার্ড দিন'}), 400

    # যদি এটি ইউটিউব লিংক বা ইউটিউব সার্চ হয়, তবে প্রাইওরিটি হিসেবে LibreTube ওপেন সোর্স মেথড ব্যবহার করবে
    if 'youtube.com' in url_or_keyword or 'youtu.be' in url_or_keyword or not url_or_keyword.startswith(('http://', 'https://')):
        lt_result = fetch_via_libretube(url_or_keyword)
        if lt_result:
            return jsonify(lt_result)

    # অন্যান্য প্ল্যাটফর্মের (যেমন TikTok, Facebook ইত্যাদি) জন্য আগের মতো yt-dlp অপশন কাজ করবে
    ydl_opts = {
        'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
        'nocheckcertificate': True,
        'noplaylist': True,
        'ignoreerrors': True,
        'quiet': True,
        'no_warnings': True,
        'geo_bypass': True,
        'socket_timeout': 30,
    }

    is_search = not url_or_keyword.startswith(('http://', 'https://'))
    if is_search:
        url_or_keyword = f"ytsearch1:{url_or_keyword}"
        ydl_opts['noplaylist'] = False

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url_or_keyword, download=False)
            if 'entries' in info:
                entries = [e for e in info['entries'] if e]
                if not entries:
                    return jsonify({'error': 'কোনো ভিডিও পাওয়া যায়নি'}), 404
                video_data = entries[0]
            else:
                video_data = info

            raw_video_url = video_data.get('url')
            if not raw_video_url:
                return jsonify({'error': 'ভিডিও লিংক পাওয়া যায়নি।'}), 404

            proxied_video_url = f"/api/proxy_video?stream_url={requests.utils.quote(raw_video_url)}"
            title = video_data.get('title', 'Video')
            safe_title = "".join([c for c in title if c.isalnum() or c in (' ', '-', '_')]).strip()[:80]

            return jsonify({
                'success': True,
                'title': title,
                'thumbnail': video_data.get('thumbnail', ''),
                'duration': video_data.get('duration', 0),
                'uploader': video_data.get('uploader', 'Unknown'),
                'ext': 'mp4',
                'platform': video_data.get('extractor', 'unknown'),
                'video_url': proxied_video_url,
                'url': proxied_video_url,
                'original_url': raw_video_url,
                'filename': f"{safe_title}.mp4"
            })
    except Exception as e:
        return jsonify({'error': f"ব্যর্থ হয়েছে: {str(e)[:200]}"}), 500

@app.route('/api/proxy_video')
def proxy_video():
    stream_url = request.args.get('stream_url')
    if not stream_url:
        return "Missing URL", 400
    try:
        req_headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': '*/*',
            'Range': request.headers.get('Range', ''),
        }
        r = requests.get(stream_url, headers=req_headers, stream=True, timeout=30, verify=False)
        
        response_headers = {}
        for h in ['Content-Type', 'Content-Length', 'Accept-Ranges', 'Content-Range', 'Content-Disposition']:
            if r.headers.get(h):
                response_headers[h] = r.headers.get(h)
        
        if 'Content-Type' not in response_headers:
            response_headers['Content-Type'] = 'video/mp4'
        
        response_headers['Access-Control-Allow-Origin'] = '*'

        def generate():
            for chunk in r.iter_content(chunk_size=512*1024):
                if chunk:
                    yield chunk

        status = r.status_code if r.status_code in [200, 206] else 200
        return Response(generate(), status=status, headers=response_headers)
    except Exception as e:
        return f"Error streaming video: {str(e)[:100]}", 500

@app.route('/api/proxy_thumb')
def proxy_thumb():
    thumb_url = request.args.get('url')
    if not thumb_url:
        return "", 404
    try:
        r = requests.get(thumb_url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=10, stream=True)
        return Response(r.iter_content(8192), content_type=r.headers.get('Content-Type','image/jpeg'))
    except:
        return "", 404

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    print(f"\n✅ PUBLIC SERVER: http://localhost:{port}")
    app.run(host='0.0.0.0', port=port, debug=False)
