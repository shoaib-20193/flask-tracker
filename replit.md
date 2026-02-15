# Visitor Tracker

## Overview
A lightweight visitor tracking and analytics application built with Flask and SQLite. It tracks visitors, devices, sessions, and events via a JavaScript tracking pixel, and provides a simple analytics dashboard.

## Project Architecture
- **Language**: Python 3.11
- **Framework**: Flask 2.3.3
- **Database**: SQLite (tracker.db)
- **Production Server**: gunicorn 20.1.0

## Key Files
- `app.py` - Main application with all routes, database logic, and analytics
- `templates/dashboard.html` - Dashboard template showing analytics stats
- `tracker.db` - SQLite database (auto-created on startup)

## Routes
- `GET /r/<track_id>` - Tracking pixel page that collects visitor data
- `POST /collect` - Event collection endpoint (receives beacon data)
- `GET /dashboard/<track_id>` - Analytics dashboard for a given track ID

## Running
- Development: `python app.py` (runs on 0.0.0.0:5000)
- Production: `gunicorn --bind=0.0.0.0:5000 app:app`

## Recent Changes
- Configured to run on 0.0.0.0:5000 for Replit environment
- Fixed dashboard template bug (removed reference to non-existent `stats.countries`)
