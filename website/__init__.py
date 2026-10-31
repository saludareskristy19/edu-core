import os
from flask import Flask, send_from_directory

def create_app():
    app = Flask(__name__)  

    @app.route('/')
    def home():
        folder_path = os.path.join(app.root_path, "templates")
        return send_from_directory(folder_path, "index.html")

    return app