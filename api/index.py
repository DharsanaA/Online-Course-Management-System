"""Vercel Serverless Function entry point for Flask."""
import os
import sys

# Ensure application root directory is on the Python module search path
app_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if app_dir not in sys.path:
    sys.path.insert(0, app_dir)

from app import app
