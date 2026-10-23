"""Remote route registration must retain the host application's security stack."""

from types import SimpleNamespace

import pytest

from asgiref.sync import async_to_sync
from channels.routing import ProtocolTypeRouter, URLRouter
from django.urls import re_path

from general_manager.api import remote_invalidation as remote


class Project:
    class RemoteAPI:
        enabled = True
        resource_name = "secure-projects"
        base_path = "/remote"
        websocket_invalidation = True
        allow_update = True


class DenyMiddleware:
    def __init__(self, inner):
        self.inner = inner
        self.calls = 0

    async def __call__(self, scope, receive, send):
        self.calls += 1
        await send({"type": "websocket.close", "code": 4403})


@pytest.mark.parametrize("declared_patterns", [True, False])
def test_registration_and_clear_preserve_security_wrappers(
    monkeypatch, settings, declared_patterns
):
    existing = re_path(r"^existing/$", lambda *_: None)
    router = URLRouter([existing])
    guard = DenyMiddleware(router)
    protocol = ProtocolTypeRouter({"websocket": guard})
    outer = DenyMiddleware(protocol)
    module = SimpleNamespace(application=outer)
    if declared_patterns:
        module.websocket_urlpatterns = [existing]
    settings.ASGI_APPLICATION = "tests.fake.application"
    monkeypatch.setattr(remote, "import_module", lambda _: module)

    remote.ensure_remote_invalidation_route([Project])
    assert module.application is outer
    assert protocol.application_mapping["websocket"] is guard
    assert len(router.routes) == 2

    async def invoke(path):
        sent = []

        async def send(message):
            sent.append(message)

        async def receive():
            return {"type": "websocket.connect"}

        await protocol({"type": "websocket", "path": path}, receive, send)
        return sent

    for path in ("/existing/", "/remote/ws/secure-projects/"):
        assert async_to_sync(invoke)(path) == [
            {"type": "websocket.close", "code": 4403}
        ]
    assert guard.calls == 2

    remote.clear_remote_invalidation_routes()
    assert module.application is outer
    assert protocol.application_mapping["websocket"] is guard
    assert router.routes == [existing]
    assert module.websocket_urlpatterns == [existing]
    assert async_to_sync(invoke)("/existing/")[0]["code"] == 4403


def test_empty_registry_is_a_complete_noop(monkeypatch, settings):
    module = SimpleNamespace(application=object())
    settings.ASGI_APPLICATION = "tests.fake.application"
    monkeypatch.setattr(remote, "import_module", lambda _: module)
    remote.ensure_remote_invalidation_route([])
    assert not hasattr(module, "websocket_urlpatterns")


def test_opaque_websocket_application_is_not_replaced(monkeypatch, settings):
    guard = DenyMiddleware(None)
    application = ProtocolTypeRouter({"websocket": guard})
    module = SimpleNamespace(application=application, websocket_urlpatterns=[])
    settings.ASGI_APPLICATION = "tests.fake.application"
    monkeypatch.setattr(remote, "import_module", lambda _: module)
    remote.ensure_remote_invalidation_route([Project])
    assert application.application_mapping["websocket"] is guard
    assert module.websocket_urlpatterns == []


def test_opaque_app_attribute_guard_is_preserved(monkeypatch, settings):
    class OpaqueGuard:
        def __init__(self, app):
            self.app = app

        async def __call__(self, scope, receive, send):
            await send({"type": "websocket.close", "code": 4403})

    guard = OpaqueGuard(ProtocolTypeRouter({"websocket": URLRouter([])}))
    _assert_opaque_root_preserved(guard, monkeypatch, settings)


def test_opaque_closure_guard_is_preserved(monkeypatch, settings):
    inner = ProtocolTypeRouter({"websocket": URLRouter([])})

    async def guard(scope, receive, send):
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 4403})
            return
        await inner(scope, receive, send)

    _assert_opaque_root_preserved(guard, monkeypatch, settings)


def _assert_opaque_root_preserved(guard, monkeypatch, settings):
    module = SimpleNamespace(application=guard, websocket_urlpatterns=[])
    settings.ASGI_APPLICATION = "tests.fake.application"
    monkeypatch.setattr(remote, "import_module", lambda _: module)
    remote.ensure_remote_invalidation_route([Project])
    assert module.application is guard
    assert module.websocket_urlpatterns == []
    remote.clear_remote_invalidation_routes()
    assert module.application is guard

    async def invoke():
        sent = []

        async def send(message):
            sent.append(message)

        async def receive():
            return {"type": "websocket.connect"}

        await module.application(
            {"type": "websocket", "path": "/remote/ws/secure-projects/"}, receive, send
        )
        return sent

    assert async_to_sync(invoke)() == [{"type": "websocket.close", "code": 4403}]


def test_unwrapped_django_http_handler_can_gain_websocket_routes(monkeypatch, settings):
    from django.core.handlers.asgi import ASGIHandler

    handler = ASGIHandler()
    module = SimpleNamespace(application=handler, websocket_urlpatterns=[])
    settings.ASGI_APPLICATION = "tests.fake.application"
    monkeypatch.setattr(remote, "import_module", lambda _: module)
    remote.ensure_remote_invalidation_route([Project])
    assert isinstance(module.application, ProtocolTypeRouter)
    assert module.application.application_mapping["http"] is handler
    assert len(module.websocket_urlpatterns) == 1


@pytest.mark.django_db(transaction=True)
def test_channels_origin_validator_remains_around_registered_routes(
    monkeypatch, settings
):
    from asgiref.testing import ApplicationCommunicator
    from channels.security.websocket import OriginValidator

    reached = []

    async def endpoint(scope, receive, send):
        reached.append(scope["path"])
        await send({"type": "websocket.accept"})

    monkeypatch.setattr(remote.RemoteInvalidationConsumer, "as_asgi", lambda: endpoint)
    router = URLRouter([])
    guard = OriginValidator(router, ["https://allowed.example"])
    module = SimpleNamespace(
        application=ProtocolTypeRouter({"websocket": guard}), websocket_urlpatterns=[]
    )
    settings.ASGI_APPLICATION = "tests.fake.application"
    monkeypatch.setattr(remote, "import_module", lambda _: module)
    remote.ensure_remote_invalidation_route([Project])
    assert module.application.application_mapping["websocket"] is guard
    assert len(router.routes) == 1

    async def connect(origin):
        headers = [] if origin is None else [(b"origin", origin)]
        communicator = ApplicationCommunicator(
            module.application,
            {
                "type": "websocket",
                "path": "/remote/ws/secure-projects/",
                "headers": headers,
            },
        )
        await communicator.send_input({"type": "websocket.connect"})
        response = await communicator.receive_output()
        await communicator.send_input({"type": "websocket.disconnect", "code": 1000})
        await communicator.wait()
        return response["type"]

    assert async_to_sync(connect)(None) == "websocket.close"
    assert async_to_sync(connect)(b"https://evil.example") == "websocket.close"
    assert reached == []
    assert async_to_sync(connect)(b"https://allowed.example") == "websocket.accept"
    assert reached == ["/remote/ws/secure-projects/"]
    remote.clear_remote_invalidation_routes()
    assert module.application.application_mapping["websocket"] is guard
    assert router.routes == []
