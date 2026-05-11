from flask import Flask, jsonify
from threading import Thread
from utils.api import api, set_bot

app = Flask('')
app.register_blueprint(api)


@app.route('/')
def home():
    return jsonify({"status": "Leviathan is awake!"})


def run():
    app.run(host='0.0.0.0', port=5000)


def keep_alive(bot=None):
    if bot is not None:
        set_bot(bot)
    t = Thread(target=run)
    t.daemon = True
    t.start()
