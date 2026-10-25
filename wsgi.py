"""
WSGI entrypoint for APAAR Nexus.
Suitable for production deployment with Gunicorn, Waitress, or Cloud Platforms (Render, Railway, etc.).
"""
import os
from app import create_app
from models import init_db

# Initialize application instance with environment config
app = create_app()

# Ensure database tables exist
init_db(app)

if __name__ == "__main__":
    # Fallback runner for local testing or container environments without Gunicorn
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
