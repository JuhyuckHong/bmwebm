import io
import os
from flask import Flask, request, jsonify, url_for, send_from_directory, send_file, make_response
from flask_jwt_extended import JWTManager, jwt_required, create_access_token
from flask_pymongo import PyMongo
from flask_cors import CORS
from datetime import timedelta, datetime
from dotenv import load_dotenv
from werkzeug.security import generate_password_hash, check_password_hash
import glob
from flask_apscheduler import APScheduler
import time
import glob
from PIL import Image

load_dotenv()

app = Flask(__name__)

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


@app.route('/signup', methods=['POST'])
def signup():
    data = request.get_json()
    if not data or 'username' not in data or 'password' not in data:
        return jsonify({'message': 'Invalid data'}), 400
    if mongo.db.users.find_one({'username': data['username']}):
        return jsonify({'message': 'User already exists'}), 400
    hashed_password = generate_password_hash(data['password'])
    mongo.db.users.insert_one(
        {'username': data['username'], 'password': hashed_password})
    return jsonify({'message': 'User created'}), 201


@app.route('/login', methods=['POST'])
def login():
    data = request.get_json()
    if not data or 'username' not in data or 'password' not in data:
        return jsonify({'message': 'Invalid data'}), 400
    user = mongo.db.users.find_one(
        {'username': data['username'], 'password': data['password']})
    if not user:
        return jsonify({'message': 'Invalid credentials'}), 400
    access_token = create_access_token(identity={'username': data['username']})
    return jsonify({'access_token': access_token,
                    'message': 'Login success.'}), 200


@app.route('/auth', methods=['GET'])
@jwt_required()
def auth():
    return jsonify({'message': 'OK'}), 200


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

    # 캐시 제어 헤더 설정
    response = make_response(jsonify({'thumbnail_urls': thumbnail_list}), 200)
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'

    return response


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

        # Process all the folders
        for folder_path in subfolders:
            folder_name = os.path.basename(folder_path)
            # Try to find the folder for today's date
            image_folder = os.path.join(
                os.getenv("IMAGES"), folder_name, today)
            print(image_folder)
            if not os.path.exists(image_folder):
                # If the folder does not exist, create an empty thumbnail
                img = Image.new('RGB', (200, 200), color=(73, 109, 137))
                thumbnail_path = os.path.join(
                    'static', f'thumb_{folder_name}.jpg')
                img.save(thumbnail_path)
                continue

            # If the folder exists, find the latest image file in the folder
            image_files = glob.glob(os.path.join(image_folder, '*.jpg'))
            latest_image_file = max(image_files)

            # Generate the thumbnail of the latest image
            with Image.open(latest_image_file) as img:
                img.thumbnail((300, 300))
                thumbnail_path = os.path.join(
                    'static', f'thumb_{folder_name}.jpg')
                img.save(thumbnail_path)

    app.run('localhost', port=3000, debug=True, use_reloader=False)
