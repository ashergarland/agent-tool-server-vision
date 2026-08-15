"""Transport scoped context variables."""

from contextvars import ContextVar

from ..security import ANONYMOUS_PRINCIPAL

#: Principal for the in-flight MCP call. HTTP tool routes pass the principal
#: explicitly; the MCP transports read it from here.
current_principal: ContextVar[str] = ContextVar("current_principal", default=ANONYMOUS_PRINCIPAL)
