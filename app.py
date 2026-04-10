import io
import os
import re
import json
import subprocess
from flask import Flask, request, jsonify, Response, send_from_directory, send_file
from flask_jwt_extended import JWTManager, jwt_required, create_access_token, get_jwt_identity, verify_jwt_in_request
from flask_pymongo import PyMongo
from flask_cors import CORS
from datetime import timedelta, datetime
from dotenv import load_dotenv
from werkzeug.security import generate_password_hash, check_password_hash
from glob import glob
from apscheduler.schedulers.background import BackgroundScheduler
import logging
from logging.handlers import RotatingFileHandler
from PIL import Image

load_dotenv()

app = Flask(__name__)

# log/ 디렉토리 생성
os.makedirs('log', exist_ok=True)

log_format = logging.Formatter('%(asctime)s %(levelname)s: %(message)s')


# DEBUG 레벨만 통과시키는 필터
class DebugOnlyFilter(logging.Filter):
    def filter(self, record):
        return record.levelno == logging.DEBUG


# debug.log: DEBUG 레벨만 (1MB 초과 시 log/debug.log.1 등으로 순환)
debug_log_handler = RotatingFileHandler(
    'log/debug.log', maxBytes=1_048_576, backupCount=5, encoding='utf-8')
debug_log_handler.setLevel(logging.DEBUG)
debug_log_handler.addFilter(DebugOnlyFilter())
debug_log_handler.setFormatter(log_format)

# info.log: INFO 이상 (1MB 초과 시 log/info.log.1 등으로 순환)
info_log_handler = RotatingFileHandler(
    'log/info.log', maxBytes=1_048_576, backupCount=5, encoding='utf-8')
info_log_handler.setLevel(logging.INFO)
info_log_handler.setFormatter(log_format)

# add logger handler to Flask application
app.logger.addHandler(debug_log_handler)
app.logger.addHandler(info_log_handler)

# set log level to debug
app.logger.setLevel(logging.DEBUG)

# CORS setting
CORS(app, resources={
     r"/*": {"origins": [os.getenv('FRONT_DEV'), os.getenv('FRONT_PRD')]}})


app.config['MONGO_URI'] = os.getenv('MONGO_URI')
app.config['JWT_SECRET_KEY'] = os.getenv('JWT_SECRET_KEY')
app.config['JWT_ACCESS_TOKEN_EXPIRES'] = timedelta(
    days=int(os.getenv('JWT_EXP_DAY')))
app.config['SCHEDULER_API_ENABLED'] = True

jwt = JWTManager(app)
mongo = PyMongo(app)
scheduler = BackgroundScheduler()

# APScheduler 에러를 info.log에 기록
apscheduler_logger = logging.getLogger('apscheduler')
apscheduler_logger.addHandler(info_log_handler)
apscheduler_logger.setLevel(logging.WARNING)

scheduler.start()


@scheduler.scheduled_job('cron',
                         id='making_thumbnails',
                         hour='*',
                         minute='*/10',
                         misfire_grace_time=10,
                         max_instances=3)
def making_thumbnails():
    # Generate today's date string
    today = datetime.now().strftime('%Y-%m-%d')

    # Get all subfolders in the static directory
    subfolders = [f for f in glob(
        f'{os.getenv("IMAGES")}/*') if os.path.isdir(f)]

    # Get a list of existing thumbnail files
    existing_thumbnails = [f for f in glob('static/thumb_*.jpg')]

    # Make report list
    remove_site = []
    no_photo_yet_site = []
    thumbnail_made_site = []

    # Remove thumbnails for subfolders that no longer exist
    site_folder_set = {os.path.basename(subfolder) for subfolder in subfolders}
    for existing_thumbnail in existing_thumbnails:
        thumbnail_name = os.path.basename(existing_thumbnail)
        site = os.path.splitext(thumbnail_name)[0].replace('thumb_', '')
        if site not in site_folder_set:
            os.remove(existing_thumbnail)
            remove_site.append(site)

    # Process all the folders
    for folder_path in subfolders:
        folder_name = os.path.basename(folder_path)

        # Try to find the settings folder,
        # If not found, skip.
        setting_folder = os.path.join(
            os.getenv("IMAGES"), folder_name, 'setting')
        if not os.path.exists(setting_folder):
            continue

        # Try to find the folder for today's date
        image_folder = os.path.join(
            os.getenv("IMAGES"), folder_name, today)
        if not os.path.exists(image_folder):
            # If the folder does not exist, use no_image_today.jpg
            with Image.open('static/no_image_today.jpg') as img:
                thumbnail_path = os.path.join(
                    'static', f'thumb_{folder_name}.jpg')
                img.save(thumbnail_path)
                no_photo_yet_site.append(folder_name)
            continue

        # If the folder exists, find the latest image file in the folder
        image_files = glob(os.path.join(image_folder, '*.jpg'))
        if not image_files:
            with Image.open('static/no_image_today.jpg') as img:
                thumbnail_path = os.path.join(
                    'static', f'thumb_{folder_name}.jpg')
                img.save(thumbnail_path)
                no_photo_yet_site.append(folder_name)
            continue
        latest_image_file = max(image_files)

        # Generate the thumbnail of the latest image
        with Image.open(latest_image_file) as img:
            img.thumbnail((300, 200))
            thumbnail_path = os.path.join(
                'static', f'thumb_{folder_name}.jpg')
            img.save(thumbnail_path)
            thumbnail_made_site.append(folder_name)

    app.logger.info(f'Sites removed                : {remove_site}')
    app.logger.info(f'Sites with no photos yet     : {no_photo_yet_site}')
    app.logger.info(f'Sites with thumbnails created: {thumbnail_made_site}')


@scheduler.scheduled_job('cron',
                         id='making_setting_json',
                         hour='*',
                         minute='*/10',
                         misfire_grace_time=10,
                         max_instances=3)
def making_setting_json():
    # Save Settings for each site
    settings = {}
    # Generate today's date string
    today = datetime.now().strftime('%Y-%m-%d')
    sites = [f.path for f in os.scandir(os.getenv('IMAGES')) if f.is_dir()]
    setting_missing_site = []
    no_photos_today_site = []
    created_setting_site = []

    for site in sites:
        site_settings = {}
        site_name = os.path.basename(site)
        folders = [os.path.basename(f.path)
                   for f in os.scandir(site) if f.is_dir()]
        if 'setting' not in folders:
            setting_missing_site.append(site.replace('images/', ' '))
            continue
        else:
            file_path = os.path.join(site, 'setting', 'settings.txt')
            if not os.path.exists(file_path):
                setting_missing_site.append(site.replace('images/', ' '))
                continue
            with open(file_path, 'r') as f:
                for line in f:
                    line = line.strip()
                    if not line or '=' not in line:
                        continue
                    key, _, value = line.partition('=')
                    site_settings[key.strip()] = value.strip().strip('"')
            required_keys = ["time_start", "time_end", "time_interval"]
            missing_keys = [k for k in required_keys if k not in site_settings]
            if missing_keys:
                app.logger.warning(f'Site {site_name} missing keys in settings.txt: {missing_keys}')
                setting_missing_site.append(site_name)
                continue
            try:
                # Calculate Shooting Count of today
                start_minutes = int(
                    site_settings["time_start"][:2]) * 60 + int(site_settings["time_start"][2:])
                end_minutes = int(
                    site_settings["time_end"][:2]) * 60 + int(site_settings["time_end"][2:])
                interval_minutes = int(site_settings["time_interval"])
            except (ValueError, IndexError) as e:
                app.logger.warning(f'Site {site_name} has invalid time settings: {e}')
                setting_missing_site.append(site_name)
                continue
            if interval_minutes <= 0:
                app.logger.warning(f'Site {site_name} has invalid time_interval: {interval_minutes}')
                setting_missing_site.append(site_name)
                continue
            crosses_midnight = end_minutes < start_minutes
            if crosses_midnight:
                end_minutes += 1440
            site_settings["shooting_count"] = (
                end_minutes - start_minutes) // interval_minutes + 1
            # Calculate Shooting Count of current time
            current_time = datetime.now()
            current_minutes_raw = current_time.hour * 60 + current_time.minute
            current_minutes = current_minutes_raw + (1440 if crosses_midnight and current_minutes_raw < start_minutes else 0)
            current_minutes = min(current_minutes, end_minutes)
            site_settings["shooting_count_till_now"] = max(0, (
                current_minutes - start_minutes) // interval_minutes + 1)

        is_after_midnight = crosses_midnight and current_minutes_raw < start_minutes
        if is_after_midnight:
            yesterday = (current_time - timedelta(days=1)).strftime('%Y-%m-%d')
            photo_folders = [f for f in [yesterday, today] if f in folders]
        else:
            photo_folders = [today] if today in folders else []

        if photo_folders:
            all_photos = []
            for folder in photo_folders:
                all_photos.extend(os.listdir(os.path.join(site, folder)))
            site_settings['photos_count'] = len(all_photos)
            site_settings['recent_photo'] = sorted(all_photos)[-1] if all_photos else "No Photo Available"
            created_setting_site.append(site.replace('images/', ' '))
        else:
            site_settings['photos_count'] = 0
            site_settings['recent_photo'] = "No Photo Available"
            no_photos_today_site.append(site.replace('images/', ' '))

        settings[site_name] = site_settings

    # Check connection via tailscale (primary) or SSH (fallback)
    connected_devices = None

    # Primary: tailscale status
    try:
        ts_result = subprocess.run(
            ["tailscale", "status", "--json"],
            capture_output=True, text=True, check=True
        )
        ts_status = json.loads(ts_result.stdout)
        connected_devices = {
            peer["HostName"].lower()
            for peer in ts_status.get("Peer", {}).values()
            if peer.get("Online", False)
        }
        app.logger.info(f'Tailscale check succeeded: {sorted(connected_devices)}')
    except Exception as e:
        app.logger.warning(f'Tailscale check failed, trying SSH fallback: {e}')

    # Fallback: SSH
    if connected_devices is None:
        command = ["ssh", os.getenv("SSH_HOST"), '-p',
                   os.getenv("SSH_PORT"), os.getenv("SSH_COMMAND")]
        try:
            result = subprocess.run(
                command, capture_output=True, text=True, check=True).stdout
            ssh_numbers = []
            for line in result.splitlines():
                match = re.search(r'127\.0\.0\.1:(\d+)', line)
                if match:
                    port = match.group(1).replace('22', '')
                    if port.isdigit():
                        ssh_numbers.append(int(port))
            connected_devices = {f'bmotion{n}' for n in ssh_numbers}
            app.logger.info(f'SSH fallback succeeded: {connected_devices}')
        except subprocess.CalledProcessError as e:
            app.logger.error(f'SSH connection check failed: {e}')
            return
        except Exception as e:
            app.logger.error(f'SSH fallback parsing failed: {e}')
            return

    for site_name, setting in settings.items():
        device_number = setting.get('device_number')
        if device_number:
            settings[site_name]['ssh'] = device_number.lower() in connected_devices
        else:
            app.logger.warning(f'Site {site_name} missing device_number in settings.txt')
            settings[site_name]['ssh'] = False

    settings_json = json.dumps(settings, indent=4)
    # Save Json into File
    with open('settings.json', 'w') as json_file:
        json_file.write(settings_json)

    app.logger.info(
        f'Setting does not exist for the site  : {setting_missing_site}')
    app.logger.info(
        f'Site with no photos today            : {no_photos_today_site}')
    app.logger.info(
        f'Setting has been created for the site: {created_setting_site}')


# Load settings from settings.json
def load_settings():
    if not os.path.exists('settings.json'):
        app.logger.warning('settings.json not found')
        return {}
    try:
        with open('settings.json', 'r') as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        app.logger.error(f'Failed to load settings.json: {e}')
        return {}


def user_auth_sites(username):
    data = mongo.db.users.find_one(
        {'username': username}, {"sites": 1, "_id": 0})
    if data is None:
        return []
    return data.get("sites") or []


def is_admin(identity):
    return identity.get("class") == "bmotion"


def check_site_access(identity, site):
    """사용자의 허가된 사이트면 True를 반환합니다."""
    return site in user_auth_sites(identity.get('username'))


def read_paginated_logs(log_type, page, page_size):
    base_log_path = os.path.join('log', f'{log_type}.log')
    rotated_log_paths = sorted(
        glob(f'{base_log_path}.*'),
        key=lambda path: int(path.rsplit('.', 1)[1]) if path.rsplit('.', 1)[1].isdigit() else 10**9
    )

    log_paths = [base_log_path] + rotated_log_paths
    log_lines = []

    for path in log_paths:
        if not os.path.exists(path):
            continue

        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            lines = f.readlines()
            # 최신 로그가 먼저 오도록 파일 내부 라인 순서를 뒤집습니다.
            for line in reversed(lines):
                log_lines.append(line.rstrip('\n'))

    total = len(log_lines)
    total_pages = (total + page_size - 1) // page_size if total > 0 else 0
    start = (page - 1) * page_size
    end = start + page_size

    return {
        "logs": log_lines[start:end],
        "total": total,
        "total_pages": total_pages
    }


# auth - signup
@app.route('/signup', methods=['POST'])
def signup():
    data = request.get_json()
    if not data or 'username' not in data or 'password' not in data or 'code' not in data:
        return jsonify({'message': 'Invalid data'}), 400
    if mongo.db.users.find_one({'username': data['username']}) or mongo.db.pending_users.find_one({'username': data['username']}):
        app.logger.warning(f"Signup failed - username already exists: {data['username']}")
        return jsonify({'message': 'User already exists'}), 400
    hashed_password = generate_password_hash(data['password'])
    mongo.db.pending_users.insert_one(
        {'username': data['username'], 'password': hashed_password, 'code': data['code']})
    app.logger.info(f"Signup requested: {data['username']}")
    return jsonify({'message': 'User registered, awaiting approval'}), 201


# auth - list of pending user
@app.route('/users/pending', methods=['GET'])
@jwt_required()
def list_pending_users():
    # admin 유저 권한 확인
    current_user_identity = get_jwt_identity()
    if (not is_admin(current_user_identity)):
        return jsonify({'message': 'Not authorized'}), 403

    users = mongo.db.pending_users.find()
    # Making list of pending user
    user_list = []
    for user in users:
        user_data = {
            'username': user['username'],
            'code': user['code']
        }
        user_list.append(user_data)

    return jsonify({'pending_users': user_list}), 200


# auth admin - approve user
@app.route('/approve/<username>', methods=['PUT'])
@jwt_required()
def approve_user(username):
    # admin 유저 권한 확인
    current_user_identity = get_jwt_identity()
    if (not is_admin(current_user_identity)):
        return jsonify({'message': 'Not authorized'}), 403

    user = mongo.db.pending_users.find_one({'username': username})
    if not user:
        return jsonify({'message': 'User not found in pending list'}), 404
    user['class'] = 'user'
    user['sites'] = []
    user['activate'] = True
    mongo.db.users.insert_one(user)
    mongo.db.pending_users.delete_one({'username': username})
    app.logger.info(f"User approved: {username} by {current_user_identity.get('username')}")
    return jsonify({'message': f'User {username} approved and added to users'}), 200


# auth admin - decline user
@app.route('/decline/<username>', methods=['PUT'])
@jwt_required()
def decline_user(username):
    # admin 유저 권한 확인
    current_user_identity = get_jwt_identity()
    if (not is_admin(current_user_identity)):
        return jsonify({'message': 'Not authorized'}), 403

    user = mongo.db.pending_users.find_one({'username': username})
    if not user:
        return jsonify({'message': 'User not found in pending list'}), 404
    mongo.db.pending_users.delete_one({'username': username})
    app.logger.info(f"User declined: {username} by {current_user_identity.get('username')}")
    return jsonify({'message': f'User {username} declined'}), 200


# auth - login
@app.route('/login', methods=['POST'])
def login():
    data = request.get_json()
    if not data or 'username' not in data or 'password' not in data:
        return jsonify({'message': 'Invalid data'}), 400
    user = mongo.db.users.find_one({'username': data['username']})
    if not user or not check_password_hash(user['password'], data['password']):
        app.logger.warning(f"Login failed: {data.get('username', 'unknown')} from {request.remote_addr}")
        return jsonify({'message': 'Invalid credentials'}), 400
    if not user.get('activate', False):
        app.logger.warning(f"Login blocked - deactivated user: {data['username']} from {request.remote_addr}")
        return jsonify({'message': 'Account is deactivated'}), 403
    access_token = create_access_token(
        identity={'username': user['username'], 'class': user['class']})
    app.logger.info(f"Login success: {user['username']} from {request.remote_addr}")
    return jsonify({'access_token': access_token, 'message': 'Login success.'}), 200


# auth - check
@app.route('/auth', methods=['GET'])
@jwt_required()
def auth():
    current_user_identity = get_jwt_identity()
    user = mongo.db.users.find_one({'username': current_user_identity.get('username')},
                                   {'_id': False, 'password': False})
    return jsonify({'message': 'OK', 'user': user}), 200


# auth - view all user information
@app.route('/users', methods=['GET'])
@jwt_required()
def get_all_users():
    # admin 유저 권한 확인
    current_user_identity = get_jwt_identity()
    if (not is_admin(current_user_identity)):
        return jsonify({'message': 'Not authorized'}), 403

    # _id 필드는 제외하고 결과를 가져옵니다.
    users = list(mongo.db.users.find(
        {}, {'_id': False, 'password': False}))
    return jsonify(users)


# auth - update user view auth list
@app.route('/user/<username>/update', methods=['PUT'])
@jwt_required()
def update_user_sites(username):
    current_user_identity = get_jwt_identity()
    if (not is_admin(current_user_identity)):
        return jsonify({'message': 'Not authorized'}), 403

    body = request.get_json(silent=True)
    if body is None:
        return jsonify({'message': 'Invalid request body'}), 400
    new_sites = body.get('sites', [])
    if not isinstance(new_sites, list) or not all(isinstance(s, str) for s in new_sites):
        return jsonify({'message': 'sites must be a list of strings'}), 400
    # remove duplicated sites
    new_sites = list(set(new_sites))

    # username을 기반으로 사용자의 site 권한을 업데이트합니다.
    result = mongo.db.users.update_one(
        {'username': username}, {'$set': {'sites': new_sites}})

    if result.matched_count == 0:
        return jsonify({'message': 'User not found'}), 404
    return jsonify({'message': 'Updated successfully'}), 200


# auth - users activate
@app.route('/user/<username>/activate', methods=['PUT'])
@jwt_required()
def activate_users(username):
    # admin 유저 권한 확인
    identity = get_jwt_identity()
    if (not is_admin(identity)):
        return jsonify({'message': 'Not authorized'}), 403

    result = mongo.db.users.update_one({'username': username},
                                       {'$set': {'activate': True}})

    if result.matched_count == 0:
        return jsonify({'message': 'User not found'}), 404

    return jsonify({'message': f'{username} activated'}), 200


# auth - users deactivate
@app.route('/user/<username>/deactivate', methods=['PUT'])
@jwt_required()
def deactivate_users(username):
    # admin 유저 권한 확인
    identity = get_jwt_identity()
    if (not is_admin(identity)):
        return jsonify({'message': 'Not authorized'}), 403

    result = mongo.db.users.update_one({'username': username},
                                       {'$set': {'activate': False}})

    if result.matched_count == 0:
        return jsonify({'message': 'User not found'}), 404

    return jsonify({'message': f'{username} deactivated'}), 200


# auth - delete user
@app.route('/user/<username>', methods=['DELETE'])
@jwt_required()
def delete_user(username):
    # admin 유저 권한 확인
    current_user_identity = get_jwt_identity()
    if (not is_admin(current_user_identity)):
        return jsonify({'message': 'Not authorized'}), 403

    result = mongo.db.users.delete_one({'username': username})

    if result.deleted_count == 0:
        return jsonify({'message': 'User not found'}), 404

    app.logger.warning(f"User deleted: {username} by {current_user_identity.get('username')}")
    return jsonify({'message': 'User successfully deleted'})


# auth/monitor - return all current service site name list
@app.route('/sites/all', methods=['GET'])
@jwt_required()
def all_sites_name_list():
    settings = load_settings()
    auth_sites = set(user_auth_sites(get_jwt_identity().get('username')))
    return jsonify([site for site in settings.keys() if site in auth_sites]), 200


# (Monitoring) Heartbeat check
@app.route('/heartbeat', methods=['POST'])
def heartbeat():
    # Retrieve JSON data from the request.
    data = request.get_json()

    # Validate the data
    if not data:
        return jsonify({'message': 'Invalid data'}), 400

    # Return a success response.
    return jsonify({'message': 'Heartbeat received successfully'}), 200


# (Monitoring) Information of all available sites
@app.route('/information/all', methods=['GET'])
@jwt_required()
def get_all_information():
    settings = load_settings()
    auth_sites = set(user_auth_sites(get_jwt_identity().get('username')))
    auth_settings = {key: settings[key] for key in settings.keys() if key in auth_sites}
    return jsonify(auth_settings)


# (Monitoring) Information of the site
@app.route('/information/<site>', methods=['GET'])
@jwt_required()
def get_site_information(site):
    settings = load_settings()
    identity = get_jwt_identity()

    if site not in settings:
        return jsonify({"message": f"Site '{site}' not found"}), 404

    if check_site_access(identity, site):
        return jsonify(settings[site])

    return jsonify({"message": f"Site '{site}' not found"}), 404


# (Monitoring) Thumbnails of Today's Photos from All Available Sites
@app.route('/thumbnails', methods=['GET'])
@jwt_required()
def get_thumbnails():
    # check user authorization
    auth_sites = set(user_auth_sites(get_jwt_identity().get('username')))
    thumbnail_files = glob('static/thumb_*.jpg')
    thumbnail_list = list()
    for file in thumbnail_files:
        site = os.path.basename(file).replace('thumb_', '').replace('.jpg', '')
        if site in auth_sites:
            thumbnail_url = os.path.basename(file)
            thumbnail_dict = {'site': site, 'url': thumbnail_url}
            thumbnail_list.append(thumbnail_dict)

    return jsonify(thumbnail_list), 200


# (Monitoring) Static Image Authorization Check
ALLOWED_PUBLIC_STATIC = {'monitor.jpg', 'no_image_today.jpg'}

@app.route('/static/<file>', methods=['GET'])
def get_thumbnail_image(file):
    if "thumb_" not in file:
        if file in ALLOWED_PUBLIC_STATIC:
            return send_from_directory('static', file)
        return jsonify({"message": "Not found."}), 404

    token = request.headers.get('Authorization')
    thumbnail_files = [os.path.basename(filename)
                       for filename in glob('static/thumb_*.jpg')]

    if token and file in thumbnail_files:
        try:
            verify_jwt_in_request()
        except Exception:
            return jsonify({"message": "Not found."}), 404
        identity = get_jwt_identity()
        site = file.replace('thumb_', '').split('.')[0]
        if check_site_access(identity, site):
            return send_from_directory('static', file)
    return jsonify({"message": "Not found."}), 404


# (Monitoring) Recent Images of a Site:
@app.route('/images/<site>/recent', methods=['GET'])
@jwt_required()
def recent_image(site):
    identity = get_jwt_identity()
    if not check_site_access(identity, site):
        return jsonify({"message": "Not found."}), 404

    # Define the path of the site
    site_path = os.path.join(os.getenv("IMAGES"), site)

    # Get the list of all date folders in the site
    date_folders = glob(os.path.join(site_path, '????-??-??'))

    # Filter out items that are not directories
    date_folders = [folder for folder in date_folders if os.path.isdir(folder)]

    if not date_folders:
        return jsonify({"message": "No images available"}), 404

    # Find the most recent date folder
    recent_date_folder = max(date_folders, key=os.path.basename)

    # Get the list of all image files in the recent date folder
    image_files = glob(os.path.join(recent_date_folder, '*.jpg'))

    if not image_files:
        return jsonify({"message": "No images available"}), 404

    # Find the most recent image file based on the file name
    recent_image_file = max(image_files, key=os.path.basename)

    # Open, resize, and save the image to a BytesIO object
    byte_io = io.BytesIO()
    with Image.open(recent_image_file) as image:
        image.thumbnail((1200, 1000))
        image.save(byte_io, 'JPEG')
    byte_io.seek(0)

    # Send the BytesIO object as a file
    return send_file(byte_io, mimetype='image/jpeg')


# (Monitoring) Selected Time-Specific Photo of the Site:
@app.route('/images/<site>/<date>/<photo>', methods=['GET'])
@jwt_required()
def get_single_image(site, date, photo):
    # Using send from directory
    # check user authorization
    if not check_site_access(get_jwt_identity(), site):
        return jsonify({"message": "Not found."}), 404
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', date):
        return jsonify({"message": "Not found."}), 404
    base_dir = os.path.realpath(os.path.join(os.getenv('IMAGES'), site))
    date_path = os.path.realpath(os.path.join(base_dir, date))
    if not date_path.startswith(base_dir + os.sep):
        return jsonify({"message": "Not found."}), 404
    if not re.fullmatch(r'[\w\-]+', photo):
        return jsonify({"message": "Not found."}), 404
    file = f'{photo}.jpg'
    return send_from_directory(date_path, file)

    # # Open, resize, and save the image to a BytesIO object
    # image = Image.open(os.path.join(os.getenv('IMAGES'),
    #                                 site,
    #                                 date,
    #                                 f'{photo}.jpg'))
    # image.thumbnail((1200, 1000))
    # byte_io = io.BytesIO()
    # image.save(byte_io, 'JPEG')
    # byte_io.seek(0)

    # # Send the BytesIO object as a file
    # return send_file(byte_io, mimetype='image/jpeg')


# (Monitoring) Video list of Selected Site:
@app.route('/video/<site>')
@jwt_required()
def get_daily_video_list(site):
    if not check_site_access(get_jwt_identity(), site):
        return jsonify({"message": "Not found."}), 404

    # Find video list
    try:
        all_files = os.listdir(os.path.join(os.getenv('IMAGES'), site, 'daily'))
    except Exception as e:
        app.logger.error(f'Video list error for site {site}: {e}')
        return jsonify([]), 200
    allowed_exts = {'.mp4', '.gif'}
    video_list = sorted(f for f in all_files if os.path.splitext(f)[1].lower() in allowed_exts)
    return jsonify(video_list), 200


# (Monitoring) Video of Selected Date:
@app.route('/video/<site>/<video>')
@jwt_required()
def get_daily_video(site, video):
    if not check_site_access(get_jwt_identity(), site):
        return jsonify({"message": "Not found."}), 404

    base_dir = os.path.realpath(os.path.join(os.getenv('IMAGES'), site, 'daily'))
    video_path = os.path.realpath(os.path.join(base_dir, video))
    if not video_path.startswith(base_dir + os.sep):
        return jsonify({"message": "Not found."}), 404

    ext = os.path.splitext(video_path)[1].lower()
    mime_map = {'.mp4': 'video/mp4', '.gif': 'image/gif'}
    if ext not in mime_map:
        return jsonify({"message": "Not found."}), 404

    if not os.path.isfile(video_path):
        return jsonify({"message": "daily video not found"}), 404

    return send_file(video_path, mimetype=mime_map[ext], as_attachment=False)


# (Monitoring) List of Date Folders:
@app.route('/images/<site>', methods=['GET'])
@jwt_required()
def get_site_image_list_by_date(site):
    if not check_site_access(get_jwt_identity(), site):
        return jsonify({"message": "Not found."}), 404

    # Define the path of the site
    site_path = os.path.join(os.getenv("IMAGES"), site)

    # Get the list of folders in the site
    folder_list = glob(os.path.join(site_path, '????-??-??'))

    # Filter out items that are not directories
    folder_list = [folder for folder in folder_list if os.path.isdir(folder)]

    # Extract the date part from each folder, sorted descending (newest first)
    date_list = sorted([os.path.basename(folder) for folder in folder_list], reverse=True)

    # Return the date list
    return jsonify(date_list), 200


# (Monitoring) List of Photos by Date:
@app.route('/images/<site>/<date>', methods=['GET'])
@jwt_required()
def get_site_image_list_in_date(site, date):
    if not check_site_access(get_jwt_identity(), site):
        return jsonify({"message": "Not found."}), 404
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', date):
        return jsonify({"message": "Not found."}), 404

    base_dir = os.path.realpath(os.path.join(os.getenv("IMAGES"), site))
    date_path = os.path.realpath(os.path.join(base_dir, date))
    if not date_path.startswith(base_dir + os.sep):
        return jsonify({"message": "Not found."}), 404

    # Get the list of image files in the date folder
    image_files = glob(os.path.join(date_path, '*.jpg'))

    # Extract the filename from each image file, sorted ascending
    image_list = sorted(os.path.basename(file) for file in image_files)

    # Return the image list
    return jsonify(image_list), 200


@app.route('/logs', methods=['GET'])
@jwt_required()
def get_logs():
    # admin 유저 권한 확인
    current_user_identity = get_jwt_identity()
    if (not is_admin(current_user_identity)):
        return jsonify({'message': 'Not authorized'}), 403

    log_type = request.args.get('type', 'info').lower()
    if log_type not in ['info', 'debug']:
        return jsonify({'message': "Invalid type. Use 'info' or 'debug'."}), 400

    try:
        page = int(request.args.get('page', 1))
        page_size = int(request.args.get('page_size', 50))
    except ValueError:
        return jsonify({'message': 'page and page_size must be integers.'}), 400

    if page < 1 or page_size < 1:
        return jsonify({'message': 'page and page_size must be greater than 0.'}), 400

    # 과도한 요청 제한
    page_size = min(page_size, 500)

    logs = read_paginated_logs(log_type, page, page_size)

    return jsonify({
        'type': log_type,
        'page': page,
        'page_size': page_size,
        'total': logs['total'],
        'total_pages': logs['total_pages'],
        'logs': logs['logs']
    }), 200


@app.route('/', methods=['GET'])
def hi():
    return jsonify('hi'), 200


if __name__ == '__main__':

    app.run('localhost', port=3000, debug=True, use_reloader=False)
