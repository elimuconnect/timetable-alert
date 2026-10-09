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

STORAGE_DIR.mkdir(
    parents=True,
    exist_ok=True,
)

ALERTS_FILE = STORAGE_DIR / "alerts.txt"
LOG_FILE = STORAGE_DIR / "debug.log"


# ============================================================
# DEBUG LOG
# ============================================================

def _log(msg: str):
    try:
        LOG_FILE.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        with open(
            LOG_FILE,
            "a",
            encoding="utf-8",
        ) as f:
            f.write(
                f"{datetime.now():%Y-%m-%d %H:%M:%S} "
                f"{msg}\n"
            )

    except Exception:
        pass


try:
    _FAULT_FILE = open(
        LOG_FILE,
        "a",
        encoding="utf-8",
    )

    faulthandler.enable(
        _FAULT_FILE
    )

except Exception:
    _FAULT_FILE = None


# ============================================================
# MAIN APP
# ============================================================

class SmartAlert:

    DEFAULT_REMINDER_MINUTES = 5

    DAYS = [
        "Monday",
        "Tuesday",
        "Wednesday",
        "Thursday",
        "Friday",
        "Saturday",
        "Sunday",
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

        # Python TTS is initialized only when the user
        # presses the voice-test button.
        self._tts = None
        self._tts_initializing = False

        # ----------------------------------------------------
        # LOAD HTML
        # ----------------------------------------------------

        try:
            html = (
                BASE_DIR / "index.html"
            ).read_text(
                encoding="utf-8"
            )

        except Exception as err:
            _log(
                f"STARTUP: index.html ERROR: {err!r}"
            )

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

            self.webview.javascript_channels = [
                "FletBridge"
            ]

            self.webview.on_javascript_message = (
                self.on_message
            )

        except Exception as err:
            _log(
                "STARTUP: WebView creation ERROR:\n"
                + traceback.format_exc()
            )
            raise RuntimeError(
                f"Could not create the timetable WebView: {err}"
            ) from err

        # ----------------------------------------------------
        # FILE PICKER
        # ----------------------------------------------------

        try:
            self.picker = ft.FilePicker()

            try:
                page.services.append(
                    self.picker
                )
            except (AttributeError, TypeError):
                page.overlay.append(
                    self.picker
                )

        except Exception:
            _log(
                "STARTUP: FilePicker ERROR:\n"
                + traceback.format_exc()
            )
            raise

        # ----------------------------------------------------
        # APP BAR
        # ----------------------------------------------------

        page.appbar = ft.AppBar(
            title=ft.Text(
                "Timetable Lesson Alert"
            ),
            center_title=True,
            bgcolor=ft.Colors.BLUE,
            actions=[
                ft.IconButton(
                    icon=ft.Icons.ALARM_ON,
                    tooltip="Test notification and voice",
                    icon_color=ft.Colors.GREEN,
                    on_click=self._on_test_clicked,
                )
            ],
        )

        page.add(
            self.webview
        )

        _log(
            "STARTUP: UI created successfully"
        )

        # ----------------------------------------------------
        # STARTUP TASKS
        #
        # No automatic Python TTS startup.
        # No Python alarm-intent polling.
        # Kotlin AlarmReceiver handles scheduled alarms.
        # ----------------------------------------------------

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

        _log(
            "STARTUP: SmartAlert initialized successfully"
        )

    # ========================================================
    # SAFE BACKGROUND TASKS
    # ========================================================

    def _start_task(
        self,
        coroutine_function,
        task_name: str,
    ):
        try:
            task = asyncio.create_task(
                coroutine_function()
            )

            task.add_done_callback(
                lambda completed:
                self._startup_task_done(
                    completed,
                    task_name,
                )
            )

        except Exception:
            _log(
                f"STARTUP: Could not start {task_name}:\n"
                + traceback.format_exc()
            )

    @staticmethod
    def _startup_task_done(
        task,
        task_name: str,
    ):
        try:
            task.result()

        except asyncio.CancelledError:
            _log(
                f"STARTUP: {task_name} cancelled"
            )

        except Exception as err:
            _log(
                f"STARTUP: {task_name} FAILED: {err!r}\n"
                + traceback.format_exc()
            )

    def _on_test_clicked(self, _):
        self._start_task(
            self.test_notification,
            "test_notification",
        )

    # ========================================================
    # PYTHON TTS - MANUAL TEST ONLY
    # ========================================================

    async def initialize_tts(self):

        if (
            self._tts is not None
            and getattr(
                self._tts,
                "ready",
                False,
            )
        ):
            return True

        if self._tts_initializing:
            return False

        self._tts_initializing = True

        try:
            _log(
                "TTS: creating engine for manual test"
            )

            self._tts = tts.TTS()

            _log(
                "TTS: engine created; context="
                f"{getattr(self._tts, 'context_source', 'unknown')}"
            )

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

        except Exception as err:
            _log(
                f"TTS: initialization ERROR: {err!r}\n"
                + traceback.format_exc()
            )

            self._tts = None
            return False

        finally:
            self._tts_initializing = False

    async def _ensure_tts_ready(self):

        if (
            self._tts is not None
            and getattr(
                self._tts,
                "ready",
                False,
            )
        ):
            return True

        if self._tts_initializing:
            for _ in range(40):
                await asyncio.sleep(0.25)

                if (
                    self._tts is not None
                    and getattr(
                        self._tts,
                        "ready",
                        False,
                    )
                ):
                    return True

                if not self._tts_initializing:
                    break

        if (
            self._tts is None
            or not getattr(
                self._tts,
                "ready",
                False,
            )
        ):
            return await self.initialize_tts()

        return True

    def _speak_alert(
        self,
        spoken: str,
    ) -> bool:

        try:
            spoken = str(
                spoken or ""
            ).strip()

            if not spoken:
                _log("TTS: empty speech text")
                return False

            if self._tts is None:
                _log("TTS: engine unavailable")
                return False

            if not getattr(
                self._tts,
                "ready",
                False,
            ):
                _log("TTS: engine not ready")
                return False

            result = self._tts.speak(
                spoken
            )

            _log(
                f"TTS: speak returned {result!r}"
            )

            return bool(result)

        except Exception as err:
            _log(
                f"TTS: speech ERROR: {err!r}\n"
                + traceback.format_exc()
            )
            return False

    # ========================================================
    # WEBVIEW -> PYTHON
    # ========================================================

    def on_message(self, e):

        try:
            body = e.message_body

            if isinstance(
                body,
                (str, bytes, bytearray),
            ):
                data = json.loads(body)
            else:
                data = body

        except (ValueError, TypeError) as err:
            _log(
                f"WEBVIEW: invalid message: {err!r}"
            )
            self._toast(
                "Could not read the message from the timetable.",
                ok=False,
            )
            return

        if (
            isinstance(data, dict)
            and data.get("action") == "pick_docx"
        ):
            self._start_task(
                self.pick_docx,
                "pick_docx",
            )
            return

        try:
            lessons = self._parse_payload(
                data
            )

            scheduled, skipped = (
                self.sync_timetable(
                    lessons
                )
            )

        except Exception as err:
            _log(
                f"TIMETABLE: processing ERROR: {err!r}\n"
                + traceback.format_exc()
            )

            self._toast(
                "Could not process the timetable. "
                "Check the debug log.",
                ok=False,
            )
            return

        if scheduled == 0 and skipped:
            self._toast(
                "No valid lessons found. "
                "Existing alerts were left unchanged.",
                ok=False,
            )

        elif skipped:
            self._toast(
                f"{scheduled} lesson alerts set, "
                f"{skipped} skipped.",
                ok=(scheduled > 0),
            )

        else:
            self._toast(
                f"{scheduled} lesson alerts set.",
                ok=(scheduled > 0),
            )

    @staticmethod
    def _parse_payload(data) -> list:

        if isinstance(data, dict):
            for key in (
                "lessons",
                "timetable",
                "table",
                "data",
            ):
                if isinstance(
                    data.get(key),
                    list,
                ):
                    return data[key]

            return [data]

        if isinstance(data, list):
            return data

        raise TypeError(
            "Unsupported payload type: "
            f"{type(data).__name__}"
        )

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

            data = getattr(
                selected,
                "bytes",
                None,
            )

            if (
                not data
                and getattr(
                    selected,
                    "path",
                    None,
                )
            ):
                try:
                    data = pathlib.Path(
                        selected.path
                    ).read_bytes()

                except Exception as err:
                    _log(
                        f"FILE: path read ERROR: {err!r}"
                    )

            if not data:
                self._toast(
                    "Could not read the selected file.",
                    ok=False,
                )
                return

            b64 = base64.b64encode(
                data
            ).decode("ascii")

            await self.webview.run_javascript(
                "receiveDocx("
                f"{json.dumps(selected.name)}, "
                f"{json.dumps(b64)}"
                ")"
            )

            _log(
                f"FILE: DOCX delivered: {selected.name}"
            )

        except Exception as err:
            _log(
                f"FILE: picker ERROR: {err!r}\n"
                + traceback.format_exc()
            )

            self._toast(
                f"File selection failed: {err}",
                ok=False,
            )

    # ========================================================
    # TIMETABLE -> ANDROID ALARMS
    # ========================================================

    def sync_timetable(
        self,
        lessons: list,
    ) -> tuple[int, int]:

        records = []
        skipped = 0

        for lesson in lessons:
            record = self._build_record(
                lesson,
                len(records) + 1,
            )

            if record is None:
                skipped += 1
            else:
                records.append(record)

        if lessons and not records:
            return 0, skipped

        old_records = self._load_records()

        # Cancel old alarms before replacing the timetable.
        for old in old_records:
            self._cancel_alarm(
                old["id"]
            )

        self._save_records(
            records
        )

        successful = 0

        for record in records:
            try:
                self._schedule_alarm(
                    record["id"],
                    record["time"],
                    record["subject"],
                    record["grade"],
                    record.get("speech", ""),
                )

                successful += 1

            except Exception as err:
                _log(
                    f"ALARM: scheduling id={record['id']} "
                    f"FAILED: {err!r}\n"
                    + traceback.format_exc()
                )

        return successful, skipped

    def _build_record(
        self,
        lesson,
        nt_id: int,
    ):

        if not isinstance(lesson, dict):
            return None

        row = {
            str(k).lower().strip(): v
            for k, v in lesson.items()
        }

        day = self._parse_day(
            row.get("day")
        )

        start = self._parse_start_time(
            row.get("time")
        )

        subject = self._clean(
            row.get("lesson")
            or row.get("subject")
        )

        if day is None or start is None or not subject:
            _log(
                f"TIMETABLE: skipping invalid lesson: {lesson!r}"
            )
            return None

        grade = self._clean(
            row.get("class")
            or row.get("grade")
        )

        speech = self._clean(
            row.get("speech")
        )

        reminder = self._to_int(
            row.get("reminder"),
            self.DEFAULT_REMINDER_MINUTES,
        )

        reminder = max(
            0,
            min(reminder, 1440),
        )

        class_time = self._next_weekday(
            day,
            start,
        )

        return {
            "id": nt_id,
            "time": self._alarm_time(
                class_time,
                reminder,
            ),
            "class_time": class_time,
            "reminder_before": reminder,
            "subject": subject,
            "grade": grade,
            "speech": speech,
        }

    # ========================================================
    # PARSING / TIME HELPERS
    # ========================================================

    @staticmethod
    def _clean(value) -> str:
        return (
            str(value or "")
            .replace("|", " ")
            .replace("\n", " ")
            .replace("\r", " ")
            .strip()
        )

    @staticmethod
    def _to_int(
        value,
        default: int,
    ) -> int:

        try:
            text = str(value).strip()

            if not text:
                return default

            n = int(
                text.split()[0]
            )

            return n if n >= 0 else default

        except (
            ValueError,
            IndexError,
            TypeError,
        ):
            return default

    def _parse_day(
        self,
        value,
    ) -> int | None:

        text = str(
            value or ""
        ).strip().lower()

        text = re.sub(
            r"[^a-z]",
            "",
            text,
        )

        if len(text) < 2:
            return None

        for i, name in enumerate(self.DAYS):
            name_lower = name.lower()

            if (
                name_lower.startswith(text)
                or text == name_lower[:3]
            ):
                return i

        return None

    def _parse_start_time(
        self,
        value,
    ) -> time | None:

        text = str(
            value or ""
        ).strip()

        if not text:
            return None

        start = re.split(
            r"\s*[-\u2013\u2014]\s*"
            r"|\s+to\s+",
            text,
            maxsplit=1,
            flags=re.I,
        )[0].strip()

        start = re.sub(
            r"\s+",
            " ",
            start,
        ).strip()

        for fmt in self.TIME_FORMATS:
            try:
                return datetime.strptime(
                    start.upper(),
                    fmt,
                ).time()

            except ValueError:
                continue

        match = re.fullmatch(
            r"(\d{1,2})[:.](\d{2})",
            start,
        )

        if match:
            hour = int(match.group(1))
            minute = int(match.group(2))

            if (
                0 <= hour <= 23
                and 0 <= minute <= 59
            ):
                return time(hour, minute)

        return None

    @staticmethod
    def _next_weekday(
        day_index: int,
        start: time,
    ) -> datetime:

        now = datetime.now()

        ahead = (
            day_index - now.weekday()
        ) % 7

        candidate = datetime.combine(
            now.date() + timedelta(days=ahead),
            start,
        )

        if candidate <= now:
            candidate += timedelta(days=7)

        return candidate

    @staticmethod
    def _alarm_time(
        class_time: datetime,
        reminder: int,
    ) -> datetime:

        alarm = class_time - timedelta(
            minutes=reminder
        )

        if alarm <= datetime.now():
            return class_time

        return alarm

    @staticmethod
    def _next_future(
        value: datetime,
    ) -> datetime:

        now = datetime.now()

        while value <= now:
            value += timedelta(days=7)

        return value

    # ========================================================
    # STORAGE
    # ========================================================

    def _load_records(self) -> list[dict]:

        records = []

        if not ALERTS_FILE.exists():
            return records

        try:
            lines = ALERTS_FILE.read_text(
                encoding="utf-8"
            ).splitlines()

        except Exception as err:
            _log(
                f"STORAGE: read ERROR: {err!r}"
            )
            return records

        for line in lines:
            line = line.strip()

            if not line:
                continue

            try:
                parts = line.split("|")

                if len(parts) == 6:
                    parts.append("")

                if len(parts) != 7:
                    _log(
                        f"STORAGE: malformed record: {line!r}"
                    )
                    continue

                (
                    id_,
                    alarm,
                    klass,
                    rem,
                    subject,
                    grade,
                    speech,
                ) = parts

                records.append({
                    "id": int(id_),
                    "time": datetime.fromisoformat(alarm),
                    "class_time": datetime.fromisoformat(klass),
                    "reminder_before": int(rem),
                    "subject": subject,
                    "grade": grade,
                    "speech": speech,
                })

            except (
                ValueError,
                TypeError,
            ) as err:
                _log(
                    f"STORAGE: invalid entry: {line!r}: {err!r}"
                )

        return records

    def _save_records(self, records):

        try:
            ALERTS_FILE.parent.mkdir(
                parents=True,
                exist_ok=True,
            )

            temp_file = ALERTS_FILE.with_suffix(".tmp")

            with open(
                temp_file,
                "w",
                encoding="utf-8",
            ) as f:
                for record in records:
                    f.write(
                        f"{record['id']}|"
                        f"{record['time'].isoformat()}|"
                        f"{record['class_time'].isoformat()}|"
                        f"{record['reminder_before']}|"
                        f"{record['subject']}|"
                        f"{record['grade']}|"
                        f"{record.get('speech', '')}\n"
                    )

            temp_file.replace(
                ALERTS_FILE
            )

        except Exception as err:
            _log(
                f"STORAGE: write ERROR: {err!r}\n"
                + traceback.format_exc()
            )

    # ========================================================
    # ANDROID ALARMS
    # ========================================================

    def _schedule_alarm(
        self,
        nt_id: int,
        when: datetime,
        subject: str,
        grade: str,
        speech: str = "",
    ):

        from flet_alarm import FletAlarm

        suffix = (
            f" for {grade}"
            if grade
            else ""
        )

        speech_text = str(
            speech or ""
        ).strip()

        if not speech_text:
            speech_text = (
                f"{subject}"
                + (f", {grade}" if grade else "")
                + ", class starts now."
            )

        title = (
            f"Class Starting: {subject}"
            + (f", grade {grade}" if grade else "")
        )

        message = (
            f"Your {subject} class{suffix} is ready."
        )

        _log(
            f"ALARM: scheduling id={nt_id}; "
            f"time={when.isoformat()}"
        )

        # Native Kotlin receiver handles the scheduled
        # notification, speech, and weekly recurrence.
        FletAlarm().set_alarm(
            when,
            nt_id,
            title=title,
            message=message,
            speech_text=speech_text,
            repeat_weekly=True,
        )

        _log(
            f"ALARM: scheduled successfully id={nt_id}"
        )

    def _cancel_alarm(
        self,
        nt_id: int,
    ):

        try:
            from flet_alarm import FletAlarm

            FletAlarm().cancel_alarm(
                nt_id
            )

            _log(
                f"ALARM: cancelled id={nt_id}"
            )

        except Exception as err:
            _log(
                f"ALARM: cancel ERROR id={nt_id}: {err!r}"
            )

        # Clean up notifications created by older app versions.
        try:
            Notification(
                id=nt_id
            ).cancel(
                nt_id
            )

        except Exception as err:
            _log(
                f"NOTIFICATION: cancel ERROR: {err!r}"
            )

    # ========================================================
    # TEST NOTIFICATION
    # ========================================================

    @staticmethod
    def _build_notification(
        nt_id: int,
        title: str,
        body: str,
        custom_sound: bool = False,
    ):

        notif = Notification(
            id=nt_id,
            title=title,
            message=body,
        )

        try:
            notif.icon_name = "ic_lock_idle_alarm"
        except Exception:
            pass

        if custom_sound:
            sound_path = (
                BASE_DIR
                / "assets"
                / "alarmsound.mp3"
            )

            if sound_path.exists():
                try:
                    notif.setSound(
                        sound_path=str(sound_path)
                    )
                except Exception as err:
                    _log(
                        f"NOTIFICATION: sound ERROR: {err!r}"
                    )

        return notif

    @staticmethod
    def _send_notification(
        notif,
        body: str,
    ) -> bool:

        try:
            try:
                notif.setBigText(body)
            except Exception:
                pass

            result = notif.send(
                silent=False
            )

            _log(
                f"NOTIFICATION: send returned {result!r}"
            )

            return True

        except Exception as err:
            _log(
                f"NOTIFICATION: send ERROR: {err!r}\n"
                + traceback.format_exc()
            )
            return False

    # ========================================================
    # TOAST
    # ========================================================

    def _toast(
        self,
        message: str,
        ok: bool = True,
    ):

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

        except Exception as err:
            _log(
                f"UI: toast ERROR: {err!r}"
            )

    # ========================================================
    # RESTORE ALARMS
    # ========================================================

    async def restore_alarms(self):

        try:
            records = self._load_records()

            if not records:
                _log(
                    "RESTORE: no saved alarms"
                )
                return

            changed = False

            for record in records:
                class_time = self._next_future(
                    record["class_time"]
                )

                alarm = self._alarm_time(
                    class_time,
                    record["reminder_before"],
                )

                if (
                    class_time != record["class_time"]
                    or alarm != record["time"]
                ):
                    record["class_time"] = class_time
                    record["time"] = alarm
                    changed = True

                try:
                    self._schedule_alarm(
                        record["id"],
                        alarm,
                        record["subject"],
                        record["grade"],
                        record.get("speech", ""),
                    )

                except Exception as err:
                    _log(
                        f"RESTORE: alarm {record['id']} "
                        f"ERROR: {err!r}\n"
                        + traceback.format_exc()
                    )

            if changed:
                self._save_records(records)

            _log(
                f"RESTORE: processed {len(records)} saved alarms"
            )

        except Exception as err:
            _log(
                f"RESTORE: ERROR: {err!r}\n"
                + traceback.format_exc()
            )

    # ========================================================
    # SHOW PREVIOUS LOG
    # ========================================================

    async def show_last_log(self):

        # Allow the UI time to render before opening a dialog.
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

        except Exception as err:
            _log(
                f"LOG DISPLAY: ERROR: {err!r}\n"
                + traceback.format_exc()
            )

    # ========================================================
    # DIALOG
    # ========================================================

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
    # TEST NOTIFICATION + VOICE
    # ========================================================

    async def test_notification(self):

        body = "If you see this, notifications are working!"

        try:
            sent = self._send_notification(
                self._build_notification(
                    999,
                    "Test Notification",
                    body,
                ),
                body,
            )

            self._toast(
                (
                    "Test sent! Check your notification tray."
                    if sent
                    else "Test failed. Check Android notification settings."
                ),
                ok=sent,
            )

            # Run the voice test only after the user taps
            # the test button, never during startup.
            self._start_task(
                self.test_voice,
                "test_voice",
            )

        except Exception as err:
            _log(
                f"TEST: notification ERROR: {err!r}\n"
                + traceback.format_exc()
            )

            self._toast(
                f"Error sending test notification: {err}",
                ok=False,
            )

    async def test_voice(self):

        await asyncio.sleep(1)

        try:
            _log(
                "TEST TTS: starting"
            )

            ready = await self._ensure_tts_ready()

            if ready:
                success = self._speak_alert(
                    "Voice test. Mathematics, in two minutes."
                )

                if success:
                    result = (
                        "TTS READY\n\n"
                        f"Context: "
                        f"{getattr(self._tts, 'context_source', 'unknown')}\n\n"
                        "Speech command sent successfully."
                    )
                else:
                    result = (
                        "TTS initialized, but speech failed.\n\n"
                        f"Status: {getattr(self._tts, 'status', None)}\n"
                        f"Error: {getattr(self._tts, 'error', None)}"
                    )

            else:
                result = (
                    "TTS NOT READY.\n\n"
                    f"Status: {getattr(self._tts, 'status', None)}\n"
                    f"Error: {getattr(self._tts, 'error', None)}\n"
                    f"Context: "
                    f"{getattr(self._tts, 'context_source', 'unknown')}"
                )

            _log(
                f"TEST TTS: {result}"
            )

        except Exception as err:
            result = f"TTS TEST ERROR: {err!r}"

            _log(
                f"{result}\n{traceback.format_exc()}"
            )

        try:
            self.page.show_dialog(
                ft.AlertDialog(
                    title=ft.Text("Voice test"),
                    content=ft.Text(
                        result,
                        selectable=True,
                    ),
                    actions=[
                        ft.TextButton(
                            "OK",
                            on_click=lambda _: self.close_dialog(),
                        )
                    ],
                )
            )

        except Exception as err:
            _log(
                f"TEST TTS: dialog ERROR: {err!r}"
            )

    # ========================================================
    # PERMISSIONS
    # ========================================================

    async def request_permission(self):

        try:
            ph = fph.PermissionHandler()

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
                    result = await ph.request(permission)

                    _log(
                        f"PERMISSION: {label}: {result!r}"
                    )

                    if (
                        result is not None
                        and getattr(result, "name", "") == "DENIED"
                    ):
                        self._toast(
                            f"{label} permission was denied. "
                            "Enable it in Android settings if needed.",
                            ok=False,
                        )

                except Exception as err:
                    _log(
                        f"PERMISSION: {label} request ERROR: {err!r}"
                    )

            # This permission is optional and can vary by Android version.
            try:
                await ph.request(
                    fph.Permission.IGNORE_BATTERY_OPTIMIZATIONS
                )
            except Exception as err:
                _log(
                    f"PERMISSION: battery optimization request "
                    f"ERROR: {err!r}"
                )

            _log(
                "PERMISSION: requests completed"
            )

        except Exception as err:
            _log(
                f"PERMISSION: ERROR: {err!r}\n"
                + traceback.format_exc()
            )


# ============================================================
# FLET ENTRY POINT
# ============================================================

def main(page: ft.Page):

    try:
        page.title = "Smart Timetable Lesson Alert"

        SmartAlert(page)

    except Exception as err:
        _log(
            f"FATAL: main() failed: {err!r}\n"
            + traceback.format_exc()
        )
        raise


ft.run(
    main,
    assets_dir="assets",
)
