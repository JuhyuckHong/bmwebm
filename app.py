import os
from flask import Flask, request, jsonify, url_for
from flask_jwt_extended import JWTManager, jwt_required, create_access_token
from flask_pymongo import PyMongo
from datetime import timedelta
from dotenv import load_dotenv
from werkzeug.security import generate_password_hash, check_password_hash
import glob

load_dotenv()

app = Flask(__name__)

app.config['MONGO_URI'] = os.getenv('MONGO_URI')
app.config['JWT_SECRET_KEY'] = os.getenv('JWT_SECRET_KEY')
app.config['JWT_ACCESS_TOKEN_EXPIRES'] = timedelta(days=int(os.getenv('JWT_EXP_DAY')))


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
    app.run('localhost', port=3000, debug=True)
