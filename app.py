import io
import os
from flask import Flask, request, jsonify, url_for, send_from_directory, send_file, make_response
from flask_jwt_extended import JWTManager, jwt_required, create_access_token, get_jwt_identity
from flask_pymongo import PyMongo
from flask_cors import CORS
from datetime import timedelta, datetime
from dotenv import load_dotenv
from werkzeug.security import generate_password_hash, check_password_hash
import glob
from flask_apscheduler import APScheduler
import logging
import glob
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
scheduler = APScheduler()
scheduler.init_app(app)
scheduler.start()


@scheduler.task('interval',
                id='making_thumbnails',
                seconds=int(os.getenv('THUMBNAIL_INTERVAL')),
                misfire_grace_time=10)
def making_thumbnails():
    # Generate today's date string
    today = datetime.now().strftime('%Y-%m-%d')

    # Get all subfolders in the static directory
    subfolders = [f for f in glob.glob(
        f'{os.getenv("IMAGES")}/*') if os.path.isdir(f)]

    # Get a list of existing thumbnail files
    existing_thumbnails = [f for f in glob.glob('static/thumb_*.jpg')]

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
        image_files = glob.glob(os.path.join(image_folder, '*.jpg'))
        latest_image_file = max(image_files)

        # Generate the thumbnail of the latest image
        with Image.open(latest_image_file) as img:
            img.thumbnail((300, 200))
            thumbnail_path = os.path.join(
                'static', f'thumb_{folder_name}.jpg')
            img.save(thumbnail_path)
            thumbnail_made_site.append(folder_name)

    app.logger.info(f'Sites removed: {remove_site}')
    app.logger.info(f'Sites with no photos yet: {no_photo_yet_site}')
    app.logger.info(f'Sites with thumbnails created: {thumbnail_made_site}')


# auth - signup
@app.route('/signup', methods=['POST'])
def signup():
    data = request.get_json()
    print(data)
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
    # admin check
    identity = get_jwt_identity()
    print(identity)
    if identity["class"] == identity["username"]:
        users = mongo.db.pending_users.find()
    else:
        users = []
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
def approve_user(username):
    user = mongo.db.pending_users.find_one({'username': username})
    if not user:
        return jsonify({'message': 'User not found in pending list'}), 404
    user['class'] = 'user'
    mongo.db.users.insert_one(user)
    mongo.db.pending_users.delete_one({'username': username})
    return jsonify({'message': f'User {username} approved and added to users'}), 200


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
    return jsonify({'message': 'OK', 'identity': current_user_identity}), 200


# (Monitoring) Thumbnails of Today's Photos from All Available Sites
@app.route('/thumbnails', methods=['GET'])
@jwt_required()
def get_thumbnails():
    thumbnail_files = glob.glob('static/thumb_*.jpg')
    thumbnail_list = list()
    for file in thumbnail_files:
        site = os.path.basename(file).replace('thumb_', '').replace('.jpg', '')
        thumbnail_url = url_for('static', filename=os.path.basename(file))
        thumbnail_dict = {'site': site, 'url': thumbnail_url}
        thumbnail_list.append(thumbnail_dict)

    return jsonify({'thumbnail_urls': thumbnail_list}), 200


# (Monitoring) Recent Images of a Site:
@app.route('/images/<site>/recent', methods=['GET'])
@jwt_required()
def recent_image(site):
    # Define the path of the site
    site_path = os.path.join(os.getenv("IMAGES"), site)

    # Get the list of all date folders in the site
    date_folders = glob.glob(os.path.join(site_path, '????-??-??'))

    # Filter out items that are not directories
    date_folders = [folder for folder in date_folders if os.path.isdir(folder)]

    # Find the most recent date folder
    recent_date_folder = max(date_folders, key=os.path.basename)

    # Get the list of all image files in the recent date folder
    image_files = glob.glob(os.path.join(recent_date_folder, '*.jpg'))

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
@app.route('/images/<site>/<date>/<photo>')
@jwt_required()
def get_single_image(site, date, photo):
    # Open, resize, and save the image to a BytesIO object
    image = Image.open(os.path.join(os.getenv('IMAGES'),
                                    site,
                                    date,
                                    f'{photo}.jpg'))
    image.thumbnail((1200, 1000))
    byte_io = io.BytesIO()
    image.save(byte_io, 'JPEG')
    byte_io.seek(0)

    # Send the BytesIO object as a file
    return send_file(byte_io, mimetype='image/jpeg')


# (Monitoring) List of Date Folders:
@app.route('/images/<site>', methods=['GET'])
@jwt_required()
def get_site_image_list_by_date(site):
    # Define the path of the site
    site_path = os.path.join(os.getenv("IMAGES"), site)

    # Get the list of folders in the site
    folder_list = glob.glob(os.path.join(site_path, '????-??-??'))

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
    image_files = glob.glob(os.path.join(date_path, '*.jpg'))

    # Extract the filename from each image file
    image_list = [os.path.basename(file) for file in image_files]

    # Return the image list
    return jsonify(image_list), 200


if __name__ == '__main__':

    app.run('localhost', port=3000, debug=True, use_reloader=False)
