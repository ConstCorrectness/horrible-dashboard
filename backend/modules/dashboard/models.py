from pydantic import BaseModel


class DashboardLayout(BaseModel):
    """Ordered widget ids shown on the dashboard grid."""

    widgets: list[str]


DEFAULT_LAYOUT = DashboardLayout(widgets=["dashboard.welcome", "dashboard.backendStatus", "dashboard.gameWidget"])
