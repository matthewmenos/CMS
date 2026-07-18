"""
Pytest configuration for COP Agona Ahanta ChMS tests.
"""

import pytest
import os
import sys

# Add the project root to the path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture
def app():
    """Create a test application instance."""
    from app import create_app
    from app.models import db, Church
    
    test_app = create_app()
    test_app.config.update({
        'TESTING': True,
        'SQLALCHEMY_DATABASE_URI': 'sqlite:///:memory:',
        'WTF_CSRF_ENABLED': False,
    })
    
    with test_app.app_context():
        db.create_all()
        # Create a test church
        church = Church(
            name='Test Church',
            slug='test-church',
            db_key='dbs/test-church.db'
        )
        db.session.add(church)
        db.session.commit()
    
    yield test_app


@pytest.fixture
def client(app):
    """Create a test client."""
    return app.test_client()


@pytest.fixture
def runner(app):
    """Create a test CLI runner."""
    return app.test_cli_runner()