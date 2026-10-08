"""Experiment tests must never connect to an external endpoint."""

import socket

import pytest


@pytest.fixture(autouse=True)
def prohibit_external_connections(monkeypatch):
    original = socket.socket.connect

    def connect(sock, address):
        if not isinstance(address, tuple) or address[0] not in {"127.0.0.1", "::1"}:
            raise AssertionError("external_network_forbidden")
        return original(sock, address)

    monkeypatch.setattr(socket.socket, "connect", connect)
