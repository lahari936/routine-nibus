from datetime import datetime
from functools import cached_property
from typing import Any, Literal
from zoneinfo import ZoneInfo

from pydantic import AwareDatetime, BaseModel, Field

EventKind = Literal[
    "motion_human", "motion_other", "ding", "contact_open", "contact_close", "offline", "online"
]
Severity = Literal["low", "medium", "high"]


def minute_of_day(dt: datetime) -> float:
    return dt.hour * 60 + dt.minute + dt.second / 60


class Event(BaseModel):
    event_id: str
    home_id: str
    device_id: str
    kind: EventKind
    ts: AwareDatetime
    # True for devices that only fire when someone is inside (indoor motion, door contact).
    # False for the doorbell, whose motion is mostly cars and passers-by.
    indoor: bool
    raw: dict[str, Any] = Field(default_factory=dict)

    @property
    def is_activity(self) -> bool:
        return self.indoor and self.kind not in ("offline", "online")


class Caregiver(BaseModel):
    name: str
    phone: str | None = None
    email: str | None = None


class Home(BaseModel):
    id: str
    name: str
    timezone: str = "America/New_York"
    caregivers: list[Caregiver] = Field(default_factory=list)
    quiet_hours: tuple[int, int] = (23, 5)  # local hours [start, end)
    checkin_enabled: bool = False
    snapshot_optin: bool = False

    @cached_property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    def in_quiet_hours(self, local: datetime) -> bool:
        start, end = self.quiet_hours
        if start > end:
            return local.hour >= start or local.hour < end
        return start <= local.hour < end


class FirstActivity(BaseModel):
    """Minutes after local midnight."""
    p10: float
    p50: float
    p90: float
    p95: float


class RecurringVisit(BaseModel):
    label: str
    weekdays: list[int]  # 0 = Monday
    window_start: float  # minutes after local midnight
    window_end: float
    hit_rate: float


class Baseline(BaseModel):
    home_id: str
    days: int
    first_activity: FirstActivity
    hourly_rate: list[list[float]]  # [weekday][hour], mean events per hour, smoothed
    recurring_visits: list[RecurringVisit]
    night_door: float  # fraction of nights with a door open in quiet hours

    @property
    def learning(self) -> bool:
        return self.days < 7


class Deviation(BaseModel):
    rule: str
    severity: Severity
    reason: str


class Alert(BaseModel):
    home_id: str
    ts: AwareDatetime
    rule: str
    severity: Severity
    reason: str
    status: Literal["open", "acked", "self_checked_in", "unresolved"] = "open"
    escalation_exec_arn: str | None = None
    acked_by: str | None = None
