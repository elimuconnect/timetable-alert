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

    ALARM_ACTION_PREFIX = (
        "com.zaimtech.CLASS_ALERT_ALARM_"
    )

    def __init__(self, page: ft.Page):

        self.page = page

        # Prevent duplicate processing of the same Android intent.
        self._last_alarm_signature = None

        # Prevent two alarm handlers from running simultaneously.
        self._alarm_lock = asyncio.Lock()

        # Prevent two scheduled TTS engines from speaking together.
        self._tts_lock = asyncio.Lock()

        # --------------------------------------------------------
        # TTS STATE
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
                    on_click=lambda _: (
                        asyncio.create_task(
                            self.test_notification()
                        )
                    ),
                )
            ],
        )

        page.add(
            self.webview
        )

        # --------------------------------------------------------
        # STARTUP TASKS
        # --------------------------------------------------------

        for job in (
            self.request_permission,
            self.initialize_tts,
            self.restore_alarms,
            self.check_for_alarm_intent,
            self.monitor_alarm_intents,
            self.show_last_log,
        ):
            asyncio.create_task(
                job()
            )

        # --------------------------------------------------------
        # CHECK ALARM WHEN ROUTE / ACTIVITY CHANGES
        # --------------------------------------------------------

        page.on_route_change = (
            lambda _:
            asyncio.create_task(
                self.check_for_alarm_intent()
            )
        )

        page.on_resume = (
            lambda _:
            asyncio.create_task(
                self.check_for_alarm_intent()
            )
        )

        _log(
            "startup: SmartAlert initialized"
        )

    # ============================================================
    # NATIVE ANDROID TTS
    # ============================================================

    async def initialize_tts(self):

        """
        Initialize the persistent Android TTS engine.

        This engine is used for manual/test speech.
        Scheduled alarms create their own engine because
        Android may recreate the Python activity after an alarm.
        """

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
                f"context={getattr(self._tts, 'context_source', 'unknown')}"
            )

            ready = await asyncio.to_thread(
                self._tts.wait_ready,
                10,
            )

            if ready:

                _log(
                    "TTS startup: READY; "
                    f"status={getattr(self._tts, 'status', None)}"
                )

                return True

            _log(
                "TTS startup: NOT READY; "
                f"status={getattr(self._tts, 'status', None)}; "
                f"error={getattr(self._tts, 'error', None)}"
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

    async def _ensure_tts_ready(self):

        """
        Wait for the persistent TTS engine if initialization
        is already underway, otherwise initialize it.
        """

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

                    _log(
                        "TTS: initialization completed"
                    )

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

        """
        Speak using the persistent TTS engine.
        """

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
                    "TTS: engine not ready; "
                    f"status={getattr(self._tts, 'status', None)}; "
                    f"error={getattr(self._tts, 'error', None)}"
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

    async def _speak_scheduled_alarm(
        self,
        spoken: str,
    ) -> bool:

        """
        Speak a scheduled alarm using a fresh Android TTS
        instance.

        This is intentionally independent of the normal
        persistent TTS instance because Android alarms can
        launch/recreate the application process.
        """

        spoken = str(
            spoken or ""
        ).strip()

        if not spoken:

            _log(
                "ALARM TTS: empty speech text"
            )

            return False

        async with self._tts_lock:

            fresh_tts = None

            try:

                _log(
                    "ALARM TTS: creating fresh TTS"
                )

                fresh_tts = tts.TTS()

                _log(
                    "ALARM TTS: TTS created; "
                    f"context={getattr(fresh_tts, 'context_source', 'unknown')}"
                )

                ready = await asyncio.to_thread(
                    fresh_tts.wait_ready,
                    10,
                )

                if not ready:

                    _log(
                        "ALARM TTS: NOT READY; "
                        f"status={getattr(fresh_tts, 'status', None)}; "
                        f"error={getattr(fresh_tts, 'error', None)}"
                    )

                    return False

                _log(
                    "ALARM TTS: READY; "
                    f"status={getattr(fresh_tts, 'status', None)}"
                )

                # Give Android a short moment after initialization.
                await asyncio.sleep(
                    0.5
                )

                # Try the actual speech command.
                success = fresh_tts.speak(
                    spoken
                )

                _log(
                    f"ALARM TTS: speak returned {success}"
                )

                if not success:

                    # One retry can recover from occasional
                    # Android TTS initialization races.
                    _log(
                        "ALARM TTS: first speak failed; retrying"
                    )

                    await asyncio.sleep(
                        0.7
                    )

                    success = fresh_tts.speak(
                        spoken
                    )

                    _log(
                        f"ALARM TTS: retry speak returned {success}"
                    )

                if not success:

                    return False

                # Keep the TTS object alive until speech has had
                # enough time to finish.
                wait_seconds = max(
                    4.0,
                    min(
                        15.0,
                        2.0 + len(spoken) / 11.0,
                    ),
                )

                _log(
                    "ALARM TTS: keeping engine alive for "
                    f"{wait_seconds:.1f}s"
                )

                await asyncio.sleep(
                    wait_seconds
                )

                _log(
                    "ALARM TTS: speech completed"
                )

                return True

            except Exception as err:

                _log(
                    f"ALARM TTS: ERROR: {err}"
                )

                print(
                    f"Scheduled alarm TTS error: {err}"
                )

                return False

            finally:

                if fresh_tts is not None:

                    try:

                        fresh_tts.shutdown()

                        _log(
                            "ALARM TTS: fresh engine shutdown"
                        )

                    except Exception as err:

                        _log(
                            f"ALARM TTS: shutdown ERROR: {err}"
                        )

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

        """
        Open the Android file picker and send the selected DOCX
        to the WebView as base64.
        """

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
                f"FILE: DOCX delivered to WebView: "
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
    # TIMETABLE -> ALARMS
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

        # Cancel previous alarms.
        for old in self._load_records():

            self._cancel_alarm(
                old["id"]
            )

        self._save_records(
            records
        )

        # Schedule new alarms.
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

            except Exception as err:

                _log(
                    f"Alarm scheduling ERROR: {err}"
                )

                print(
                    f"Alarm scheduling unavailable: {err}"
                )

        return (
            len(records),
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
                f"TIMETABLE: skipping invalid lesson: "
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

        # Do not allow an unreasonable reminder value.
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

        # Remove punctuation commonly produced by DOCX tables.
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

            # Also accept common abbreviations.
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

        # Examples:
        # 8:20 - 9:00
        # 8:20 – 9:00
        # 8:20 — 9:00
        # 8:20 to 9:00
        start = re.split(
            r"\s*[-\u2013\u2014]\s*"
            r"|\s+to\s+",
            text,
            maxsplit=1,
            flags=re.I,
        )[0].strip()

        # Normalize spaces around AM/PM.
        start = re.sub(
            r"\s+",
            " ",
            start,
        ).strip()

        # Accept forms such as "8.20".
        for fmt in self.TIME_FORMATS:

            try:

                return datetime.strptime(
                    start.upper(),
                    fmt,
                ).time()

            except ValueError:
                continue

        # Additional robust handling of simple numeric times.
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

        # If today's lesson time has already passed, use next week.
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

        # If reminder time has already passed, don't schedule an
        # alarm in the past. Schedule the actual class time instead.
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

                # Backward compatibility with records that did
                # not yet contain speech.
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

            # Write to a temporary file first so a partial write
            # does not destroy the saved timetable.
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
    # ANDROID ALARMS / NOTIFICATIONS
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

        FletAlarm().set_alarm(
            when,
            nt_id,
            title=title,
            message=message,
            speech_text=speech_text,
            repeat_weekly=False,
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

        try:

            Notification(
                id=nt_id
            ).cancel(
                nt_id
            )

        except Exception as err:

            _log(
                f"notification-cancel ERROR id={nt_id}: {err}"
            )

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
    # CLASS DIALOG
    # ============================================================

    def _show_class_dialog(
        self,
        day: str,
        when: str,
        subject: str,
        grade: str,
    ):

        grade_line = (
            f"\nGrade: {grade}"
            if grade
            else ""
        )

        try:

            self.page.show_dialog(
                ft.AlertDialog(
                    title=ft.Text(
                        "Time for Class!"
                    ),
                    content=ft.Text(
                        f"It's {day} {when}.\n"
                        f"Subject: {subject}"
                        f"{grade_line}"
                    ),
                    actions=[
                        ft.TextButton(
                            "Dismiss",
                            on_click=lambda _:
                            self.close_dialog(),
                        )
                    ],
                )
            )

        except Exception as err:

            _log(
                f"DIALOG: ERROR: {err}"
            )

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
    # ALARM TRIGGER HANDLING
    # ============================================================

    def _reschedule_next(
        self,
        alarm_id: int,
    ):

        records = self._load_records()

        for r in records:

            if r["id"] != alarm_id:
                continue

            # The alarm that just fired belongs to this week's
            # occurrence. Move it exactly seven days forward.
            next_class = (
                r["class_time"]
                + timedelta(days=7)
            )

            r["class_time"] = (
                self._next_future(
                    next_class
                )
            )

            r["time"] = (
                self._alarm_time(
                    r["class_time"],
                    r["reminder_before"],
                )
            )

            try:

                self._schedule_alarm(
                    r["id"],
                    r["time"],
                    r["subject"],
                    r["grade"],
                    r.get(
                        "speech",
                        "",
                    ),
                )

                self._save_records(
                    records
                )

                _log(
                    "alarm-reschedule: "
                    f"id={alarm_id}; "
                    f"next_class={r['class_time']}; "
                    f"next_alarm={r['time']}"
                )

            except Exception as err:

                _log(
                    f"alarm-reschedule ERROR: {err}"
                )

            return

    def _find_triggered(
        self,
        now: datetime,
        alarm_id: int | None,
    ):

        records = self._load_records()

        # First preference: exact alarm ID supplied by Android.
        if alarm_id is not None:

            for r in records:

                if r["id"] == alarm_id:

                    return r

        # Fallback: compare weekday and minute.
        now_key = now.strftime(
            "%A %H:%M"
        )

        for r in records:

            if (
                r["time"].strftime(
                    "%A %H:%M"
                )
                == now_key
            ):

                return r

        # Second fallback: compare class time.
        for r in records:

            if (
                r["class_time"].strftime(
                    "%A %H:%M"
                )
                == now_key
            ):

                return r

        return None

    # ============================================================
    # READ ANDROID ALARM INTENT
    # ============================================================

    async def check_for_alarm_intent(
        self,
    ):

        # Only one alarm intent may be handled at a time.
        if self._alarm_lock.locked():

            return

        async with self._alarm_lock:

            try:

                _log(
                    "alarm-check: ENTERED"
                )

                from flet_alarm import (
                    PythonActivity,
                    cast,
                )

                if not PythonActivity:

                    _log(
                        "alarm-check: PythonActivity unavailable"
                    )

                    return

                if (
                    PythonActivity.mActivity
                    is None
                ):

                    _log(
                        "alarm-check: mActivity unavailable"
                    )

                    return

                activity = cast(
                    "android.app.Activity",
                    PythonActivity.mActivity,
                )

                intent = activity.getIntent()

                if intent is None:

                    _log(
                        "alarm-check: intent is None"
                    )

                    return

                # ------------------------------------------------
                # ACTION
                # ------------------------------------------------

                action = intent.getAction()

                _log(
                    f"alarm-check: action={action}"
                )

                # ------------------------------------------------
                # NORMAL ALARM FLAG
                # ------------------------------------------------

                is_alarm = (
                    intent.getBooleanExtra(
                        "is_alarm_trigger",
                        False,
                    )
                )

                # ------------------------------------------------
                # ALARM EXTRAS
                # ------------------------------------------------

                raw_alarm_id = (
                    intent.getIntExtra(
                        "alarm_id",
                        0,
                    )
                )

                raw_notification_id = (
                    intent.getIntExtra(
                        "notification_id",
                        0,
                    )
                )

                speech_extra = (
                    intent.getStringExtra(
                        "speech_text"
                    )
                )

                scheduled_at = (
                    intent.getLongExtra(
                        "scheduled_at_ms",
                        0,
                    )
                )

                _log(
                    "alarm-check: extras "
                    f"alarm_id={raw_alarm_id}; "
                    f"notification_id={raw_notification_id}; "
                    f"speech={speech_extra!r}; "
                    f"scheduled_at={scheduled_at}; "
                    f"is_alarm={is_alarm}"
                )

                # ------------------------------------------------
                # ACTION FALLBACK
                # ------------------------------------------------

                action_alarm_id = None

                if (
                    isinstance(
                        action,
                        str,
                    )
                    and action.startswith(
                        self.ALARM_ACTION_PREFIX
                    )
                ):

                    try:

                        action_alarm_id = int(
                            action.rsplit(
                                "_",
                                1,
                            )[1]
                        )

                        is_alarm = True

                        _log(
                            "alarm-check: recovered alarm ID "
                            f"from action={action_alarm_id}"
                        )

                    except (
                        ValueError,
                        IndexError,
                    ):

                        _log(
                            "alarm-check: invalid alarm action"
                        )

                # ------------------------------------------------
                # NO ALARM
                # ------------------------------------------------

                if not is_alarm:

                    return

                # ------------------------------------------------
                # DETERMINE IDS
                # ------------------------------------------------

                alarm_id = raw_alarm_id

                if (
                    alarm_id == 0
                    and action_alarm_id is not None
                ):

                    alarm_id = (
                        action_alarm_id
                    )

                nt_id = (
                    raw_notification_id
                )

                if nt_id == 0:

                    nt_id = alarm_id

                # ------------------------------------------------
                # DUPLICATE PROTECTION
                # ------------------------------------------------

                # If Android did not supply scheduled_at_ms,
                # use the current minute so repeated checks of
                # the same activity intent do not fire repeatedly.
                signature_time = (
                    scheduled_at
                    if scheduled_at
                    else int(
                        datetime.now().timestamp()
                        // 60
                    )
                )

                signature = (
                    alarm_id,
                    signature_time,
                )

                _log(
                    "alarm-check: trigger "
                    f"alarm_id={alarm_id}; "
                    f"notification_id={nt_id}; "
                    f"signature={signature}"
                )

                if (
                    signature
                    == self._last_alarm_signature
                ):

                    _log(
                        "alarm-check: duplicate ignored"
                    )

                    return

                # Mark BEFORE doing notification/TTS so the
                # 1-second monitor cannot enter again.
                self._last_alarm_signature = (
                    signature
                )

                # ------------------------------------------------
                # FIND SAVED RECORD
                # ------------------------------------------------

                now = datetime.now()

                record = self._find_triggered(
                    now,
                    alarm_id,
                )

                # ------------------------------------------------
                # BUILD NOTIFICATION
                # ------------------------------------------------

                if record:

                    title = (
                        "Class Starting: "
                        f"{record['subject']}"
                    )

                    if record["grade"]:

                        body = (
                            f"Grade: "
                            f"{record['grade']} "
                            "is waiting for you."
                        )

                    else:

                        body = (
                            "Your lesson is starting."
                        )

                    nt_id = record["id"]

                else:

                    title = (
                        intent.getStringExtra(
                            "notification_title"
                        )
                        or "Class Reminder"
                    )

                    body = (
                        intent.getStringExtra(
                            "notification_body"
                        )
                        or "Check your timetable."
                    )

                # ------------------------------------------------
                # SEND NOTIFICATION FIRST
                # ------------------------------------------------

                notification = (
                    self._build_notification(
                        nt_id,
                        title,
                        body,
                        True,
                    )
                )

                notification_sent = (
                    self._send_notification(
                        notification,
                        body,
                    )
                )

                _log(
                    "alarm-check: notification "
                    f"sent={notification_sent}"
                )

                # ------------------------------------------------
                # BUILD SPEECH
                # ------------------------------------------------

                if record:

                    spoken = (
                        record.get(
                            "speech",
                            "",
                        ).strip()
                        or (
                            record["subject"]
                            + (
                                f", {record['grade']}"
                                if record["grade"]
                                else ""
                            )
                            + ", starts now."
                        )
                    )

                else:

                    spoken = (
                        speech_extra
                        or body
                    )

                spoken = str(
                    spoken
                ).strip()

                _log(
                    f"alarm-check: speech={spoken!r}"
                )

                # ------------------------------------------------
                # SPEAK
                # ------------------------------------------------

                try:

                    success = (
                        await self._speak_scheduled_alarm(
                            spoken
                        )
                    )

                    _log(
                        "alarm-check: TTS "
                        f"success={success}"
                    )

                except Exception as err:

                    _log(
                        f"alarm-check: TTS ERROR: {err}"
                    )

                # ------------------------------------------------
                # SHOW DIALOG
                # ------------------------------------------------

                if record:

                    ct = record[
                        "class_time"
                    ]

                    self._show_class_dialog(
                        ct.strftime(
                            "%A"
                        ),
                        ct.strftime(
                            "%H:%M"
                        ),
                        record[
                            "subject"
                        ],
                        record[
                            "grade"
                        ],
                    )

                # ------------------------------------------------
                # MOVE TO NEXT WEEK
                # ------------------------------------------------

                if record:

                    self._reschedule_next(
                        record["id"]
                    )

                elif alarm_id:

                    self._reschedule_next(
                        alarm_id
                    )

                # ------------------------------------------------
                # REMOVE ALARM MARKERS
                # ------------------------------------------------

                for key in (
                    "is_alarm_trigger",
                    "alarm_id",
                    "notification_id",
                    "notification_title",
                    "notification_body",
                    "speech_text",
                    "scheduled_at_ms",
                    "wake_for_alarm",
                ):

                    try:

                        intent.removeExtra(
                            key
                        )

                    except Exception:
                        pass

                # Clear the action after processing.
                try:

                    intent.setAction(
                        None
                    )

                except Exception:
                    pass

                _log(
                    "alarm-check: handled successfully "
                    f"id={alarm_id}"
                )

            except Exception as err:

                _log(
                    f"alarm-check: ERROR: {err}"
                )

                print(
                    f"Intent check error: {err}"
                )

    async def monitor_alarm_intents(
        self,
    ):

        """
        Keep checking the current Android activity intent.

        This is useful when the alarm opens/resumes the Python
        activity rather than starting a completely new process.
        """

        while True:

            try:

                await self.check_for_alarm_intent()

            except Exception as err:

                _log(
                    f"alarm-monitor ERROR: {err}"
                )

            await asyncio.sleep(
                1
            )

    # ============================================================
    # RESTORE ALARMS
    # ============================================================

    async def restore_alarms(
        self,
    ):

        """
        Restore all saved timetable alarms after app startup.
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

                # Move old occurrences forward until they are
                # in the future.
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
                f"restore: completed {len(records)} alarms"
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

        """
        Display the previous run's debug log.
        """

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

            # Start a fresh log for the current run.
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

            # Run voice test independently.
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
        Test persistent native Android TTS.
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

                _log(
                    "test: TTS READY"
                )

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

            # Battery optimization permission.
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
