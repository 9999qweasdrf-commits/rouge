from flask import Flask, render_template, jsonify, request
from flask_socketio import SocketIO
from werkzeug.middleware.proxy_fix import ProxyFix
import importlib
import pkgutil

socketio = SocketIO(
    cors_allowed_origins="*",
    async_mode="gevent",
    logger=True,
    engineio_logger=True,
)

APPS = []

def create_app():
    app = Flask(__name__)

    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1)

    import games as games_pkg

    for _, module_name, _ in pkgutil.iter_modules(games_pkg.__path__):
        module = importlib.import_module(f"games.{module_name}")

        if hasattr(module, "bp"):
            app.register_blueprint(module.bp)
            
            info = getattr(module, "APP_INFO", {}).copy()
            info["_raw_prefix"] = module.bp.url_prefix
            APPS.append(info)
    
    # ← 重要：socketio.init_app() を先に呼ぶ
    socketio.init_app(app)
    
    # ← その後で init_app() を呼ぶ
    for _, module_name, _ in pkgutil.iter_modules(games_pkg.__path__):
        module = importlib.import_module(f"games.{module_name}")
        if hasattr(module, "init_app"):
            module.init_app(app)

    @app.after_request
    def allow_iframe(response):
        response.headers.remove('X-Frame-Options')
        response.headers['Content-Security-Policy'] = "frame-ancestors 'self' https://play.tacz.f5.si"
        return response

    @app.route("/")
    def desktop():
        return render_template("desktop.html")

    @app.route("/api/apps")
    def api_apps():
        dynamic_apps = []
        for app_info in APPS:
            copied = app_info.copy()
            
            base_url = request.host_url.rstrip('/')
            prefix = copied["_raw_prefix"].lstrip('/')
            
            copied["url"] = f"{base_url}/{prefix}"
            dynamic_apps.append(copied)
            
        return jsonify(dynamic_apps)

    return app