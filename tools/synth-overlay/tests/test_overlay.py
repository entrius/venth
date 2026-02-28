import pytest
import sys
import os
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from api import app

client = TestClient(app)

def test_daily_edge_endpoint():
    response = client.get("/api/edge?market_type=daily")
    assert response.status_code == 200
    data = response.json()
    assert "edge_up" in data
    assert "edge_down" in data
    assert "synth_up" in data
    assert "poly_up" in data
    assert data["decision"] in ["BUY YES", "BUY NO", "NO TRADE"]

def test_hourly_edge_endpoint():
    response = client.get("/api/edge?market_type=hourly")
    assert response.status_code == 200
    data = response.json()
    assert "edge_up" in data
    assert "edge_down" in data
    assert "synth_up" in data
    assert "poly_up" in data
    assert data["decision"] in ["BUY YES", "BUY NO", "NO TRADE"]

def test_invalid_market_type():
    response = client.get("/api/edge?market_type=invalid")
    assert response.status_code == 200
    data = response.json()
    assert "error" in data
