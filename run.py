from app import create_app
import sys
import os

app = create_app()

if __name__ == "__main__":
    print(sys.version)
    debug = os.environ.get('FLASK_DEBUG', 'false').lower() == 'true'
    app.run(host="0.0.0.0", port=5000, debug=debug)
