from flask import Flask

flask_app = Flask(__name__)

@flask_app.before_first_request
def show_me():
    print("✅ before_first_request triggered")

@flask_app.route("/")
def hello():
    return "Hello Trash Panda"

if __name__ == "__main__":
    flask_app.run(debug=True)