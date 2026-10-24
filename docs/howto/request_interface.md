# Configure a request interface safely

Use `RequestInterface` when a manager reads a remote HTTP resource and you want
declared fields, filters, operations, authentication, retries, and stable
transport errors. Start with `UrllibRequestTransport` and configure the final
upstream URL directly.

## Define the interface

```python
from typing import ClassVar

from general_manager.interface import (
    BearerTokenAuthProvider,
    RequestField,
    RequestFilter,
    RequestInterface,
    RequestQueryOperation,
    RequestTransportConfig,
    UrllibRequestTransport,
)
from general_manager.manager import GeneralManager, Input


class Project(GeneralManager):
    class Interface(RequestInterface):
        id = Input(int)
        name = RequestField(str)

        class Meta:
            filters: ClassVar[dict[str, RequestFilter]] = {
                "status": RequestFilter(remote_name="state", value_type=str),
            }
            query_operations: ClassVar[dict[str, RequestQueryOperation]] = {
                "detail": RequestQueryOperation(
                    name="detail", method="GET", path="/projects/{id}"
                ),
                "list": RequestQueryOperation(
                    name="list", method="GET", path="/projects"
                ),
            }
            transport = UrllibRequestTransport()
            transport_config = RequestTransportConfig(
                base_url="https://service.example.com/api",
                timeout=5,
            )
            auth_provider = BearerTokenAuthProvider(token=lambda: "replace-me")
```

Call the manager normally:

```python
active_projects = Project.filter(status="active")
project = Project(id=42)
```

## Keep redirects explicit

The built-in urllib transport never follows redirects, including same-origin
redirects. A redirect raises a transport error with the upstream status and
headers intact. This prevents response-controlled destinations from receiving
credentials or request bodies and blocks redirects to internal services or from
HTTPS to HTTP.

If the application needs a private CA or an explicit proxy, do not rely on
`urllib.request.install_opener`; the transport creates its own opener. Inject a
custom opener and reject every redirect before inspecting `Location`:

```python
from email.message import Message
from typing import IO, NoReturn
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from general_manager.interface import UrllibRequestTransport


class RejectRedirects(HTTPRedirectHandler):
    def http_error_302(
        self,
        request: Request,
        response: IO[bytes],
        code: int,
        message: str,
        headers: Message,
    ) -> NoReturn:
        raise HTTPError(request.full_url, code, message, headers, response)

    http_error_301 = http_error_302
    http_error_303 = http_error_302
    http_error_307 = http_error_302
    http_error_308 = http_error_302


opener = build_opener(RejectRedirects())
transport = UrllibRequestTransport(urlopen=opener.open)
```

Returning `None` only from `redirect_request()` is insufficient for malformed
`Location` values because urllib may parse that header first. The injected
callable owns its redirect policy, so custom openers must preserve this early
rejection behavior.

See the [request interface recipe](../examples/request_interface_end_to_end.md),
the [request interface concept](../concepts/interfaces/request_interface.md),
and the [interface API reference](../api/interface.md#request-interfaces).
