import os
from flask import Flask, request, jsonify, url_for
from flask_jwt_extended import JWTManager, jwt_required, create_access_token
from flask_pymongo import PyMongo
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
    access_token = create_access_token(identity=data['username'])
    return jsonify({'access_token': access_token}), 200


@app.route('/thumbnails', methods=['GET'])
@jwt_required()
def get_thumbnails():
    thumbnail_files = glob.glob('static/thumb_*.jpg')
    thumbnail_urls = [url_for('static', filename=os.path.basename(file))
                      for file in thumbnail_files]
    return jsonify({'thumbnail_urls': thumbnail_urls}), 200


@app.route('/recent-image', methods=['GET'])
@jwt_required()
def get_recent_image():
    # get the most recent image
    pass


@app.route('/images', methods=['GET'])
@jwt_required()
def get_images():
    # get a list of all images
    pass


if __name__ == '__main__':
    scheduler = APScheduler()
    scheduler.init_app(app)
    scheduler.start()

    @scheduler.task('interval', id='do_job_1', seconds=10*60, misfire_grace_time=10)
    def job1():
        # Generate today's date string
        today = datetime.now().strftime('%Y-%m-%d')

        # Get all subfolders in the static directory
        subfolders = [f for f in glob.glob('static/*') if os.path.isdir(f)]

        # Process all the folders
        for folder_path in subfolders:
            folder_name = os.path.basename(folder_path)
            # Try to find the folder for today's date
            image_folder = os.path.join('static', folder_name, today)
            print(image_folder)
            if not os.path.exists(image_folder):
                # If the folder does not exist, create an empty thumbnail
                img = Image.new('RGB', (128, 128), color=(73, 109, 137))
                thumbnail_path = os.path.join(
                    'static', folder_name, f'thumbnail_{folder_name}.jpg')
                img.save(thumbnail_path)
                continue

            # If the folder exists, find the latest image file in the folder
            image_files = glob.glob(os.path.join(image_folder, '*.jpg'))
            latest_image_file = max(image_files)

            # Generate the thumbnail of the latest image
            with Image.open(latest_image_file) as img:
                img.thumbnail((128, 128))
                thumbnail_path = os.path.join(
                    'static', f'thumbnail_{folder_name}.jpg')
                img.save(thumbnail_path)

    app.run('localhost', port=3000, debug=True, use_reloader=False)
