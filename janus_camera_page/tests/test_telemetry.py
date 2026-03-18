"""Tests for the telemetry ingestion endpoint."""
from __future__ import annotations

import pytest


class TestTelemetryEndpoint:
    """POST /telemetry — client WebRTC stats ingestion."""

    @pytest.mark.asyncio
    async def test_ice_connected_returns_204(self, client):
        resp = await client.post("/telemetry", json={
            "event": "ice_connected",
            "session_id": "test-session-1",
            "camera": "color",
            "ice_connect_ms": 1200.5,
            "time_to_first_frame_ms": 3500.0,
        })
        assert resp.status_code == 204

    @pytest.mark.asyncio
    async def test_stats_report_returns_204(self, client):
        resp = await client.post("/telemetry", json={
            "event": "stats_report",
            "session_id": "test-session-2",
            "camera": "depth",
            "packets_received": 10000,
            "packets_lost": 50,
            "frames_decoded": 900,
            "jitter": 0.012,
        })
        assert resp.status_code == 204

    @pytest.mark.asyncio
    async def test_minimal_payload_accepted(self, client):
        resp = await client.post("/telemetry", json={
            "event": "stats_report",
        })
        assert resp.status_code == 204

    @pytest.mark.asyncio
    async def test_ice_failed_accepted(self, client):
        resp = await client.post("/telemetry", json={
            "event": "ice_failed",
            "session_id": "test-session-3",
            "camera": "color",
        })
        assert resp.status_code == 204

    @pytest.mark.asyncio
    async def test_invalid_event_type_rejected(self, client):
        resp = await client.post("/telemetry", json={
            "event": "invalid_event_type",
        })
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_extra_fields_accepted(self, client):
        resp = await client.post("/telemetry", json={
            "event": "ice_connected",
            "ice_connect_ms": 500,
            "extra": {"custom_field": "value", "debug": True},
        })
        assert resp.status_code == 204

    @pytest.mark.asyncio
    async def test_missing_body_rejected(self, client):
        resp = await client.post("/telemetry")
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_candidate_fields_accepted(self, client):
        resp = await client.post("/telemetry", json={
            "event": "ice_connected",
            "ice_connect_ms": 800,
            "local_candidate": {"type": "relay", "protocol": "udp", "address": "10.0.0.1", "port": 50000},
            "remote_candidate": {"type": "host", "protocol": "udp", "address": "192.168.1.10", "port": 40001},
        })
        assert resp.status_code == 204
