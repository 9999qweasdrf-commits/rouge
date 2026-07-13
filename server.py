# 🚨 必ず一番上に書く（他のインポートより前）
from gevent import monkey
monkey.patch_all()
import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))  # ← これを追加
from __init__ import create_app, socketio

app = create_app()

if __name__ == "__main__":
    socketio.run(
        app,
        host="0.0.0.0",
        port=5001,
        debug=False,
        use_reloader=False
    )