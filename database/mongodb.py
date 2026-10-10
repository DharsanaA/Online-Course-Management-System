"""MongoDB connection handling (PyMongo). One client is created in the app factory."""
from flask import current_app
from pymongo import MongoClient


def init_db(app):
    """Connect to MongoDB and store the database handle on the app."""
    client = MongoClient(app.config["MONGO_URI"], serverSelectionTimeoutMS=5000)
    client.admin.command("ping")  
    db = client[app.config["MONGO_DB_NAME"]]
    app.extensions["mongo_client"] = client
    app.extensions["mongo_db"] = db
    return db


def get_db():
    """Return the PyMongo database handle for the current app."""
    return current_app.extensions["mongo_db"]
