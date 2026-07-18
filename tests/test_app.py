"""
Test suite for COP Agona Ahanta ChMS.
Run with: python -m pytest tests/
"""

import os
import tempfile
import sqlite3

import pytest

from app.models import db, GlobalUser, Church, init_tenant_db, get_tenant_conn


class TestAuthRoutes:
    """Tests for authentication routes."""
    
    def test_login_page_loads(self, client):
        """Test that login page renders."""
        response = client.get('/auth/login')
        assert response.status_code == 200
        assert b'Sign In' in response.data
    
    def test_signup_page_loads(self, client):
        """Test that signup page renders."""
        response = client.get('/auth/signup')
        assert response.status_code == 200
        assert b'Create your account' in response.data
    
    def test_login_missing_credentials(self, client):
        """Test login with missing credentials."""
        response = client.post('/auth/login', json={
            'identifier': '',
            'password': ''
        })
        assert response.status_code == 400
    
    def test_signup_missing_fields(self, client):
        """Test signup with missing fields."""
        response = client.post('/auth/signup', json={
            'username': '',
            'email': '',
            'password': '',
            'confirm': ''
        })
        assert response.status_code == 400


class TestAPIRoutes:
    """Tests for API routes."""
    
    def test_feed_requires_auth(self, client):
        """Test that feed endpoint requires authentication."""
        response = client.get('/api/feed')
        assert response.status_code == 401
    
    def test_events_requires_auth(self, client):
        """Test that events endpoint requires authentication."""
        response = client.get('/api/events')
        assert response.status_code == 401


class TestModels:
    """Tests for database models."""
    
    def test_church_creation(self, app):
        """Test Church model creation."""
        with app.app_context():
            church = Church.query.filter_by(slug='test-church').first()
            assert church is not None
            assert church.name == 'Test Church'
    
    def test_tenant_db_init(self, app):
        """Test tenant database initialization."""
        with app.app_context():
            tenant_path = os.path.join(app.instance_path, 'tenants', 'test-church.db')
            init_tenant_db(tenant_path)
            
            # Check file was created
            assert os.path.exists(tenant_path)
            
            # Check tables exist
            conn = get_tenant_conn(tenant_path)
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='members'"
            )
            assert cursor.fetchone() is not None
            conn.close()
            
            # Cleanup
            os.unlink(tenant_path)


class TestRateLimiting:
    """Tests for rate limiting."""
    
    def test_rate_limit_allows_normal_requests(self, client):
        """Test that rate limiting allows normal request volume."""
        # Make a few requests - should all succeed
        for i in range(3):
            response = client.get('/auth/login')
            assert response.status_code == 200
    
    def test_rate_limit_blocks_excessive_requests(self, client):
        """Test that rate limiting blocks after threshold."""
        # Make more than 5 requests rapidly
        for i in range(6):
            response = client.post('/auth/login', json={
                'identifier': f'user{i}',
                'password': 'password123'
            })
            if i >= 5:
                # After 5 attempts, should be rate limited
                assert response.status_code == 429


if __name__ == '__main__':
    pytest.main([__file__, '-v'])