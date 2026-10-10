import asyncio
import base64
import json
import os
import pathlib
import re
import faulthandler
import traceback
from datetime import datetime, time, timedelta

import flet as ft
import flet_permission_handler as fph
import flet_webview_all as fwa
from android_notify import Notification
import tts


# ============================================================
# PATHS / STORAGE
# ============================================================

BASE_DIR = pathlib.Path(__file__).resolve().parent

STORAGE_DIR = pathlib.Path(
    os.environ.get(
        "FLET_APP_STORAGE_DATA",
        str(BASE_DIR),
    )
)

STORAGE_DIR.mkdir(parents=True, exist_ok=True)

ALERTS_FILE = STORAGE_DIR / "alerts.txt"
LOG_FILE = STORAGE_DIR / "debug.log"


# ============================================================
# DEBUG LOGGING
# ============================================================

def _log(message):
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as stream:
            stream.write(
                f"{datetime.now():%Y-%m-%d %H:%M:%S} "
                f"{message}\n"
            )
            stream.flush()
    except Exception:
        pass


try:
    _FAULT_FILE = open(LOG_FILE, "a", encoding="utf-8")
    faulthandler.enable(_FAULT_FILE)
except Exception:
    _FAULT_FILE = None


# ============================================================
# MAIN APP
# ============================================================

class SmartAlert:

    DEFAULT_REMINDER_MINUTES = 5

    DAYS = [
        "Monday", "Tuesday", "Wednesday", "Thursday",
        "Friday", "Saturday", "Sunday",
    ]

    TIME_FORMATS = (
        "%H:%M",
        "%H.%M",
        "%I:%M %p",
        "%I:%M%p",
        "%I %p",
    )

    def __init__(self, page: ft.Page):
        self.page = page
        self._tts = None
        self._tts_initializing = False
        self._tts_lock = asyncio.Lock()

        # ----------------------------------------------------
        # LOAD HTML
        # ----------------------------------------------------

        try:
            html = (BASE_DIR / "index.html").read_text(
                encoding="utf-8"
            )
        except Exception as exc:
            _log(f"STARTUP: index.html ERROR: {exc!r}")

            html = """
            <!DOCTYPE html>
            <html>
            <head>
                <meta name="viewport"
                      content="width=device-width, initial-scale=1">
            </head>
            <body>
                <h2>Timetable Lesson Alert</h2>
                <p>index.html could not be loaded.</p>
            </body>
            </html>
            """

        # ----------------------------------------------------
        # WEBVIEW
        # ----------------------------------------------------

        try:
            self.webview = fwa.FletWebviewAll(
                html=html,
                expand=True,
                allow_webview_permissions=True,
                background_color=ft.Colors.BLACK,
            )

            self.webview.javascript_channels = ["FletBridge"]
            self.webview.on_javascript_message = self.on_message

        except Exception as exc:
            _log(
                "STARTUP: WebView creation ERROR:\n"
                + traceback.format_exc()
            )
            raise RuntimeError(
                f"Could not create timetable WebView: {exc}"
            ) from exc

        # ----------------------------------------------------
        # FILE PICKER
        # ----------------------------------------------------

        self.picker = ft.FilePicker()

        try:
            page.services.append(self.picker)
        except (AttributeError, TypeError):
            page.overlay.append(self.picker)

        # ----------------------------------------------------
        # APP BAR
        # ----------------------------------------------------

        page.appbar = ft.AppBar(
            title=ft.Text("Timetable Lesson Alert"),
            center_title=True,
            bgcolor=ft.Colors.BLUE,
            actions=[
                ft.IconButton(
                    icon=ft.Icons.ALARM_ON,
                    tooltip="Test notification and voice",
                    icon_color=ft.Colors.GREEN,
                    on_click=self._on_test_clicked,
                ),
                ft.IconButton(
                    icon=ft.Icons.ALARM_ADD,
                    tooltip="Native alarm test (20 s)",
                    on_click=lambda _: self._native_test(),
                ),
                ft.IconButton(
                    icon=ft.Icons.ARTICLE,
                    tooltip="Native alarm log",
                    on_click=lambda _: self._show_native_log(),
                ),
            ],
        )

        page.add(self.webview)

        _log("STARTUP: UI created successfully")

        self._start_task(
            self.request_permission,
            "permissions",
        )
        self._start_task(
            self.restore_alarms,
            "restore_alarms",
        )
        self._start_task(
            self.show_last_log,
            "show_last_log",
        )

        _log("STARTUP: SmartAlert initialized successfully")

    # ========================================================
    # TASK MANAGEMENT
    # ========================================================

    def _start_task(self, coroutine_function, task_name):
        try:
            task = asyncio.create_task(coroutine_function())
            task.add_done_callback(
                lambda completed: self._task_done(
                    completed,
                    task_name,
                )
            )
            return task
        except Exception:
            _log(
                f"TASK: Could not start {task_name}:\n"
                + traceback.format_exc()
            )
            return None

    @staticmethod
    def _task_done(task, task_name):
        try:
            task.result()
        except asyncio.CancelledError:
            _log(f"TASK: {task_name} cancelled")
        except Exception as exc:
            _log(
                f"TASK: {task_name} FAILED: {exc!r}\n"
                + "".join(
                    traceback.format_exception(
                        type(exc), exc, exc.__traceback__
                    )
                )
            )

    # ========================================================
    # NATIVE ALARM DIAGNOSTICS
    # ========================================================

    def _native_test(self):
        try:
            from flet_alarm import schedule_native_test

            self._toast(schedule_native_test(20), ok=True)
        except Exception as exc:
            _log(
                f"NATIVE TEST ERROR: {exc!r}\n"
                + traceback.format_exc()
            )
            self._toast(f"Native test failed: {exc!r}", ok=False)

    def _show_native_log(self):
        try:
            from flet_alarm import read_native_log

            text = read_native_log()
        except Exception as exc:
            _log(
                f"NATIVE LOG ERROR: {exc!r}\n"
                + traceback.format_exc()
            )
            text = f"Could not read native log: {exc!r}"

        self.page.show_dialog(
            ft.AlertDialog(
                title=ft.Text("Native alarm log"),
                content=ft.Column(
                    [ft.Text(text, selectable=True, size=11)],
                    scroll=ft.ScrollMode.AUTO,
                    height=400,
                ),
                actions=[
                    ft.TextButton(
                        "OK",
                        on_click=lambda _: self.close_dialog(),
                    )
                ],
            )
        )

    def _on_test_clicked(self, _):
        self._start_task(
            self.test_notification,
            "test_notification",
        )

    # ========================================================
    # WEBVIEW -> PYTHON
    # ========================================================

    def on_message(self, event):
        try:
            body = event.message_body

            if isinstance(body, (str, bytes, bytearray)):
                data = json.loads(body)
            else:
                data = body

            if isinstance(data, dict) and data.get("action") == "pick_docx":
                self._start_task(self.pick_docx, "pick_docx")
                return

            lessons = self._parse_payload(data)
            scheduled, skipped = self.sync_timetable(lessons)

            if scheduled == 0:
                self._toast(
                    "No new timetable alarms were registered. "
                    "Check debug.log. Existing saved alarms were retained "
                    "where possible.",
                    ok=False,
                )
            elif skipped:
                self._toast(
                    f"{scheduled} lesson alerts set; "
                    f"{skipped} lesson(s) skipped.",
                    ok=True,
                )
            else:
                self._toast(
                    f"{scheduled} lesson alerts set.",
                    ok=True,
                )

        except (ValueError, TypeError) as exc:
            _log(f"WEBVIEW: invalid message: {exc!r}")
            self._toast(
                "Could not read the timetable data.",
                ok=False,
            )

        except Exception as exc:
            _log(
                f"TIMETABLE: processing ERROR: {exc!r}\n"
                + traceback.format_exc()
            )
            self._toast(
                "Timetable processing failed. Check debug.log.",
                ok=False,
            )

    @staticmethod
    def _parse_payload(data):
        if isinstance(data, dict):
            for key in ("lessons", "timetable", "table", "data"):
                if isinstance(data.get(key), list):
                    return data[key]
            return [data]

        if isinstance(data, list):
            return data

        raise TypeError(
            f"Unsupported payload type: {type(data).__name__}"
        )

    # ========================================================
    # DOCX PICKER
    # ========================================================

    async def pick_docx(self):
        try:
            files = await self.picker.pick_files(
                allow_multiple=False,
                allowed_extensions=["docx"],
                with_data=True,
            )

            if not files:
                return

            selected = files[0]
            data = getattr(selected, "bytes", None)

            if not data and getattr(selected, "path", None):
                try:
                    data = pathlib.Path(selected.path).read_bytes()
                except Exception as exc:
                    _log(f"FILE: path read ERROR: {exc!r}")

            if not data:
                self._toast(
                    "Could not read the selected DOCX file.",
                    ok=False,
                )
                return

            encoded = base64.b64encode(data).decode("ascii")

            await self.webview.run_javascript(
                "receiveDocx("
                f"{json.dumps(selected.name)}, "
                f"{json.dumps(encoded)}"
                ")"
            )

            _log(f"FILE: DOCX delivered: {selected.name}")

        except Exception as exc:
            _log(
                f"FILE: picker ERROR: {exc!r}\n"
                + traceback.format_exc()
            )
            self._toast(
                "File selection failed. Check debug.log.",
                ok=False,
            )

    # ========================================================
    # TIMETABLE VALIDATION AND REGISTRATION
    # ========================================================

    def sync_timetable(self, lessons):
        if not isinstance(lessons, list) or not lessons:
            _log("TIMETABLE: empty or invalid timetable rejected")
            return 0, 1

        old_records = self._load_records()
        records = []
        skipped = 0

        for lesson in lessons:
            try:
                record = self._build_record(lesson, len(records) + 1)
                if record is None:
                    skipped += 1
                else:
                    records.append(record)
            except Exception as exc:
                skipped += 1
                _log(
                    f"TIMETABLE: invalid lesson: {exc!r}\n"
                    + traceback.format_exc()
                )

        if not records:
            _log("TIMETABLE: no valid lessons; old alarms retained")
            return 0, skipped

        existing_ids = {
            int(record["id"])
            for record in old_records
        }

        base_id = int(datetime.now().timestamp()) % 1_000_000_000

        for index, record in enumerate(records):
            candidate = (base_id + index + 1) % 2_000_000_000

            if candidate <= 0:
                candidate = index + 1

            while candidate in existing_ids:
                candidate = (candidate + 1) % 2_000_000_000
                if candidate <= 0:
                    candidate = 1

            record["id"] = candidate
            existing_ids.add(candidate)

        scheduled_records = []

        for record in records:
            try:
                self._schedule_alarm(
                    record["id"],
                    record["time"],
                    record["subject"],
                    record["grade"],
                    record.get("speech", ""),
                )
                scheduled_records.append(record)

            except Exception as exc:
                _log(
                    f"TIMETABLE: scheduling failed for "
                    f"{record['id']}: {exc!r}\n"
                    + traceback.format_exc()
                )
                break

        if len(scheduled_records) != len(records):
            for record in scheduled_records:
                self._cancel_alarm(record["id"])

            _log(
                "TIMETABLE: registration failed; "
                "old saved records retained"
            )
            return 0, skipped + 1

        if not self._save_records(records):
            for record in scheduled_records:
                self._cancel_alarm(record["id"])

            _log("TIMETABLE: save failed; new alarms cancelled")
            return 0, skipped + 1

        new_ids = {int(record["id"]) for record in records}

        for old in old_records:
            if int(old["id"]) not in new_ids:
                self._cancel_alarm(old["id"])

        _log(
            f"TIMETABLE: replacement complete; "
            f"scheduled={len(records)}, skipped={skipped}"
        )

        return len(records), skipped

    def _build_record(self, lesson, nt_id):
        if not isinstance(lesson, dict):
            return None

        row = {
            str(key).lower().strip(): value
            for key, value in lesson.items()
        }

        day = self._parse_day(row.get("day"))
        start = self._parse_start_time(row.get("time"))
        subject = self._clean(
            row.get("lesson") or row.get("subject")
        )

        if day is None or start is None or not subject:
            _log(f"TIMETABLE: skipping invalid lesson: {lesson!r}")
            return None

        grade = self._clean(row.get("class") or row.get("grade"))
        speech = self._clean(row.get("speech"))

        reminder = self._to_int(
            row.get("reminder"),
            self.DEFAULT_REMINDER_MINUTES,
        )
        reminder = max(0, min(reminder, 1440))

        class_time = self._next_weekday(day, start)
        alarm_time = self._alarm_time(class_time, reminder)

        return {
            "id": nt_id,
            "time": alarm_time,
            "class_time": class_time,
            "reminder_before": reminder,
            "subject": subject,
            "grade": grade,
            "speech": speech,
        }

    # ========================================================
    # DATE AND TIME HELPERS
    # ========================================================

    @staticmethod
    def _clean(value):
        return (
            str(value or "")
            .replace("|", " ")
            .replace("\n", " ")
            .replace("\r", " ")
            .strip()
        )

    @staticmethod
    def _to_int(value, default):
        try:
            number = int(str(value).strip().split()[0])
            return number if number >= 0 else default
        except (ValueError, IndexError, TypeError):
            return default

    def _parse_day(self, value):
        text = re.sub(r"[^a-z]", "", str(value or "").lower())

        if len(text) < 2:
            return None

        for index, name in enumerate(self.DAYS):
            if name.lower().startswith(text) or text == name[:3].lower():
                return index

        return None

    def _parse_start_time(self, value):
        text = str(value or "").strip()

        if not text:
            return None

        start = re.split(
            r"\s*[-\u2013\u2014]\s*|\s+to\s+",
            text,
            maxsplit=1,
            flags=re.I,
        )[0].strip()

        start = re.sub(r"\s+", " ", start)

        for fmt in self.TIME_FORMATS:
            try:
                return datetime.strptime(start.upper(), fmt).time()
            except ValueError:
                continue

        match = re.fullmatch(r"(\d{1,2})[:.](\d{2})", start)

        if match:
            hour, minute = map(int, match.groups())
            if 0 <= hour <= 23 and 0 <= minute <= 59:
                return time(hour, minute)

        return None

    @staticmethod
    def _next_weekday(day_index, start):
        now = datetime.now()
        days_ahead = (day_index - now.weekday()) % 7

        candidate = datetime.combine(
            now.date() + timedelta(days=days_ahead),
            start,
        )

        if candidate <= now:
            candidate += timedelta(days=7)

        return candidate

    @staticmethod
    def _alarm_time(class_time, reminder):
        candidate = class_time - timedelta(minutes=reminder)

        # Never send a past time to Android AlarmManager.
        # If the reminder time has passed, alert at class time.
        return candidate if candidate > datetime.now() else class_time

    @staticmethod
    def _next_future(value):
        now = datetime.now()
        while value <= now:
            value += timedelta(days=7)
        return value

    # ========================================================
    # STORAGE
    # ========================================================

    def _load_records(self):
        if not ALERTS_FILE.exists():
            return []

        records = []

        try:
            lines = ALERTS_FILE.read_text(
                encoding="utf-8"
            ).splitlines()
        except Exception as exc:
            _log(f"STORAGE: read ERROR: {exc!r}")
            return []

        for line in lines:
            if not line.strip():
                continue

            try:
                parts = line.split("|")

                if len(parts) == 6:
                    parts.append("")

                if len(parts) != 7:
                    _log(f"STORAGE: malformed record: {line!r}")
                    continue

                alarm_id, alarm, klass, reminder, subject, grade, speech = parts

                records.append({
                    "id": int(alarm_id),
                    "time": datetime.fromisoformat(alarm),
                    "class_time": datetime.fromisoformat(klass),
                    "reminder_before": int(reminder),
                    "subject": subject,
                    "grade": grade,
                    "speech": speech,
                })

            except (ValueError, TypeError) as exc:
                _log(f"STORAGE: invalid record: {exc!r}")

        return records

    def _save_records(self, records):
        temp_file = ALERTS_FILE.with_suffix(".tmp")

        try:
            ALERTS_FILE.parent.mkdir(parents=True, exist_ok=True)

            with open(temp_file, "w", encoding="utf-8") as stream:
                for record in records:
                    stream.write(
                        f"{record['id']}|"
                        f"{record['time'].isoformat()}|"
                        f"{record['class_time'].isoformat()}|"
                        f"{record['reminder_before']}|"
                        f"{record['subject']}|"
                        f"{record['grade']}|"
                        f"{record.get('speech', '')}\n"
                    )

                stream.flush()
                os.fsync(stream.fileno())

            temp_file.replace(ALERTS_FILE)
            _log(f"STORAGE: saved {len(records)} records")
            return True

        except Exception as exc:
            _log(
                f"STORAGE: write ERROR: {exc!r}\n"
                + traceback.format_exc()
            )

            try:
                temp_file.unlink(missing_ok=True)
            except Exception:
                pass

            return False

    # ========================================================
    # ANDROID ALARMS
    # ========================================================

    def _schedule_alarm(
        self,
        nt_id,
        when,
        subject,
        grade,
        speech="",
    ):
        from flet_alarm import FletAlarm

        if not isinstance(when, datetime):
            raise TypeError("Alarm time must be datetime")

        if when <= datetime.now():
            raise ValueError(
                f"Refusing to schedule past alarm: {when.isoformat()}"
            )

        grade_text = f" for {grade}" if grade else ""

        speech_text = str(speech or "").strip()
        if not speech_text:
            speech_text = (
                f"{subject}"
                + (f", {grade}" if grade else "")
                + ", class starts now."
            )

        title = f"Class Starting: {subject}"
        if grade:
            title += f", grade {grade}"

        message = f"Your {subject} class{grade_text} is ready."

        _log(
            f"ALARM: registering id={nt_id}; "
            f"time={when.isoformat()}; "
            f"speech={speech_text!r}"
        )

        alarm = FletAlarm()
        result = alarm.set_alarm(
            when,
            nt_id,
            title=title,
            message=message,
            speech_text=speech_text,
            repeat_weekly=True,
        )

        if result is False:
            raise RuntimeError(
                f"FletAlarm returned failure for alarm {nt_id}"
            )

        _log(f"ALARM: registered id={nt_id}")

    def _cancel_alarm(self, nt_id):
        try:
            from flet_alarm import FletAlarm
            FletAlarm().cancel_alarm(nt_id)
            _log(f"ALARM: cancelled id={nt_id}")
        except Exception as exc:
            _log(
                f"ALARM: cancel ERROR id={nt_id}: {exc!r}"
            )

        try:
            Notification(id=nt_id).cancel(nt_id)
        except Exception as exc:
            _log(f"NOTIFICATION: cancel ERROR: {exc!r}")

    # ========================================================
    # NOTIFICATIONS
    # ========================================================

    @staticmethod
    def _build_notification(nt_id, title, body):
        return Notification(
            id=nt_id,
            title=title,
            message=body,
        )

    @staticmethod
    def _send_notification(notification, body):
        try:
            try:
                notification.setBigText(body)
            except Exception:
                pass

            result = notification.send(silent=False)
            _log(f"NOTIFICATION: send returned {result!r}")
            return True

        except Exception as exc:
            _log(
                f"NOTIFICATION: send ERROR: {exc!r}\n"
                + traceback.format_exc()
            )
            return False

    # ========================================================
    # UI FEEDBACK
    # ========================================================

    def _toast(self, message, ok=True):
        try:
            self.page.show_dialog(
                ft.SnackBar(
                    content=ft.Text(message),
                    bgcolor=(
                        ft.Colors.GREEN_400
                        if ok
                        else ft.Colors.RED_400
                    ),
                )
            )
        except Exception as exc:
            _log(f"UI: toast ERROR: {exc!r}")

    def close_dialog(self):
        try:
            self.page.pop_dialog()
        except Exception:
            pass

        try:
            self.page.update()
        except Exception:
            pass

    # ========================================================
    # RESTORE SAVED ALARMS
    # ========================================================

    async def restore_alarms(self):
        records = self._load_records()

        if not records:
            _log("RESTORE: no saved alarms")
            return

        changed = False

        for record in records:
            try:
                class_time = self._next_future(record["class_time"])
                alarm_time = self._alarm_time(
                    class_time,
                    record["reminder_before"],
                )

                if (
                    class_time != record["class_time"]
                    or alarm_time != record["time"]
                ):
                    record["class_time"] = class_time
                    record["time"] = alarm_time
                    changed = True

                self._schedule_alarm(
                    record["id"],
                    alarm_time,
                    record["subject"],
                    record["grade"],
                    record.get("speech", ""),
                )

            except Exception as exc:
                _log(
                    f"RESTORE: alarm {record.get('id')} failed: "
                    f"{exc!r}\n"
                    + traceback.format_exc()
                )

        if changed:
            self._save_records(records)

        _log(f"RESTORE: processed {len(records)} saved alarms")

    # ========================================================
    # PREVIOUS LOG
    # ========================================================

    async def show_last_log(self):
        await asyncio.sleep(2)

        try:
            text = (
                LOG_FILE.read_text(
                    encoding="utf-8",
                    errors="replace",
                )
                if LOG_FILE.exists()
                else ""
            )

            if text.strip():
                self.page.show_dialog(
                    ft.AlertDialog(
                        title=ft.Text("Previous run log"),
                        content=ft.Column(
                            [
                                ft.Text(
                                    text[-5000:],
                                    selectable=True,
                                    size=11,
                                )
                            ],
                            scroll=ft.ScrollMode.AUTO,
                            height=400,
                        ),
                        actions=[
                            ft.TextButton(
                                "OK",
                                on_click=lambda _: self.close_dialog(),
                            )
                        ],
                    )
                )

            _log("STARTUP: app started")

        except Exception as exc:
            _log(
                f"LOG DISPLAY: ERROR: {exc!r}\n"
                + traceback.format_exc()
            )

    # ========================================================
    # MANUAL VOICE TEST
    # ========================================================

    async def initialize_tts(self):
        async with self._tts_lock:
            if (
                self._tts is not None
                and getattr(self._tts, "ready", False)
            ):
                return True

            try:
                _log("TTS: creating engine for manual test")
                self._tts = tts.TTS()

                ready = await asyncio.to_thread(
                    self._tts.wait_ready,
                    10,
                )

                _log(
                    f"TTS: ready={ready}; "
                    f"status={getattr(self._tts, 'status', None)}; "
                    f"error={getattr(self._tts, 'error', None)}"
                )

                return bool(ready)

            except Exception as exc:
                _log(
                    f"TTS: initialization ERROR: {exc!r}\n"
                    + traceback.format_exc()
                )
                self._tts = None
                return False

    async def _ensure_tts_ready(self):
        if (
            self._tts is not None
            and getattr(self._tts, "ready", False)
        ):
            return True

        return await self.initialize_tts()

    def _speak_alert(self, spoken):
        try:
            spoken = str(spoken or "").strip()

            if (
                not spoken
                or self._tts is None
                or not getattr(self._tts, "ready", False)
            ):
                return False

            result = self._tts.speak(spoken)
            _log(f"TTS: speak returned {result!r}")
            return bool(result)

        except Exception as exc:
            _log(
                f"TTS: speech ERROR: {exc!r}\n"
                + traceback.format_exc()
            )
            return False

    async def test_notification(self):
        body = "If you see this, notifications are working!"

        sent = self._send_notification(
            self._build_notification(
                999,
                "Test Notification",
                body,
            ),
            body,
        )

        self._toast(
            "Test notification sent."
            if sent
            else "Test notification failed. Check permissions.",
            ok=sent,
        )

        self._start_task(self.test_voice, "test_voice")

    async def test_voice(self):
        await asyncio.sleep(1)

        try:
            ready = await self._ensure_tts_ready()

            if ready:
                success = self._speak_alert(
                    "Voice test. Mathematics, in two minutes."
                )

                result = (
                    "TTS READY\n\nSpeech request accepted."
                    if success
                    else (
                        "TTS initialized, but speech failed.\n\n"
                        f"Status: {getattr(self._tts, 'status', None)}\n"
                        f"Error: {getattr(self._tts, 'error', None)}"
                    )
                )
            else:
                result = (
                    "TTS NOT READY.\n\n"
                    f"Status: {getattr(self._tts, 'status', None)}\n"
                    f"Error: {getattr(self._tts, 'error', None)}"
                )

        except Exception as exc:
            result = f"TTS TEST ERROR: {exc!r}"
            _log(f"{result}\n{traceback.format_exc()}")

        try:
            self.page.show_dialog(
                ft.AlertDialog(
                    title=ft.Text("Voice test"),
                    content=ft.Text(result, selectable=True),
                    actions=[
                        ft.TextButton(
                            "OK",
                            on_click=lambda _: self.close_dialog(),
                        )
                    ],
                )
            )
        except Exception as exc:
            _log(f"TEST TTS: dialog ERROR: {exc!r}")

    # ========================================================
    # PERMISSIONS
    # ========================================================

    async def request_permission(self):
        try:
            handler = fph.PermissionHandler()

            permissions = (
                (
                    fph.Permission.SCHEDULE_EXACT_ALARM,
                    "Exact alarm",
                ),
                (
                    fph.Permission.NOTIFICATION,
                    "Notification",
                ),
                (
                    fph.Permission.SYSTEM_ALERT_WINDOW,
                    "Overlay",
                ),
            )

            for permission, label in permissions:
                try:
                    result = await handler.request(permission)
                    _log(f"PERMISSION: {label}: {result!r}")

                except Exception as exc:
                    _log(
                        f"PERMISSION: {label} ERROR: {exc!r}"
                    )

            try:
                await handler.request(
                    fph.Permission.IGNORE_BATTERY_OPTIMIZATIONS
                )
            except Exception as exc:
                _log(f"PERMISSION: battery request ERROR: {exc!r}")

            _log("PERMISSION: requests completed")

        except Exception as exc:
            _log(
                f"PERMISSION: ERROR: {exc!r}\n"
                + traceback.format_exc()
            )


# ============================================================
# FLET ENTRY POINT
# ============================================================

def main(page: ft.Page):
    try:
        page.title = "Smart Timetable Lesson Alert"
        SmartAlert(page)
    except Exception as exc:
        _log(
            f"FATAL: main() failed: {exc!r}\n"
            + traceback.format_exc()
        )
        raise


ft.run(main, assets_dir="assets")
