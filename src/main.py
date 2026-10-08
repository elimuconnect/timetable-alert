import asyncio
import base64
import json
import os
import pathlib
import re
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

# Writable app storage on Android; falls back to source folder
# when running on desktop.
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

import faulthandler


def _log(msg: str):
    try:
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
    LOG_FILE.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

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

    def __init__(
        self,
        page: ft.Page,
    ):

        self.page = page

        # --------------------------------------------------------
        # TTS STATE
        #
        # Python TTS is now used ONLY for the manual voice test.
        #
        # Scheduled alarms use native Kotlin AlarmReceiver.
        # --------------------------------------------------------

        self._tts = None
        self._tts_initializing = False

        # --------------------------------------------------------
        # WEBVIEW
        # --------------------------------------------------------

        try:

            html = (
                BASE_DIR / "index.html"
            ).read_text(
                encoding="utf-8"
            )

        except Exception as err:

            _log(
                f"startup: index.html ERROR: {err}"
            )

            html = """
            <!DOCTYPE html>
            <html>
            <body>
                <h2>Timetable Lesson Alert</h2>
                <p>index.html could not be loaded.</p>
            </body>
            </html>
            """

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

        # --------------------------------------------------------
        # FILE PICKER
        # --------------------------------------------------------

        self.picker = ft.FilePicker()

        try:

            page.services.append(
                self.picker
            )

        except AttributeError:

            page.overlay.append(
                self.picker
            )

        # --------------------------------------------------------
        # APP BAR
        # --------------------------------------------------------

        page.appbar = ft.AppBar(
            title=ft.Text(
                "Timetable Lesson Alert"
            ),
            center_title=True,
            bgcolor=ft.Colors.BLUE,
            actions=[
                ft.IconButton(
                    icon=ft.Icons.ALARM_ON,
                    tooltip=(
                        "Send a test notification "
                        "and voice alert"
                    ),
                    icon_color=ft.Colors.GREEN,
                    on_click=lambda _:
                    asyncio.create_task(
                        self.test_notification()
                    ),
                )
            ],
        )

        page.add(
            self.webview
        )

        # --------------------------------------------------------
        # STARTUP TASKS
        #
        # IMPORTANT:
        #
        # There is NO alarm-intent monitor here.
        #
        # Android AlarmManager -> Kotlin AlarmReceiver
        # is now the ONLY scheduled alarm execution path.
        # --------------------------------------------------------

        for job in (
            self.request_permission,
            self.initialize_tts,
            self.restore_alarms,
            self.show_last_log,
        ):

            asyncio.create_task(
                job()
            )

        _log(
            "startup: SmartAlert initialized"
        )

    # ============================================================
    # PYTHON TTS
    #
    # USED ONLY FOR MANUAL / TEST VOICE
    # ============================================================

    async def initialize_tts(
        self,
    ):

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
                "TTS startup: creating persistent TTS"
            )

            self._tts = tts.TTS()

            _log(
                "TTS startup: created; "
                f"context="
                f"{getattr(self._tts, 'context_source', 'unknown')}"
            )

            ready = await asyncio.to_thread(
                self._tts.wait_ready,
                10,
            )

            if ready:

                _log(
                    "TTS startup: READY; "
                    f"status="
                    f"{getattr(self._tts, 'status', None)}"
                )

                return True

            _log(
                "TTS startup: NOT READY; "
                f"status="
                f"{getattr(self._tts, 'status', None)}; "
                f"error="
                f"{getattr(self._tts, 'error', None)}"
            )

            return False

        except Exception as err:

            _log(
                f"TTS startup: ERROR: {err}"
            )

            print(
                f"TTS initialization error: {err}"
            )

            self._tts = None

            return False

        finally:

            self._tts_initializing = False

    async def _ensure_tts_ready(
        self,
    ):

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

            _log(
                "TTS: initialization already running; waiting"
            )

            for _ in range(40):

                await asyncio.sleep(
                    0.25
                )

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

            await self.initialize_tts()

        return (
            self._tts is not None
            and getattr(
                self._tts,
                "ready",
                False,
            )
        )

    def _speak_alert(
        self,
        spoken: str,
    ) -> bool:

        try:

            spoken = str(
                spoken or ""
            ).strip()

            if not spoken:

                _log(
                    "TTS: empty speech text"
                )

                return False

            if self._tts is None:

                _log(
                    "TTS: no TTS object"
                )

                return False

            if not getattr(
                self._tts,
                "ready",
                False,
            ):

                _log(
                    "TTS: engine not ready"
                )

                return False

            _log(
                f"TTS: speaking {spoken!r}"
            )

            result = self._tts.speak(
                spoken
            )

            _log(
                f"TTS: speak returned {result}"
            )

            return bool(result)

        except Exception as err:

            _log(
                f"TTS: speak ERROR: {err}"
            )

            print(
                f"TTS speak error: {err}"
            )

            return False

    # ============================================================
    # WEBVIEW -> PYTHON
    # ============================================================

    def on_message(
        self,
        e,
    ):

        body = e.message_body

        try:

            data = (
                json.loads(body)
                if isinstance(
                    body,
                    (
                        str,
                        bytes,
                        bytearray,
                    ),
                )
                else body
            )

        except (
            ValueError,
            TypeError,
        ):

            data = None

        # --------------------------------------------------------
        # PICK DOCX
        # --------------------------------------------------------

        if (
            isinstance(data, dict)
            and data.get("action")
            == "pick_docx"
        ):

            asyncio.create_task(
                self.pick_docx()
            )

            return

        # --------------------------------------------------------
        # TIMETABLE DATA
        # --------------------------------------------------------

        try:

            lessons = self._parse_payload(
                data
            )

        except (
            ValueError,
            TypeError,
        ) as err:

            print(
                f"Bad timetable payload: {err}"
            )

            self._toast(
                "Could not read the timetable from the page.",
                ok=False,
            )

            return

        scheduled, skipped = (
            self.sync_timetable(
                lessons
            )
        )

        if (
            scheduled == 0
            and skipped
        ):

            self._toast(
                "No valid lessons found. "
                "Existing alerts were left unchanged.",
                ok=False,
            )

        elif skipped:

            self._toast(
                f"{scheduled} lesson alerts set, "
                f"{skipped} skipped."
            )

        else:

            self._toast(
                f"{scheduled} lesson alerts set."
            )

    @staticmethod
    def _parse_payload(
        data,
    ) -> list:

        if isinstance(
            data,
            dict,
        ):

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

        if isinstance(
            data,
            list,
        ):

            return data

        raise TypeError(
            "Unsupported payload type: "
            f"{type(data).__name__}"
        )

    async def pick_docx(
        self,
    ):

        try:

            files = await self.picker.pick_files(
                allow_multiple=False,
                allowed_extensions=[
                    "docx"
                ],
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
                        f"FILE: path read ERROR: {err}"
                    )

            if not data:

                self._toast(
                    "Could not read the selected file.",
                    ok=False,
                )

                return

            b64 = base64.b64encode(
                data
            ).decode(
                "ascii"
            )

            await self.webview.run_javascript(
                "receiveDocx("
                f"{json.dumps(selected.name)}, "
                f"{json.dumps(b64)}"
                ")"
            )

            _log(
                "FILE: DOCX delivered to WebView: "
                f"{selected.name}"
            )

        except Exception as err:

            _log(
                f"FILE: picker ERROR: {err}"
            )

            print(
                f"File pick failed: {err}"
            )

            self._toast(
                f"File pick failed: {err}",
                ok=False,
            )

    # ============================================================
    # TIMETABLE -> ANDROID ALARMS
    # ============================================================

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

                records.append(
                    record
                )

        # Never destroy valid existing alarms if the page
        # sends a completely invalid timetable.
        if (
            lessons
            and not records
        ):

            return (
                0,
                skipped,
            )

        # --------------------------------------------------------
        # CANCEL OLD ALARMS
        # --------------------------------------------------------

        for old in self._load_records():

            self._cancel_alarm(
                old["id"]
            )

        # --------------------------------------------------------
        # SAVE NEW RECORDS
        # --------------------------------------------------------

        self._save_records(
            records
        )

        # --------------------------------------------------------
        # SCHEDULE NEW NATIVE ANDROID ALARMS
        #
        # Kotlin AlarmReceiver owns:
        #   notification
        #   TTS
        #   weekly repeat
        # --------------------------------------------------------

        successful = 0

        for record in records:

            try:

                self._schedule_alarm(
                    record["id"],
                    record["time"],
                    record["subject"],
                    record["grade"],
                    record.get(
                        "speech",
                        "",
                    ),
                )

                successful += 1

            except Exception as err:

                _log(
                    f"Alarm scheduling ERROR: {err}"
                )

                print(
                    f"Alarm scheduling unavailable: {err}"
                )

        return (
            successful,
            skipped,
        )

    def _build_record(
        self,
        lesson,
        nt_id: int,
    ):

        if not isinstance(
            lesson,
            dict,
        ):

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

        if (
            day is None
            or start is None
            or not subject
        ):

            _log(
                "TIMETABLE: skipping invalid lesson: "
                f"{lesson}"
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
            min(
                reminder,
                1440,
            ),
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

    # ============================================================
    # PARSING / TIME HELPERS
    # ============================================================

    @staticmethod
    def _clean(
        value,
    ) -> str:

        return (
            str(value or "")
            .replace(
                "|",
                " ",
            )
            .replace(
                "\n",
                " ",
            )
            .replace(
                "\r",
                " ",
            )
            .strip()
        )

    @staticmethod
    def _to_int(
        value,
        default: int,
    ) -> int:

        try:

            text = str(
                value
            ).strip()

            if not text:
                return default

            n = int(
                text.split()[0]
            )

            return (
                n
                if n >= 0
                else default
            )

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

        if len(text) < 2:
            return None

        text = re.sub(
            r"[^a-z]",
            "",
            text,
        )

        if len(text) < 2:
            return None

        for i, name in enumerate(
            self.DAYS
        ):

            name_lower = name.lower()

            if name_lower.startswith(
                text
            ):

                return i

            if text == name_lower[:3]:

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

            hour = int(
                match.group(1)
            )

            minute = int(
                match.group(2)
            )

            if (
                0 <= hour <= 23
                and 0 <= minute <= 59
            ):

                return time(
                    hour,
                    minute,
                )

        return None

    @staticmethod
    def _next_weekday(
        day_index: int,
        start: time,
    ) -> datetime:

        now = datetime.now()

        ahead = (
            day_index
            - now.weekday()
        ) % 7

        candidate = datetime.combine(
            now.date()
            + timedelta(
                days=ahead
            ),
            start,
        )

        if candidate <= now:

            candidate += timedelta(
                days=7
            )

        return candidate

    @staticmethod
    def _alarm_time(
        class_time: datetime,
        reminder: int,
    ) -> datetime:

        alarm = (
            class_time
            - timedelta(
                minutes=reminder
            )
        )

        if alarm <= datetime.now():

            return class_time

        return alarm

    @staticmethod
    def _next_future(
        t: datetime,
    ) -> datetime:

        now = datetime.now()

        while t <= now:

            t += timedelta(
                days=7
            )

        return t

    # ============================================================
    # STORAGE
    # ============================================================

    def _load_records(
        self,
    ) -> list[dict]:

        records = []

        if not ALERTS_FILE.exists():

            return records

        try:

            lines = ALERTS_FILE.read_text(
                encoding="utf-8"
            ).splitlines()

        except Exception as err:

            _log(
                f"STORAGE: read ERROR: {err}"
            )

            return records

        for line in lines:

            line = line.strip()

            if not line:
                continue

            try:

                parts = line.split(
                    "|"
                )

                if len(parts) == 6:

                    parts.append("")

                if len(parts) != 7:

                    _log(
                        "STORAGE: malformed record: "
                        f"{line}"
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

                records.append(
                    {
                        "id": int(id_),
                        "time": datetime.fromisoformat(
                            alarm
                        ),
                        "class_time": datetime.fromisoformat(
                            klass
                        ),
                        "reminder_before": int(
                            rem
                        ),
                        "subject": subject,
                        "grade": grade,
                        "speech": speech,
                    }
                )

            except (
                ValueError,
                TypeError,
            ):

                _log(
                    "STORAGE: skipping malformed entry: "
                    f"{line}"
                )

        return records

    def _save_records(
        self,
        records,
    ):

        try:

            ALERTS_FILE.parent.mkdir(
                parents=True,
                exist_ok=True,
            )

            temp_file = (
                ALERTS_FILE.with_suffix(
                    ".tmp"
                )
            )

            with open(
                temp_file,
                "w",
                encoding="utf-8",
            ) as f:

                for r in records:

                    f.write(
                        f"{r['id']}|"
                        f"{r['time'].isoformat()}|"
                        f"{r['class_time'].isoformat()}|"
                        f"{r['reminder_before']}|"
                        f"{r['subject']}|"
                        f"{r['grade']}|"
                        f"{r.get('speech', '')}\n"
                    )

            temp_file.replace(
                ALERTS_FILE
            )

        except Exception as err:

            _log(
                f"STORAGE: write ERROR: {err}"
            )

            print(
                f"Error saving alerts: {err}"
            )

    # ============================================================
    # ANDROID ALARMS
    # ============================================================

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
                + (
                    f", {grade}"
                    if grade
                    else ""
                )
                + ", class starts now."
            )

        title = (
            f"Class Starting: {subject}"
            + (
                f", grade {grade}"
                if grade
                else ""
            )
        )

        message = (
            f"Your {subject} class"
            f"{suffix} is ready."
        )

        _log(
            "alarm-schedule: "
            f"id={nt_id}; "
            f"time={when.isoformat()}; "
            f"title={title!r}; "
            f"speech={speech_text!r}"
        )

        # --------------------------------------------------------
        # IMPORTANT:
        #
        # repeat_weekly=True
        #
        # Kotlin AlarmReceiver will automatically schedule
        # the next occurrence seven days later.
        #
        # Python does NOT reschedule fired alarms.
        # --------------------------------------------------------

        FletAlarm().set_alarm(
            when,
            nt_id,
            title=title,
            message=message,
            speech_text=speech_text,
            repeat_weekly=True,
        )

        _log(
            f"alarm-schedule: SUCCESS id={nt_id}"
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
                f"alarm-cancel: id={nt_id}"
            )

        except Exception as err:

            _log(
                f"alarm-cancel ERROR id={nt_id}: {err}"
            )

            print(
                f"Alarm cancellation unavailable: {err}"
            )

        # --------------------------------------------------------
        # Python notification cancellation is kept only as
        # cleanup for notifications previously created by older
        # versions of the app.
        # --------------------------------------------------------

        try:

            Notification(
                id=nt_id
            ).cancel(
                nt_id
            )

        except Exception as err:

            _log(
                f"notification-cancel ERROR: {err}"
            )

    # ============================================================
    # TEST NOTIFICATION
    # ============================================================

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

            notif.icon_name = (
                "ic_lock_idle_alarm"
            )

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
                        sound_path=str(
                            sound_path
                        )
                    )

                except Exception as err:

                    _log(
                        f"notification sound ERROR: {err}"
                    )

        return notif

    @staticmethod
    def _send_notification(
        notif,
        body: str,
    ) -> bool:

        try:

            try:

                notif.setBigText(
                    body
                )

            except Exception:
                pass

            result = notif.send(
                silent=False
            )

            _log(
                f"notification: send returned {result}"
            )

            return True

        except Exception as err:

            _log(
                f"notification: SEND ERROR: {err}"
            )

            print(
                f"Notification send error: {err}"
            )

            return False

    # ============================================================
    # TOAST
    # ============================================================

    def _toast(
        self,
        message: str,
        ok: bool = True,
    ):

        try:

            self.page.show_dialog(
                ft.SnackBar(
                    content=ft.Text(
                        message
                    ),
                    bgcolor=(
                        ft.Colors.GREEN_400
                        if ok
                        else ft.Colors.RED_400
                    ),
                )
            )

        except Exception as err:

            _log(
                f"TOAST ERROR: {err}"
            )

    # ============================================================
    # RESTORE ALARMS
    # ============================================================

    async def restore_alarms(
        self,
    ):

        """
        Restore native Android alarms when the Flet app starts.

        This is NOT the alarm execution path.

        Android AlarmManager + Kotlin AlarmReceiver handles
        alarms while the application is closed.

        Python only recreates alarms after the application
        starts again.
        """

        try:

            records = self._load_records()

            if not records:

                _log(
                    "restore: no saved alarms"
                )

                return

            changed = False

            for r in records:

                # Move the stored occurrence into the future.
                class_time = (
                    self._next_future(
                        r["class_time"]
                    )
                )

                alarm = (
                    self._alarm_time(
                        class_time,
                        r["reminder_before"],
                    )
                )

                if (
                    class_time
                    != r["class_time"]
                    or alarm
                    != r["time"]
                ):

                    r["class_time"] = (
                        class_time
                    )

                    r["time"] = alarm

                    changed = True

                try:

                    self._schedule_alarm(
                        r["id"],
                        alarm,
                        r["subject"],
                        r["grade"],
                        r.get(
                            "speech",
                            "",
                        ),
                    )

                except Exception as err:

                    _log(
                        "restore: alarm "
                        f"{r['id']} ERROR: {err}"
                    )

                    print(
                        f"Alarm restore unavailable: {err}"
                    )

            if changed:

                self._save_records(
                    records
                )

            _log(
                f"restore: completed "
                f"{len(records)} alarms"
            )

        except Exception as err:

            _log(
                f"restore: ERROR: {err}"
            )

            print(
                f"Error restoring alarms: {err}"
            )

    # ============================================================
    # DEBUG LOG DISPLAY
    # ============================================================

    async def show_last_log(
        self,
    ):

        await asyncio.sleep(
            2
        )

        try:

            text = (
                LOG_FILE.read_text(
                    encoding="utf-8",
                    errors="replace",
                )
                if LOG_FILE.exists()
                else ""
            )

            LOG_FILE.write_text(
                "",
                encoding="utf-8",
            )

            _log(
                "app started"
            )

            if text.strip():

                self.page.show_dialog(
                    ft.AlertDialog(
                        title=ft.Text(
                            "Previous run log"
                        ),
                        content=ft.Column(
                            [
                                ft.Text(
                                    text[-5000:],
                                    selectable=True,
                                    size=11,
                                )
                            ],
                            scroll=(
                                ft.ScrollMode.AUTO
                            ),
                            height=400,
                        ),
                        actions=[
                            ft.TextButton(
                                "OK",
                                on_click=lambda _:
                                self.close_dialog(),
                            )
                        ],
                    )
                )

        except Exception as err:

            print(
                f"Log display failed: {err}"
            )

    # ============================================================
    # DIALOG
    # ============================================================

    def close_dialog(
        self,
    ):

        try:

            self.page.pop_dialog()

        except Exception:
            pass

        try:

            self.page.update()

        except Exception:
            pass

    # ============================================================
    # TEST NOTIFICATION + VOICE
    # ============================================================

    async def test_notification(
        self,
    ):

        body = (
            "If you see this, notifications are working!"
        )

        try:

            sent = (
                self._send_notification(
                    self._build_notification(
                        999,
                        "Test Notification",
                        body,
                    ),
                    body,
                )
            )

            self._toast(
                (
                    "Test sent! Check your notification tray."
                    if sent
                    else
                    "Test failed. Check Android notification settings."
                ),
                ok=sent,
            )

            asyncio.create_task(
                self.test_voice()
            )

        except Exception as err:

            _log(
                f"test notification ERROR: {err}"
            )

            self._toast(
                f"Error sending test notification: {err}",
                ok=False,
            )

    async def test_voice(
        self,
    ):

        """
        Test Python/Flet TTS only.

        Scheduled lesson alarms DO NOT use this function.
        """

        await asyncio.sleep(
            1
        )

        result = ""

        try:

            _log(
                "test: starting native TTS"
            )

            ready = (
                await self._ensure_tts_ready()
            )

            if ready:

                success = (
                    self._speak_alert(
                        "Voice test. "
                        "Mathematics, in two minutes."
                    )
                )

                if success:

                    result = (
                        "TTS READY\n\n"
                        f"Context: "
                        f"{getattr(self._tts, 'context_source', 'unknown')}\n\n"
                        "Speech command sent successfully.\n"
                        "You should hear a voice now."
                    )

                else:

                    result = (
                        "TTS READY, "
                        "but the speak command failed.\n\n"
                        f"Status: "
                        f"{getattr(self._tts, 'status', None)}\n"
                        f"Error: "
                        f"{getattr(self._tts, 'error', None)}"
                    )

            else:

                result = (
                    "TTS NOT READY.\n\n"
                    f"Status: "
                    f"{getattr(self._tts, 'status', None)}\n"
                    f"Error: "
                    f"{getattr(self._tts, 'error', None)}\n"
                    f"Context: "
                    f"{getattr(self._tts, 'context_source', 'unknown')}"
                )

            _log(
                "test: " + result
            )

        except Exception as err:

            result = (
                f"TTS CRASHED: {err}"
            )

            _log(
                "test: " + result
            )

        try:

            self.page.show_dialog(
                ft.AlertDialog(
                    title=ft.Text(
                        "Voice test"
                    ),
                    content=ft.Text(
                        result,
                        selectable=True,
                    ),
                    actions=[
                        ft.TextButton(
                            "OK",
                            on_click=lambda _:
                            self.close_dialog(),
                        )
                    ],
                )
            )

        except Exception as err:

            _log(
                f"Voice test dialog ERROR: {err}"
            )

    # ============================================================
    # PERMISSIONS
    # ============================================================

    async def request_permission(
        self,
    ):

        try:

            ph = (
                fph.PermissionHandler()
            )

            exact_alarm = await ph.request(
                fph.Permission.SCHEDULE_EXACT_ALARM
            )

            notification = await ph.request(
                fph.Permission.NOTIFICATION
            )

            overlay = await ph.request(
                fph.Permission.SYSTEM_ALERT_WINDOW
            )

            try:

                await ph.request(
                    fph.Permission.IGNORE_BATTERY_OPTIMIZATIONS
                )

            except Exception as err:

                _log(
                    "permission: battery optimization "
                    f"request ERROR: {err}"
                )

            checks = (
                (
                    exact_alarm,
                    "Schedule exact alarm permission is required "
                    "for timely class reminders.",
                ),
                (
                    notification,
                    "Notification permission is required "
                    "for timely class reminders.",
                ),
                (
                    overlay,
                    "Overlay permission is required to show "
                    "reminders on top of other apps.",
                ),
            )

            for result, message in checks:

                try:

                    if (
                        result is not None
                        and result.name
                        == "DENIED"
                    ):

                        self._toast(
                            message
                            + " Please enable it in settings.",
                            ok=False,
                        )

                except Exception as err:

                    _log(
                        "permission: result check ERROR: "
                        f"{err}"
                    )

            _log(
                "permission: permission requests completed"
            )

        except Exception as err:

            _log(
                f"permission: ERROR: {err}"
            )

            print(
                f"Permission request error: {err}"
            )


# ============================================================
# FLET ENTRY POINT
# ============================================================

def main(
    page: ft.Page,
):

    page.title = (
        "Smart Timetable Lesson Alert"
    )

    SmartAlert(
        page
    )


ft.run(
    main,
    assets_dir="assets",
)
