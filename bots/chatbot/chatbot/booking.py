"""Booking calendar: opening hours, services, free slots and appointments.

Pure scheduling logic over :class:`Store`. Times are timezone-aware; the
client's timezone comes from ``booking.timezone``. Everything Claude sees
(dates, hours) is in that local timezone; storage is UTC.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from .config import WEEKDAYS, BookingConfig, Service
from .store import Session, Store

WEEKDAY_RO = ["luni", "marți", "miercuri", "joi", "vineri", "sâmbătă", "duminică"]
MONTH_RO = ["ian", "feb", "mar", "apr", "mai", "iun", "iul", "aug", "sep", "oct", "nov", "dec"]


def _fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in text if not unicodedata.combining(c))


def _digits(text: str) -> str:
    return re.sub(r"\D", "", text)


@dataclass
class Appointment:
    id: int
    channel: str
    chat_id: str
    name: str
    contact: str
    service: str
    start: datetime  # local, tz-aware
    end: datetime
    status: str
    notes: str = ""

    def describe(self) -> str:
        return f"#{self.id} {fmt_dt(self.start)} · {self.service} · {self.name} ({self.contact}) · {self.status}"


def fmt_dt(dt: datetime) -> str:
    return f"{WEEKDAY_RO[dt.weekday()]} {dt.day} {MONTH_RO[dt.month - 1]} {dt.year}, {dt:%H:%M}"


def fmt_date(d: date) -> str:
    return f"{WEEKDAY_RO[d.weekday()]} {d.day} {MONTH_RO[d.month - 1]} {d.year}"


class BookingError(Exception):
    """A user-facing problem (bad date, slot taken, unknown service)."""


class BookingCalendar:
    def __init__(self, store: Store, cfg: BookingConfig):
        self.store = store
        self.cfg = cfg
        self.tz = ZoneInfo(cfg.timezone)

    # Helpers ---------------------------------------------------------------------

    def now(self) -> datetime:
        return datetime.now(self.tz)

    def service(self, name: str) -> Service:
        wanted = _fold(name).strip()
        for s in self.cfg.services:
            if _fold(s.name) == wanted:
                return s
        partial = [s for s in self.cfg.services if wanted in _fold(s.name) or _fold(s.name) in wanted]
        if len(partial) == 1:
            return partial[0]
        options = ", ".join(s.name for s in self.cfg.services)
        raise BookingError(f"Serviciu necunoscut sau ambiguu: '{name}'. Servicii disponibile: {options}.")

    def parse_date(self, text: str) -> date:
        try:
            return date.fromisoformat(text.strip())
        except ValueError:
            raise BookingError(f"Data '{text}' nu e validă; folosește formatul AAAA-LL-ZZ.")

    def parse_time(self, text: str) -> time:
        try:
            return time.fromisoformat(text.strip())
        except ValueError:
            raise BookingError(f"Ora '{text}' nu e validă; folosește formatul HH:MM.")

    def opening_intervals(self, day: date) -> list[tuple[datetime, datetime]]:
        spans = self.cfg.hours.get(WEEKDAYS[day.weekday()], [])
        out = []
        for span in spans:
            a, b = span.split("-")
            start = datetime.combine(day, time.fromisoformat(a.strip()), self.tz)
            end = datetime.combine(day, time.fromisoformat(b.strip()), self.tz)
            if end > start:
                out.append((start, end))
        return out

    def _load(self, interval_start: datetime, interval_end: datetime) -> list[Appointment]:
        return [self._to_appointment(r) for r in self.store.appointments_between(interval_start, interval_end)]

    def _to_appointment(self, row: dict) -> Appointment:
        return Appointment(
            id=row["id"],
            channel=row["channel"],
            chat_id=row["chat_id"],
            name=row["name"],
            contact=row["contact"],
            service=row["service"],
            start=datetime.fromisoformat(row["start_at"]).astimezone(self.tz),
            end=datetime.fromisoformat(row["end_at"]).astimezone(self.tz),
            status=row["status"],
            notes=row["notes"] or "",
        )

    def _busy(self, existing: list[Appointment], start: datetime, end: datetime) -> int:
        return sum(1 for a in existing if a.start < end and a.end > start)

    # Public API --------------------------------------------------------------------

    def free_slots(self, day: date, service: Service, now: datetime | None = None) -> list[datetime]:
        now = now or self.now()
        earliest = now + timedelta(minutes=self.cfg.min_notice_minutes)
        if day < now.date():
            raise BookingError("Data e în trecut.")
        if day > now.date() + timedelta(days=self.cfg.max_days_ahead):
            raise BookingError(f"Programările se fac cu cel mult {self.cfg.max_days_ahead} de zile înainte.")

        duration = timedelta(minutes=service.minutes)
        step = timedelta(minutes=self.cfg.slot_minutes)
        slots: list[datetime] = []
        for open_at, close_at in self.opening_intervals(day):
            existing = self._load(open_at, close_at)
            t = open_at
            while t + duration <= close_at:
                if t >= earliest and self._busy(existing, t, t + duration) < self.cfg.capacity:
                    slots.append(t)
                t += step
        return slots

    def book(
        self,
        session: Session,
        name: str,
        contact: str,
        service_name: str,
        day_text: str,
        time_text: str,
        notes: str = "",
        now: datetime | None = None,
    ) -> Appointment:
        service = self.service(service_name)
        day = self.parse_date(day_text)
        start = datetime.combine(day, self.parse_time(time_text), self.tz)
        free = self.free_slots(day, service, now)
        if start not in free:
            if free:
                alternatives = ", ".join(f"{s:%H:%M}" for s in free[:6])
                raise BookingError(f"Ora {start:%H:%M} nu e disponibilă pentru {service.name}. Ore libere în {fmt_date(day)}: {alternatives}.")
            raise BookingError(f"Nu mai sunt ore libere pentru {service.name} în {fmt_date(day)}. Propune altă zi.")
        end = start + timedelta(minutes=service.minutes)
        status = "confirmed" if self.cfg.auto_confirm else "pending"
        appointment_id = self.store.add_appointment(session, name, contact, service.name, start, end, status, notes)
        return Appointment(appointment_id, session.channel, session.chat_id, name, contact, service.name, start, end, status, notes)

    def cancel(self, appointment_id: int, contact: str | None, session: Session | None = None) -> Appointment:
        row = self.store.get_appointment(appointment_id)
        if not row:
            raise BookingError(f"Nu există programarea #{appointment_id}.")
        appt = self._to_appointment(row)
        same_chat = session is not None and (session.channel, session.chat_id) == (appt.channel, appt.chat_id)
        same_contact = contact is not None and _digits(contact) and _digits(contact) == _digits(appt.contact)
        same_contact = same_contact or (contact is not None and contact.strip().lower() == appt.contact.strip().lower())
        if not (same_chat or same_contact or session is None):
            raise BookingError("Datele de contact nu se potrivesc cu programarea; nu o pot anula.")
        if appt.status == "cancelled":
            raise BookingError(f"Programarea #{appointment_id} era deja anulată.")
        self.store.set_appointment_status(appointment_id, "cancelled")
        appt.status = "cancelled"
        return appt

    def confirm(self, appointment_id: int) -> Appointment:
        row = self.store.get_appointment(appointment_id)
        if not row:
            raise BookingError(f"Nu există programarea #{appointment_id}.")
        self.store.set_appointment_status(appointment_id, "confirmed")
        row["status"] = "confirmed"
        return self._to_appointment(row)

    def day_schedule(self, day: date) -> list[Appointment]:
        start = datetime.combine(day, time.min, self.tz)
        return self._load(start, start + timedelta(days=1))

    def due_reminders(self, now: datetime | None = None) -> list[Appointment]:
        now = now or self.now()
        horizon = now + timedelta(hours=self.cfg.remind_hours_before)
        return [self._to_appointment(r) for r in self.store.due_reminders(now, horizon)]

    def mark_reminded(self, appointment_id: int) -> None:
        self.store.mark_reminded(appointment_id)

    def describe_for_prompt(self) -> str:
        """Services and hours as text for the system prompt."""
        lines = ["Servicii care se pot programa prin chat (durată" + (", preț" if any(s.price for s in self.cfg.services) else "") + "):"]
        for s in self.cfg.services:
            lines.append(f"- {s.name}: {s.minutes} min" + (f", {s.price}" if s.price else ""))
        lines.append("Program:")
        for key, ro in zip(WEEKDAYS, WEEKDAY_RO):
            spans = self.cfg.hours.get(key) or []
            lines.append(f"- {ro}: {', '.join(spans) if spans else 'închis'}")
        return "\n".join(lines)
