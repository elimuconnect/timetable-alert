import asyncio
import json
import os
import pathlib
import re
from datetime import datetime, time, timedelta

import flet as ft
import flet_permission_handler as fph
import flet_webview_all as fwa
from android_notify import Notification


class smart_alert:
    ALERTS_FILE = "alerts.txt"
    DEFAULT_REMINDER_MINUTES = 5
    DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    TIME_FORMATS = ("%H:%M", "%H.%M", "%I:%M %p", "%I:%M%p", "%I %p")

    def __init__(self, page: ft.Page):
        self.page = page
        self.id_counter = 0
        self._last_handled_alarm_signature = None

        # ---------- WebView ----------
        BASE_DIR = pathlib.Path(__file__).resolve().parent
        IILE = BASE_DIR / "index.html"

        with open(IILE, "r", encoding="utf-8") as file:
            self.html_read = file.read()

        self.webview = fwa.FletWebviewAll(
            html=self.html_read,
            expand=True,
            allow_webview_permissions=True,
            background_color=ft.Colors.BLACK
        )
        self.webview.javascript_channels = ["FletBridge"]
        self.webview.on_javascript_message = self.on_message

        # ---------- App chrome ----------
        self.page.appbar = ft.AppBar(
            title=ft.Text(value="Smart Timetable Lesson Alert"),
            center_title=True,
            bgcolor=ft.Colors.BLUE,
            actions=[
                ft.IconButton(
                    icon=ft.Icons.ALARM_ON,
                    tooltip="Send a test notification",
                    icon_color=ft.Colors.GREEN,
                    on_click=lambda _: asyncio.create_task(self.test_notification()),
                ),
            ],
        )

        self.page.floating_action_button = ft.FloatingActionButton(
            icon=ft.Icons.LIBRARY_BOOKS_SHARP,
            bgcolor=ft.Colors.GREEN_600,
            foreground_color=ft.Colors.WHITE,
        )
        self.page.floating_action_button_location = ft.FloatingActionButtonLocation.END_FLOAT

        self.page.add(self.webview)

        # ---------- Background work (same startup tasks ClassAlert had) ----------
        asyncio.create_task(self.request_permission())
        asyncio.create_task(self.restore_alarms())
        asyncio.create_task(self.check_for_alarm_intent())
        asyncio.create_task(self.monitor_alarm_intents())

        self.page.on_route_change = lambda _: asyncio.create_task(self.check_for_alarm_intent())
        self.page.on_resume = lambda _: asyncio.create_task(self.check_for_alarm_intent())

    # ------------------------------------------------------------------
    # WebView -> Python
    # ------------------------------------------------------------------
    def on_message(self, e):
        print(f"JavaScript [{e.channel_name}]: {e.message_body}")

        try:
            lessons = self._parse_payload(e.message_body)
        except (ValueError, TypeError) as err:
            print(f"Bad timetable payload: {err}")
            self._toast("Could not read the timetable from the page.", ok=False)
            return

        scheduled, skipped = self.sync_timetable(lessons)

        if scheduled == 0 and skipped:
            self._toast("No valid lessons found. Existing alerts were left unchanged.", ok=False)
        elif skipped:
            self._toast(f"{scheduled} lesson alerts set, {skipped} skipped (invalid day/time/lesson).")
        else:
            self._toast(f"{scheduled} lesson alerts set.")

    def _parse_payload(self, body) -> list:
        """Accepts a JSON string or an already-decoded list/dict and returns a list of lesson dicts."""
        data = json.loads(body) if isinstance(body, (str, bytes, bytearray)) else body

        if isinstance(data, dict):
            for key in ("lessons", "timetable", "table", "data"):
                if isinstance(data.get(key), list):
                    return data[key]
            return [data]

        if isinstance(data, list):
            return data

        raise TypeError(f"Unsupported payload type: {type(data).__name__}")

    def sync_timetable(self, lessons: list) -> tuple[int, int]:
        """Replace all stored alerts with the timetable sent by the webview.

        Returns (scheduled_count, skipped_count).
        """
        records = []
        skipped = 0

        for lesson in lessons:
            record = self._build_record(lesson, len(records) + 1)
            if record is None:
                skipped += 1
            else:
                records.append(record)

        # Every lesson was invalid: don't wipe the alerts the user already has.
        if lessons and not records:
            return 0, skipped

        for old in self._load_alert_records():
            self._cancel_alarm(old["id"])

        self._save_alert_records(records)
        self._refresh_id_counter(records)

        for record in records:
            try:
                self._schedule_exact_alarm(
                    record["id"], record["time"], record["subject"], record["grade"]
                )
            except Exception as alarm_error:
                print(f"Alarm scheduling unavailable: {alarm_error}")

        return len(records), skipped

    def _build_record(self, lesson, nt_id: int):
        if not isinstance(lesson, dict):
            return None

        row = {str(key).lower(): value for key, value in lesson.items()}

        day_index = self._parse_day(row.get("day"))
        start = self._parse_start_time(row.get("time"))
        subject = self._clean(row.get("lesson") or row.get("subject"))

        if day_index is None or start is None or not subject:
            print(f"Skipping invalid lesson: {lesson}")
            return None

        grade = self._clean(row.get("class") or row.get("grade"))
        reminder = self._to_int(row.get("reminder"), self.DEFAULT_REMINDER_MINUTES)

        class_time = self._next_weekday(day_index, start)
        return {
            "id": nt_id,
            "time": self._calculate_alarm_time(class_time, reminder),
            "class_time": class_time,
            "reminder_before": reminder,
            "subject": subject,
            "grade": grade,
        }

    # ------------------------------------------------------------------
    # Parsing / time helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _clean(value) -> str:
        # "|" and newlines are delimiters in alerts.txt
        return str(value or "").replace("|", " ").replace("\n", " ").strip()

    @staticmethod
    def _to_int(value, default: int) -> int:
        try:
            number = int(str(value).split()[0])
            return number if number >= 0 else default
        except (ValueError, IndexError):
            return default

    def _parse_day(self, value) -> int | None:
        text = str(value or "").strip().lower()
        if len(text) < 3:
            return None
        for index, name in enumerate(self.DAYS):
            if name.lower().startswith(text):
                return index
        return None

    def _parse_start_time(self, value) -> time | None:
        """'08:00-09:00', '8:00 AM - 9:00 AM', '08:00' -> time(8, 0)"""
        text = str(value or "").strip()
        if not text:
            return None

        start = re.split(r"\s*[-\u2013\u2014]\s*|\s+to\s+", text, maxsplit=1, flags=re.I)[0].strip()

        for fmt in self.TIME_FORMATS:
            try:
                return datetime.strptime(start.upper(), fmt).time()
            except ValueError:
                continue
        return None

    def _next_weekday(self, day_index: int, start: time) -> datetime:
        now = datetime.now()
        days_ahead = (day_index - now.weekday()) % 7
        candidate = datetime.combine(now.date() + timedelta(days=days_ahead), start)
        if candidate <= now:
            candidate += timedelta(days=7)
        return candidate

    def _calculate_alarm_time(self, class_time: datetime, reminder_before: int) -> datetime:
        alarm_time = class_time - timedelta(minutes=reminder_before)
        if alarm_time <= datetime.now():
            return class_time
        return alarm_time

    def _next_future_occurrence(self, scheduled_time: datetime) -> datetime:
        next_time = scheduled_time
        now = datetime.now()

        while next_time <= now:
            next_time += timedelta(days=7)

        return next_time

    # ------------------------------------------------------------------
    # Storage (same alerts.txt format ClassAlert used)
    # ------------------------------------------------------------------
    def _refresh_id_counter(self, records=None):
        if records is None:
            records = self._load_alert_records()
        self.id_counter = max((record["id"] for record in records), default=0)

    def _load_alert_records(self) -> list[dict]:
        records = []
        if not os.path.exists(self.ALERTS_FILE):
            return records

        with open(self.ALERTS_FILE, "r", encoding="utf-8") as f:
            for line in f:
                raw_line = line.strip()
                if not raw_line:
                    continue

                try:
                    parts = raw_line.split("|")

                    if len(parts) == 4:
                        nt_id_str, schld_time_str, subject, grade = parts
                        class_time = datetime.fromisoformat(schld_time_str)
                        reminder_before = 0
                    elif len(parts) == 6:
                        nt_id_str, schld_time_str, class_time_str, reminder_before_str, subject, grade = parts
                        class_time = datetime.fromisoformat(class_time_str)
                        reminder_before = int(reminder_before_str)
                    else:
                        raise ValueError("Unexpected alert record format")

                    records.append(
                        {
                            "id": int(nt_id_str),
                            "time": datetime.fromisoformat(schld_time_str),
                            "class_time": class_time,
                            "reminder_before": reminder_before,
                            "subject": subject,
                            "grade": grade,
                        }
                    )
                except ValueError:
                    print(f"Skipping malformed alert entry: {raw_line}")

        return records

    def _save_alert_records(self, records):
        with open(self.ALERTS_FILE, "w", encoding="utf-8") as f:
            for record in records:
                f.write(
                    f"{record['id']}|{record['time'].isoformat()}|{record['class_time'].isoformat()}"
                    f"|{record['reminder_before']}|{record['subject']}|{record['grade']}\n"
                )

    # ------------------------------------------------------------------
    # Alarms / notifications
    # ------------------------------------------------------------------
    @staticmethod
    def _lesson_body(grade: str) -> str:
        return f"Grade: {grade}" if grade else "Your lesson is starting."

    def _schedule_exact_alarm(self, nt_id: int, schld_time: datetime, subject: str, grade: str):
        from flet_alarm import FletAlarm

        for_grade = f", grade {grade}" if grade else ""
        FletAlarm().set_alarm(
            schld_time,
            nt_id,
            title=f"Class Starting: {subject}{for_grade}",
            message=f"Your {subject} class{f' for {grade}' if grade else ''} is ready.",
            repeat_weekly=False,
        )

    def _cancel_alarm(self, nt_id: int):
        try:
            from flet_alarm import FletAlarm
            FletAlarm().cancel_alarm(nt_id)
        except Exception as alarm_error:
            print(f"Alarm cancellation unavailable: {alarm_error}")

        try:
            Notification(id=nt_id).cancel(nt_id)
        except Exception as notif_error:
            print(f"Notification cancel error: {notif_error}")

    def _build_alarm_notification(self, notification_id: int, title: str, body: str, use_custom_sound: bool = False):
        notif = Notification(id=notification_id, title=title, message=body)
        notif.icon_name = "ic_lock_idle_alarm"

        if use_custom_sound:
            sound_path = os.path.join(os.path.dirname(__file__), "assets", "alarmsound.mp3")
            notif.setSound(sound_path=sound_path)
        return notif

    def _send_notification_with_fallback(self, notif: Notification, body: str) -> bool:
        try:
            try:
                notif.setBigText(body)
            except Exception:
                pass
            notif.send(silent=False)
            return True
        except Exception as send_error:
            print(f"Notification send error: {send_error}")
            return False

    def _show_class_dialog(self, day_name: str, time_str: str, subject: str, grade: str):
        grade_line = f"\nGrade: {grade}" if grade else ""
        class_dialog = ft.AlertDialog(
            title=ft.Text("Time for Class!"),
            content=ft.Text(f"It's {day_name} {time_str}.\nSubject: {subject}{grade_line}"),
            actions=[ft.TextButton("Dismiss", on_click=lambda _: self.close_dialog())],
        )
        self.page.show_dialog(class_dialog)

    def close_dialog(self):
        self.page.pop_dialog()
        self.page.update()

    def _toast(self, message: str, ok: bool = True):
        self.page.show_dialog(
            ft.SnackBar(
                content=ft.Text(message),
                bgcolor=ft.Colors.GREEN_400 if ok else ft.Colors.RED_400,
            )
        )

    # ------------------------------------------------------------------
    # Alarm trigger handling (weekly repeat)
    # ------------------------------------------------------------------
    def _reschedule_next_alarm_occurrence(self, alarm_id: int):
        records = self._load_alert_records()
        updated = False

        for record in records:
            if record["id"] != alarm_id:
                continue

            next_class_time = self._next_future_occurrence(record["class_time"] + timedelta(days=7))
            record["class_time"] = next_class_time
            record["time"] = self._calculate_alarm_time(next_class_time, record["reminder_before"])
            self._schedule_exact_alarm(
                record["id"],
                record["time"],
                record["subject"],
                record["grade"],
            )
            updated = True
            break

        if updated:
            self._save_alert_records(records)

    def _find_triggered_record(self, now: datetime, fallback_alarm_id: int | None = None):
        records = self._load_alert_records()
        current_weekday = now.strftime("%A")
        current_time_str = now.strftime("%H:%M")

        for record in records:
            if (
                record["time"].strftime("%A") == current_weekday
                and record["time"].strftime("%H:%M") == current_time_str
            ):
                return record

        if fallback_alarm_id is not None:
            for record in records:
                if record["id"] == fallback_alarm_id:
                    return record

        return None

    async def check_for_alarm_intent(self):
        try:
            from flet_alarm import PythonActivity, cast

            if not PythonActivity:
                return

            raw_activity = PythonActivity.mActivity
            if raw_activity is None:
                return

            activity = cast("android.app.Activity", raw_activity)
            intent = activity.getIntent()
            if intent is None or not intent.getBooleanExtra("is_alarm_trigger", False):
                return

            notification_id = intent.getIntExtra("notification_id", 0)
            alarm_id = intent.getIntExtra("alarm_id", notification_id)
            scheduled_at_ms = intent.getLongExtra("scheduled_at_ms", 0)
            alarm_signature = (alarm_id, scheduled_at_ms)

            if self._last_handled_alarm_signature == alarm_signature:
                return

            self._last_handled_alarm_signature = alarm_signature

            target_record = self._find_triggered_record(datetime.now(), fallback_alarm_id=alarm_id)
            if target_record is not None:
                title = f"Class Starting: {target_record['subject']}"
                body = (
                    f"Grade: {target_record['grade']} is waiting for you."
                    if target_record["grade"]
                    else "Your lesson is starting."
                )
                notification_id = target_record["id"]
            else:
                title = intent.getStringExtra("notification_title") or "Class Reminder"
                body = intent.getStringExtra("notification_body") or "Check your timetable."

            notif = self._build_alarm_notification(notification_id, title, body, use_custom_sound=True)
            self._send_notification_with_fallback(notif, body)

            if target_record is not None:
                class_time = target_record.get("class_time", datetime.now())
                self._show_class_dialog(
                    class_time.strftime("%A"),
                    class_time.strftime("%H:%M"),
                    target_record["subject"],
                    target_record["grade"],
                )

            self._reschedule_next_alarm_occurrence(alarm_id)

            try:
                intent.removeExtra("is_alarm_trigger")
                intent.removeExtra("notification_id")
                intent.removeExtra("notification_title")
                intent.removeExtra("notification_body")
            except Exception:
                pass
        except Exception as e:
            print(f"Intent check error: {e}")

    async def monitor_alarm_intents(self):
        while True:
            await self.check_for_alarm_intent()
            await asyncio.sleep(1)

    # ------------------------------------------------------------------
    # Startup restore (replaces ClassAlert.readtimetable's alarm handling)
    # ------------------------------------------------------------------
    async def restore_alarms(self):
        """Roll stored lessons forward to their next occurrence and re-arm the alarms."""
        try:
            records = self._load_alert_records()
            self._refresh_id_counter(records)
            normalized = False

            for record in records:
                class_time = self._next_future_occurrence(record["class_time"])
                schld_time = self._calculate_alarm_time(class_time, record["reminder_before"])

                if class_time != record["class_time"] or schld_time != record["time"]:
                    record["class_time"] = class_time
                    record["time"] = schld_time
                    normalized = True

                try:
                    self._schedule_exact_alarm(
                        record["id"], schld_time, record["subject"], record["grade"]
                    )
                except Exception as alarm_error:
                    print(f"Alarm restore unavailable: {alarm_error}")

            if normalized:
                self._save_alert_records(records)
        except Exception as e:
            print(f"Error restoring alarms: {e}")

    # ------------------------------------------------------------------
    # Test + permissions
    # ------------------------------------------------------------------
    async def test_notification(self):
        try:
            test = self._build_alarm_notification(
                notification_id=999,
                title="Test Notification",
                body="If you see this, notifications are working!",
                use_custom_sound=False,
            )
            sent = self._send_notification_with_fallback(
                test, "If you see this, notifications are working!"
            )
            self._toast(
                "Test sent! Check your notification tray."
                if sent
                else "Test failed. Check Android notification settings.",
                ok=sent,
            )
            print(f"Test notification sent: {sent}")
        except Exception as e:
            self._toast(f"Error scheduling test notification: {e}", ok=False)
            print(f"Error scheduling test notification: {e}")

    async def request_permission(self):
        ph = fph.PermissionHandler()

        exact_alarm = await ph.request(fph.Permission.SCHEDULE_EXACT_ALARM)
        notification = await ph.request(fph.Permission.NOTIFICATION)
        overlay = await ph.request(fph.Permission.SYSTEM_ALERT_WINDOW)
        permission_file = await ph.request(fph.Permission.MANAGE_EXTERNAL_STORAGE)
        await ph.request(fph.Permission.IGNORE_BATTERY_OPTIMIZATIONS)

        if exact_alarm.name == "DENIED":
            self._toast(
                "Schedule exact alarm permission is required for timely class reminders. "
                "Please enable it in settings.",
                ok=False,
            )

        if notification.name == "DENIED":
            self._toast(
                "Notification permission is required for timely class reminders. "
                "Please enable it in settings.",
                ok=False,
            )

        if overlay.name == "DENIED":
            self._toast(
                "Overlay permission is required to show class reminders on top of other apps. "
                "Please enable it in settings.",
                ok=False,
            )


def main(page: ft.Page):
    page.title = "Smart Timetable Lesson Alert"
    
    smart_alert(page)


ft.run(main, assets_dir="assets")
