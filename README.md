# COP Agona Ahanta — Church Management System

## Project Structure

```
cop_agona_ahanta/
├── app.py                      # Flask application factory
├── config.py                   # Configuration (env-based)
├── models.py                   # SQLAlchemy models (Global + Tenant)
├── requirements.txt            # Python dependencies
├── .env.example                # Environment variable template
├── manifest.json               # PWA manifest
│
├── blueprints/
│   ├── __init__.py
│   ├── admin/
│   │   ├── __init__.py
│   │   └── routes.py           # Admin dashboard routes
│   └── api/
│       ├── __init__.py
│       └── routes.py           # REST API endpoints
│
├── utils/
│   ├── __init__.py
│   ├── r2_storage.py           # Cloudflare R2 (boto3) utilities
│   ├── db_router.py            # Tenant DB download/cache/sync logic
│   └── auth.py                 # JWT / session helpers
│
├── static/
│   ├── css/
│   │   ├── main.css            # Global styles + CSS variables
│   │   ├── auth.css            # Login / signup pages
│   │   └── app.css             # Feed, stories, nav styles
│   ├── js/
│   │   ├── app.js              # Main app logic
│   │   ├── feed.js             # Feed rendering
│   │   ├── stories.js          # Stories bar
│   │   ├── upload.js           # Direct-to-R2 presigned upload
│   │   └── sw.js               # Service Worker (PWA)
│   └── icons/                  # PWA icons
│
└── templates/
    ├── base.html               # Base layout with PWA meta
    ├── auth/
    │   ├── login.html
    │   └── signup.html
    ├── app/
    │   ├── feed.html           # Main Instagram-style feed
    │   ├── search.html
    │   ├── reels.html
    │   ├── give.html
    │   └── profile.html
    └── admin/
        └── dashboard.html
```

## Quick Start

```bash
pip install -r requirements.txt
cp .env.example .env   # Fill in your credentials
flask db init && flask db migrate && flask db upgrade
flask run
```
