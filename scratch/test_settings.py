import os
from backend.modules.settings.routes import get_value
from backend.modules.games.client import DEFAULT_SERVER_URL
from backend.modules.games.server_auth import _http_base

print("DEFAULT_SERVER_URL:", DEFAULT_SERVER_URL)
print("games.serverUrl setting:", get_value("games.serverUrl", DEFAULT_SERVER_URL))
print("_http_base():", _http_base())
