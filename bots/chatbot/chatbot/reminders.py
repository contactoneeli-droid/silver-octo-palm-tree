"""Customer reminders before an appointment, sent on Telegram.

Runs as a daemon thread; every ``interval`` seconds it looks for confirmed
appointments starting within ``booking.remind_hours_before`` and sends one
message per appointment. Customers who booked from the web widget cannot be
reached this way (no channel back to them), so those are only marked as
handled; WhatsApp/SMS reminders are a later addition.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime

from .booking import Appointment, BookingCalendar, fmt_dt

log = logging.getLogger(__name__)


def reminder_text(business_name: str, appt: Appointment) -> str:
    return (
        f"⏰ Reminder de la {business_name}: ai programare {fmt_dt(appt.start)} pentru {appt.service}. "
        f"Dacă nu mai poți ajunge, scrie-ne aici cât mai devreme. Te așteptăm!"
    )


class ReminderLoop(threading.Thread):
    def __init__(self, calendar: BookingCalendar, business_name: str, telegram_api=None, interval: int = 60):
        super().__init__(name="reminders", daemon=True)
        self.calendar = calendar
        self.business_name = business_name
        self.telegram_api = telegram_api
        self.interval = interval

    def tick(self, now: datetime | None = None) -> list[Appointment]:
        sent: list[Appointment] = []
        for appt in self.calendar.due_reminders(now):
            if appt.channel == "telegram" and self.telegram_api is not None:
                try:
                    self.telegram_api.send_message(appt.chat_id, reminder_text(self.business_name, appt))
                    sent.append(appt)
                except Exception as e:  # keep the loop alive; try again next tick
                    log.error("Reminder pentru #%s a eșuat: %s", appt.id, e)
                    continue
            else:
                log.info("Reminder #%s: canalul %s nu suportă remindere, îl marchez ca tratat.", appt.id, appt.channel)
            self.calendar.mark_reminded(appt.id)
        return sent

    def run(self) -> None:
        log.info("Remindere pornite (cu %.1f ore înainte).", self.calendar.cfg.remind_hours_before)
        while True:
            try:
                self.tick()
            except Exception:
                log.exception("Eroare în bucla de remindere")
            time.sleep(self.interval)
