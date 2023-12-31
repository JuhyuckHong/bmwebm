import io
import os
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
from PIL import Image

load_dotenv()

app = Flask(__name__)

# debug log setting
debug_log_handler = logging.FileHandler('debug.log')
debug_log_handler.setLevel(logging.DEBUG)
debug_log_format = logging.Formatter('%(asctime)s %(levelname)s: %(message)s')
debug_log_handler.setFormatter(debug_log_format)

# info log setting
info_log_handler = logging.FileHandler('info.log')
info_log_handler.setLevel(logging.INFO)
info_log_format = logging.Formatter('%(asctime)s %(levelname)s: %(message)s')
info_log_handler.setFormatter(info_log_format)

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
scheduler.start()


@scheduler.scheduled_job('cron',
                         id='making_thumbnails',
                         hour='7-23',
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
    for existing_thumbnail in existing_thumbnails:
        thumbnail_name = os.path.basename(existing_thumbnail)
        site = os.path.splitext(thumbnail_name)[0].replace('thumb_', '')
        site_folder_list = [os.path.basename(
            subfolder) for subfolder in subfolders]
        if site not in site_folder_list:
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
                         hour='7-23',
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
            with open(file_path, 'r') as f:
                for line in f:
                    key, value = line.strip().split('=')
                    site_settings[key] = value.strip('""')
            # Calculate Shooting Count of today
            start_minutes = int(
                site_settings["time_start"][:2]) * 60 + int(site_settings["time_start"][2:])
            end_minutes = int(
                site_settings["time_end"][:2]) * 60 + int(site_settings["time_end"][2:])
            interval_minutes = int(site_settings["time_interval"])
            site_settings["shooting_count"] = (
                end_minutes - start_minutes) // interval_minutes + 1
            # Calculate Shooting Count of current time
            current_time = datetime.now()
            current_minutes = min(current_time.hour *
                                  60 + current_time.minute, end_minutes)
            site_settings["shooting_count_till_now"] = (
                current_minutes - start_minutes) // interval_minutes + 1

        if today in folders:
            # Counting Photo list of today
            photos = os.listdir(os.path.join(site, today))
            site_settings['photos_count'] = len(photos)
            site_settings['recent_photo'] = photos[-1]
            created_setting_site.append(site.replace('images/', ' '))
        else:
            site_settings['photos_count'] = 0
            site_settings['recent_photo'] = "No Photo Available"
            no_photos_today_site.append(site.replace('images/', ' '))

        settings[site_name] = site_settings

    # Check ssh connection
    command = ["ssh", os.getenv("SSH_HOST"), '-p',
               os.getenv("SSH_PORT"), os.getenv("SSH_COMMAND")]
    result = subprocess.run(
        command, capture_output=True, text=True, check=True).stdout
    result = [int(site.split('127.0.0.1:')[1].split(' ')[0].replace('22', ''))
              for site in result.split('\n')[:-1]]

    # Get monitor image from server
    command_img = ["scp", "-P", os.getenv("SSH_PORT"),
                   os.getenv("SSH_HOST")+os.getenv("SCP_COMMAND"), "./static/"]
    subprocess.run(command_img, text=True, check=True)

    for site_name, setting in settings.items():
        if int(setting['device_number'].replace('bmotion', '')) in result:
            settings[site_name]['ssh'] = True
        else:
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
    with open('settings.json', 'r') as f:
        settings = json.load(f)
    return settings


def user_auth_sites(username):
    data = mongo.db.users.find_one(
        {'username': username}, {"sites": 1, "_id": 0})
    return data.get("sites")


# auth - signup
@app.route('/signup', methods=['POST'])
def signup():
    data = request.get_json()
    if not data or 'username' not in data or 'password' not in data:
        return jsonify({'message': 'Invalid data'}), 400
    if mongo.db.users.find_one({'username': data['username']}) or mongo.db.pending_users.find_one({'username': data['username']}):
        return jsonify({'message': 'User already exists'}), 400
    hashed_password = generate_password_hash(data['password'])
    mongo.db.pending_users.insert_one(
        {'username': data['username'], 'password': hashed_password, 'code': data['code']})
    return jsonify({'message': 'User registered, awaiting approval'}), 201


# auth - list of pending user
@app.route('/users/pending', methods=['GET'])
@jwt_required()
def list_pending_users():
    # admin 유저 권한 확인
    current_user_identity = get_jwt_identity()
    if (current_user_identity.get("username") != current_user_identity.get("class")):
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
    if (current_user_identity.get("username") != current_user_identity.get("class")):
        return jsonify({'message': 'Not authorized'}), 403

    user = mongo.db.pending_users.find_one({'username': username})
    if not user:
        return jsonify({'message': 'User not found in pending list'}), 404
    user['class'] = 'user'
    user['sites'] = []
    user['activate'] = True
    mongo.db.users.insert_one(user)
    mongo.db.pending_users.delete_one({'username': username})
    return jsonify({'message': f'User {username} approved and added to users'}), 200


# auth admin - decline user
@app.route('/decline/<username>', methods=['PUT'])
@jwt_required()
def decline_user(username):
    # admin 유저 권한 확인
    current_user_identity = get_jwt_identity()
    if (current_user_identity.get("username") != current_user_identity.get("class")):
        return jsonify({'message': 'Not authorized'}), 403

    user = mongo.db.pending_users.find_one({'username': username})
    if not user:
        return jsonify({'message': 'User not found in pending list'}), 404
    mongo.db.pending_users.delete_one({'username': username})
    return jsonify({'message': f'User {username} declined'}), 200


# auth - login
@app.route('/login', methods=['POST'])
def login():
    data = request.get_json()
    if not data or 'username' not in data or 'password' not in data:
        return jsonify({'message': 'Invalid data'}), 400
    user = mongo.db.users.find_one({'username': data['username']})
    if not user or not check_password_hash(user['password'], data['password']):
        return jsonify({'message': 'Invalid credentials'}), 400
    access_token = create_access_token(
        identity={'username': user['username'], 'class': user['class']})
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
    if (current_user_identity.get("username") != current_user_identity.get("class")):
        return jsonify({'message': 'Not authorized'}), 403

    # _id 필드는 제외하고 결과를 가져옵니다.
    users = list(mongo.db.users.find(
        {}, {'_id': False, 'password': False}))
    return jsonify(users)


# auth - update user view auth list
@app.route('/user/<username>/update', methods=['PUT'])
@jwt_required()
def update_user_sites(username):
    new_sites = request.json.get('sites', [])
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
    if (identity.get("username") != identity.get("class")):
        return jsonify({'message': 'Not authorized'}), 403

    result = mongo.db.users.update_one({'username': username},
                                       {'$set': {'activate': True}})

    if result == 0:
        return jsonify({'message': 'No changes were made'}), 404

    return jsonify({'message': f'{username} activated'}), 200


# auth - users deactivate
@app.route('/user/<username>/deactivate', methods=['PUT'])
@jwt_required()
def deactivate_users(username):
    # admin 유저 권한 확인
    identity = get_jwt_identity()
    if (identity.get("username") != identity.get("class")):
        return jsonify({'message': 'Not authorized'}), 403

    result = mongo.db.users.update_one({'username': username},
                                       {'$set': {'activate': False}})

    if result == 0:
        return jsonify({'message': 'No changes were made'}), 404

    return jsonify({'message': f'{username} deactivated'}), 200


# auth - delete user
@app.route('/user/<username>', methods=['DELETE'])
@jwt_required()
def delete_user(username):
    # admin 유저 권한 확인
    current_user_identity = get_jwt_identity()
    if (current_user_identity.get("username") != current_user_identity.get("class")):
        return jsonify({'message': 'Not authorized'}), 403

    result = mongo.db.users.delete_one({'username': username})

    if result.deleted_count == 0:
        return jsonify({'message': 'User not found'}), 404

    return jsonify({'message': 'User successfully deleted'})


# auth/monitor - return all current service site name list
@app.route('/sites/all', methods=['GET'])
@jwt_required()
def all_sites_name_list():
    settings = load_settings()
    return jsonify(list(settings.keys())), 200


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
    # get settings
    settings = load_settings()

    # for admin
    current_user_identity = get_jwt_identity()
    if (current_user_identity.get("username") == current_user_identity.get("class")):
        return jsonify(settings)

    # for user
    auth_sites = user_auth_sites(current_user_identity.get('username'))
    auth_settings = {key: settings[key]
                     for key in settings.keys() if key in auth_sites}
    return jsonify(auth_settings)


# (Monitoring) Information of the site
@app.route('/information/<site>', methods=['GET'])
@jwt_required()
def get_site_information(site):
    settings = load_settings()
    # check user authorization
    auth_sites = user_auth_sites(get_jwt_identity().get('username'))
    if site in settings and site in auth_sites:
        return jsonify(settings[site])
    else:
        return jsonify({"message": f"Site '{site}' not found"}), 404


# (Monitoring) Thumbnails of Today's Photos from All Available Sites
@app.route('/thumbnails', methods=['GET'])
@jwt_required()
def get_thumbnails():
    # check user authorization
    auth_sites = user_auth_sites(get_jwt_identity().get('username'))
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
@app.route('/static/<file>', methods=['GET'])
def get_thumbnail_image(file):
    if "thumb_" not in file:
        return send_from_directory('static', file)

    token = request.headers.get('Authorization')
    thumbnail_files = [os.path.basename(filename)
                       for filename in glob('static/thumb_*.jpg')]

    if token and file in thumbnail_files:
        _, identity = verify_jwt_in_request()
        auth_sites = user_auth_sites(identity.get('sub').get('username'))
        if file.replace('thumb_', '').split('.')[0] in auth_sites:
            return send_from_directory('static', file)
        else:
            return jsonify({"message": "Access denied."}), 403
    else:
        return jsonify({"message": "Access denied."}), 403


# (Monitoring) Recent Images of a Site:
@app.route('/images/<site>/recent', methods=['GET'])
@jwt_required()
def recent_image(site):
    # Define the path of the site
    site_path = os.path.join(os.getenv("IMAGES"), site)

    # Get the list of all date folders in the site
    date_folders = glob(os.path.join(site_path, '????-??-??'))

    # Filter out items that are not directories
    date_folders = [folder for folder in date_folders if os.path.isdir(folder)]

    # Find the most recent date folder
    recent_date_folder = max(date_folders, key=os.path.basename)

    # Get the list of all image files in the recent date folder
    image_files = glob(os.path.join(recent_date_folder, '*.jpg'))

    # Find the most recent image file based on the file name
    recent_image_file = max(image_files, key=os.path.basename)

    # Open, resize, and save the image to a BytesIO object
    image = Image.open(recent_image_file)
    image.thumbnail((1200, 1000))
    byte_io = io.BytesIO()
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
    auth_sites = user_auth_sites(get_jwt_identity().get('username'))
    if site in auth_sites:
        path = os.path.join(os.getenv('IMAGES'), site, date)
        file = f'{photo}.jpg'
        return send_from_directory(path, file)
    else:
        return send_from_directory('static', 'no_image_today.jpg')

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
    # Find video list
    try:
        video_list = os.listdir(os.path.join('images', site, 'daily'))
    except Exception as e:
        return jsonify([]), 200
    # Send video list
    return jsonify(video_list), 200


# (Monitoring) Video of Selected Date:
@app.route('/video/<site>/<video>')
@jwt_required()
def get_daily_video(site, video):
    # Make file name and check file exist
    video = os.path.join(os.getenv('IMAGES'),
                         site,
                         'daily',
                         video)
    if not os.path.exists(video):
        return jsonify({"message": "daily video not found"}), 404

    # Send video by blob
    return send_file(video, mimetype='video/mp4', as_attachment=False)


# (Monitoring) List of Date Folders:
@app.route('/images/<site>', methods=['GET'])
@jwt_required()
def get_site_image_list_by_date(site):
    # Define the path of the site
    site_path = os.path.join(os.getenv("IMAGES"), site)

    # Get the list of folders in the site
    folder_list = glob(os.path.join(site_path, '????-??-??'))

    # Filter out items that are not directories
    folder_list = [folder for folder in folder_list if os.path.isdir(folder)]

    # Extract the date part from each folder
    date_list = [os.path.basename(folder) for folder in folder_list]

    # Return the date list
    return jsonify(date_list), 200


# (Monitoring) List of Photos by Date:
@app.route('/images/<site>/<date>', methods=['GET'])
@jwt_required()
def get_site_image_list_in_date(site, date):
    # Define the path of the site and the date
    date_path = os.path.join(os.getenv("IMAGES"), site, date)

    # Get the list of image files in the date folder
    image_files = glob(os.path.join(date_path, '*.jpg'))

    # Extract the filename from each image file
    image_list = [os.path.basename(file) for file in image_files]

    # Return the image list
    return jsonify(image_list), 200


@app.route('/', methods=['GET'])
def hi():
    return jsonify('hi'), 200


if __name__ == '__main__':

    app.run('localhost', port=3000, debug=True, use_reloader=False)
