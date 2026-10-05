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

# ---------------- Native Android Text-to-Speech ----------------
try:
    from jnius import autoclass, PythonJavaClass, java_method
except ImportError:
    autoclass = None
    PythonJavaClass = None
    java_method = None

BASE_DIR = pathlib.Path(__file__).resolve().parent
# Writable app storage on Android; falls back to the source folder on desktop.
STORAGE_DIR = pathlib.Path(os.environ.get("FLET_APP_STORAGE_DATA", str(BASE_DIR)))
ALERTS_FILE = STORAGE_DIR / "alerts.txt"


# ============================================================
# ANDROID TEXT-TO-SPEECH INITIALIZATION LISTENER
# ============================================================
if PythonJavaClass is not None:

    class TTSInitListener(PythonJavaClass):
        __javainterfaces__ = ["android/speech/tts/TextToSpeech$OnInitListener"]
        __javacontext__ = "app"

        def __init__(self, owner):
            super().__init__()
            self.owner = owner

        @java_method("(I)V")
        def onInit(self, status):
            self.owner._tts_initialized(status)


class SmartAlert:
    DEFAULT_REMINDER_MINUTES = 5
    DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    TIME_FORMATS = ("%H:%M", "%H.%M", "%I:%M %p", "%I:%M%p", "%I %p")

    def __init__(self, page: ft.Page):
        self.page = page
        self._last_alarm_signature = None

        # Native Android Text-to-Speech
        self.tts = None
        self.tts_ready = False
        self.tts_listener = None
        self._pending_speech = None
        self._initialize_native_tts()

        html = (BASE_DIR / "index.html").read_text(encoding="utf-8")
        self.webview = fwa.FletWebviewAll(
            html=html,
            expand=True,
            allow_webview_permissions=True,
            background_color=ft.Colors.BLACK,
        )
        self.webview.javascript_channels = ["FletBridge"]
        self.webview.on_javascript_message = self.on_message

        self.picker = ft.FilePicker()
        try:
            page.services.append(self.picker)
        except AttributeError:
            page.overlay.append(self.picker)

        page.appbar = ft.AppBar(
            title=ft.Text("Timetable Lesson Alert"),
            center_title=True,
            bgcolor=ft.Colors.BLUE,
            actions=[
                ft.IconButton(
                    icon=ft.Icons.VOLUME_UP,
                    tooltip="Test voice",
                    icon_color=ft.Colors.GREEN,
                    on_click=lambda _: asyncio.create_task(self.test_voice()),
                )
            ],
        )
        page.add(self.webview)

        for job in (
            self.request_permission,
            self.restore_alarms,
            self.check_for_alarm_intent,
            self.monitor_alarm_intents,
        ):
            asyncio.create_task(job())

        page.on_route_change = lambda _: asyncio.create_task(self.check_for_alarm_intent())
        page.on_resume = lambda _: asyncio.create_task(self.check_for_alarm_intent())

    # ============================================================
    # NATIVE ANDROID TEXT-TO-SPEECH
    # ============================================================
    def _initialize_native_tts(self):
        if autoclass is None:
            print("🔊 Native Android TTS unavailable: Pyjnius is not installed.")
            return

        if self.tts is not None:
            return  # already requested

        try:
            activity_host_class = os.getenv("MAIN_ACTIVITY_HOST_CLASS_NAME")
            if not activity_host_class:
                print("🔊 MAIN_ACTIVITY_HOST_CLASS_NAME is not available.")
                return

            ActivityHost = autoclass(activity_host_class)
            activity = ActivityHost.mActivity
            if activity is None:
                print("🔊 Android activity is not available yet.")
                return

            TextToSpeech = autoclass("android.speech.tts.TextToSpeech")
            self.tts_listener = TTSInitListener(self)
            self.tts = TextToSpeech(activity, self.tts_listener)
            print("🔊 Android Text-to-Speech initialization requested.")

        except Exception as err:
            self.tts = None
            self.tts_ready = False
            print("🔊 Native TTS initialization error:", err)

    def _tts_initialized(self, status):
        try:
            TextToSpeech = autoclass("android.speech.tts.TextToSpeech")

            if status != TextToSpeech.SUCCESS:
                self.tts_ready = False
                print("🔊 Android TTS initialization FAILED:", status)
                return

            Locale = autoclass("java.util.Locale")
            result = self.tts.setLanguage(Locale("en", "US"))

            if result == TextToSpeech.LANG_MISSING_DATA:
                self.tts_ready = False
                print("🔊 Android TTS language data is missing.")
                return

            if result == TextToSpeech.LANG_NOT_SUPPORTED:
                self.tts_ready = False
                print("🔊 Android TTS English language is not supported.")
                return

            self.tts.setSpeechRate(0.95)
            self.tts.setPitch(1.0)
            self.tts_ready = True
            print("🔊 Android Native TTS READY.")

            # If an alarm fired before TTS finished starting, say it now.
            if self._pending_speech:
                pending, self._pending_speech = self._pending_speech, None
                self.speak_native(pending)

        except Exception as err:
            self.tts_ready = False
            print("🔊 TTS setup error:", err)

    def speak_native(self, text):
        if not text:
            return False

        # Lazy retry: the activity may not have existed during __init__.
        if self.tts is None:
            self._initialize_native_tts()

        if self.tts is None:
            print("🔊 Native TTS object is not available.")
            return False

        if not self.tts_ready:
            print("🔊 Native TTS is not ready yet; will speak once ready.")
            self._pending_speech = str(text)
            return False

        try:
            TextToSpeech = autoclass("android.speech.tts.TextToSpeech")
            AudioManager = autoclass("android.media.AudioManager")
            Bundle = autoclass("android.os.Bundle")

            self.tts.stop()

            params = Bundle()
            params.putInt(TextToSpeech.Engine.KEY_PARAM_STREAM, AudioManager.STREAM_MUSIC)
            params.putFloat(TextToSpeech.Engine.KEY_PARAM_VOLUME, 1.0)

            result = self.tts.speak(
                str(text),
                TextToSpeech.QUEUE_FLUSH,
                params,
                "lesson_alert",
            )
            print("🔊 NATIVE TTS SPEAK:", text, "RESULT:", result)

            if result == TextToSpeech.SUCCESS:
                return True

            print("🔊 Android TTS rejected the speech request:", result)
            return False

        except Exception as err:
            print("🔊 Native TTS speak error:", err)
            return False

    async def test_voice(self):
        if not self.tts_ready:
            self._initialize_native_tts()
            self._toast(
                "Android voice is not ready yet. Please wait a moment and try again.",
                ok=False,
            )
            print("🔊 TEST VOICE: TTS NOT READY")
            return

        text = "Teacher Brian, this is a test of the timetable lesson alert voice."
        if self.speak_native(text):
            self._toast("Voice test sent to Android Text-to-Speech.")
        else:
            self._toast("Android Text-to-Speech could not speak.", ok=False)

    # ---------------- WebView -> Python ----------------
    def on_message(self, e):
        body = e.message_body
        try:
            data = json.loads(body) if isinstance(body, (str, bytes, bytearray)) else body
        except (ValueError, TypeError):
            data = None

        if isinstance(data, dict) and data.get("action") == "pick_docx":
            asyncio.create_task(self.pick_docx())
            return

        try:
            lessons = self._parse_payload(data)
        except (ValueError, TypeError) as err:
            print(f"Bad timetable payload: {err}")
            self._toast("Could not read the timetable from the page.", ok=False)
            return

        scheduled, skipped = self.sync_timetable(lessons)
        if scheduled == 0 and skipped:
            self._toast("No valid lessons found. Existing alerts were left unchanged.", ok=False)
        elif skipped:
            self._toast(f"{scheduled} lesson alerts set, {skipped} skipped.")
        else:
            self._toast(f"{scheduled} lesson alerts set.")

    @staticmethod
    def _parse_payload(data) -> list:
        if isinstance(data, dict):
            for key in ("lessons", "timetable", "table", "data"):
                if isinstance(data.get(key), list):
                    return data[key]
            return [data]
        if isinstance(data, list):
            return data
        raise TypeError(f"Unsupported payload type: {type(data).__name__}")

    async def pick_docx(self):
        """Open the system file picker and hand the .docx to the page as base64."""
        try:
            files = await self.picker.pick_files(
                allow_multiple=False, allowed_extensions=["docx"], with_data=True
            )
            if not files:
                return
            f = files[0]
            data = getattr(f, "bytes", None)
            if not data and getattr(f, "path", None):
                data = pathlib.Path(f.path).read_bytes()
            if not data:
                self._toast("Could not read the selected file.", ok=False)
                return
            b64 = base64.b64encode(data).decode()
            await self.webview.run_javascript(
                f"receiveDocx({json.dumps(f.name)}, {json.dumps(b64)})"
            )
        except Exception as err:
            print(f"File pick failed: {err}")
            self._toast(f"File pick failed: {err}", ok=False)

    # ---------------- Timetable -> alarms ----------------
    def sync_timetable(self, lessons: list) -> tuple[int, int]:
        records, skipped = [], 0
        for lesson in lessons:
            record = self._build_record(lesson, len(records) + 1)
            if record is None:
                skipped += 1
            else:
                records.append(record)

        if lessons and not records:  # all invalid: keep existing alerts
            return 0, skipped

        for old in self._load_records():
            self._cancel_alarm(old["id"])
        self._save_records(records)

        for r in records:
            try:
                self._schedule_alarm(r["id"], r["time"], r["subject"], r["grade"])
            except Exception as err:
                print(f"Alarm scheduling unavailable: {err}")
        return len(records), skipped

    def _build_record(self, lesson, nt_id: int):
        if not isinstance(lesson, dict):
            return None
        row = {str(k).lower(): v for k, v in lesson.items()}

        day = self._parse_day(row.get("day"))
        start = self._parse_start_time(row.get("time"))
        subject = self._clean(row.get("lesson") or row.get("subject"))
        if day is None or start is None or not subject:
            print(f"Skipping invalid lesson: {lesson}")
            return None

        grade = self._clean(row.get("class") or row.get("grade"))
        reminder = self._to_int(row.get("reminder"), self.DEFAULT_REMINDER_MINUTES)
        class_time = self._next_weekday(day, start)
        return {
            "id": nt_id,
            "time": self._alarm_time(class_time, reminder),
            "class_time": class_time,
            "reminder_before": reminder,
            "subject": subject,
            "grade": grade,
        }

    # ---------------- Parsing / time helpers ----------------
    @staticmethod
    def _clean(value) -> str:
        return str(value or "").replace("|", " ").replace("\n", " ").strip()

    @staticmethod
    def _to_int(value, default: int) -> int:
        try:
            n = int(str(value).split()[0])
            return n if n >= 0 else default
        except (ValueError, IndexError):
            return default

    def _parse_day(self, value) -> int | None:
        text = str(value or "").strip().lower()
        if len(text) < 3:
            return None
        for i, name in enumerate(self.DAYS):
            if name.lower().startswith(text):
                return i
        return None

    def _parse_start_time(self, value) -> time | None:
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

    @staticmethod
    def _next_weekday(day_index: int, start: time) -> datetime:
        now = datetime.now()
        ahead = (day_index - now.weekday()) % 7
        candidate = datetime.combine(now.date() + timedelta(days=ahead), start)
        return candidate + timedelta(days=7) if candidate <= now else candidate

    @staticmethod
    def _alarm_time(class_time: datetime, reminder: int) -> datetime:
        alarm = class_time - timedelta(minutes=reminder)
        return class_time if alarm <= datetime.now() else alarm

    @staticmethod
    def _next_future(t: datetime) -> datetime:
        now = datetime.now()
        while t <= now:
            t += timedelta(days=7)
        return t

    # ---------------- Storage ----------------
    def _load_records(self) -> list[dict]:
        records = []
        if not ALERTS_FILE.exists():
            return records
        for line in ALERTS_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                id_, alarm, klass, rem, subject, grade = line.split("|")
                records.append(
                    {
                        "id": int(id_),
                        "time": datetime.fromisoformat(alarm),
                        "class_time": datetime.fromisoformat(klass),
                        "reminder_before": int(rem),
                        "subject": subject,
                        "grade": grade,
                    }
                )
            except ValueError:
                print(f"Skipping malformed alert entry: {line}")
        return records

    def _save_records(self, records):
        ALERTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(ALERTS_FILE, "w", encoding="utf-8") as f:
            for r in records:
                f.write(
                    f"{r['id']}|{r['time'].isoformat()}|{r['class_time'].isoformat()}"
                    f"|{r['reminder_before']}|{r['subject']}|{r['grade']}\n"
                )

    # ---------------- Alarms / notifications ----------------
    def _schedule_alarm(self, nt_id: int, when: datetime, subject: str, grade: str):
        from flet_alarm import FletAlarm

        suffix = f" for {grade}" if grade else ""
        FletAlarm().set_alarm(
            when,
            nt_id,
            title=f"Class Starting: {subject}" + (f", grade {grade}" if grade else ""),
            message=f"Your {subject} class{suffix} is ready.",
            repeat_weekly=False,
        )

    def _cancel_alarm(self, nt_id: int):
        try:
            from flet_alarm import FletAlarm

            FletAlarm().cancel_alarm(nt_id)
        except Exception as err:
            print(f"Alarm cancellation unavailable: {err}")
        try:
            Notification(id=nt_id).cancel(nt_id)
        except Exception as err:
            print(f"Notification cancel error: {err}")

    @staticmethod
    def _build_notification(nt_id: int, title: str, body: str, custom_sound: bool = False):
        notif = Notification(id=nt_id, title=title, message=body)
        notif.icon_name = "ic_lock_idle_alarm"
        if custom_sound:
            notif.setSound(sound_path=str(BASE_DIR / "assets" / "alarmsound.mp3"))
        return notif

    @staticmethod
    def _send_notification(notif, body: str) -> bool:
        try:
            try:
                notif.setBigText(body)
            except Exception:
                pass
            notif.send(silent=False)
            return True
        except Exception as err:
            print(f"Notification send error: {err}")
            return False

    def _show_class_dialog(self, day: str, when: str, subject: str, grade: str):
        grade_line = f"\nGrade: {grade}" if grade else ""
        self.page.show_dialog(
            ft.AlertDialog(
                title=ft.Text("Time for Class!"),
                content=ft.Text(f"It's {day} {when}.\nSubject: {subject}{grade_line}"),
                actions=[ft.TextButton("Dismiss", on_click=lambda _: self.close_dialog())],
            )
        )

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

    # ---------------- Alarm trigger handling ----------------
    def _reschedule_next(self, alarm_id: int):
        records = self._load_records()
        for r in records:
            if r["id"] != alarm_id:
                continue
            r["class_time"] = self._next_future(r["class_time"] + timedelta(days=7))
            r["time"] = self._alarm_time(r["class_time"], r["reminder_before"])
            self._schedule_alarm(r["id"], r["time"], r["subject"], r["grade"])
            self._save_records(records)
            return

    def _find_triggered(self, now: datetime, alarm_id: int | None):
        records = self._load_records()
        for r in records:
            if alarm_id is not None and r["id"] == alarm_id:
                return r
        for r in records:
            if r["time"].strftime("%A %H:%M") == now.strftime("%A %H:%M"):
                return r
        return None

    async def check_for_alarm_intent(self):
        try:
            from flet_alarm import PythonActivity, cast

            if not PythonActivity or PythonActivity.mActivity is None:
                return
            activity = cast("android.app.Activity", PythonActivity.mActivity)
            intent = activity.getIntent()
            if intent is None or not intent.getBooleanExtra("is_alarm_trigger", False):
                return

            nt_id = intent.getIntExtra("notification_id", 0)
            alarm_id = intent.getIntExtra("alarm_id", nt_id)
            signature = (alarm_id, intent.getLongExtra("scheduled_at_ms", 0))
            if signature == self._last_alarm_signature:
                return
            self._last_alarm_signature = signature

            record = self._find_triggered(datetime.now(), alarm_id)
            if record:
                title = f"Class Starting: {record['subject']}"
                body = (
                    f"Grade: {record['grade']} is waiting for you."
                    if record["grade"]
                    else "Your lesson is starting."
                )
                nt_id = record["id"]
            else:
                title = intent.getStringExtra("notification_title") or "Class Reminder"
                body = intent.getStringExtra("notification_body") or "Check your timetable."

            self._send_notification(self._build_notification(nt_id, title, body, True), body)

            # ====================================================
            # NATIVE ANDROID VOICE ALERT
            # ====================================================
            if record:
                minutes = record["reminder_before"]
                unit = "minute" if minutes == 1 else "minutes"
                voice_text = f"Teacher, in {minutes} {unit} go to {record['subject']}"
                if record["grade"]:
                    voice_text += f", {record['grade']}"
                self.speak_native(voice_text)
            else:
                self.speak_native(body)

            if record:
                ct = record["class_time"]
                self._show_class_dialog(
                    ct.strftime("%A"), ct.strftime("%H:%M"), record["subject"], record["grade"]
                )
            self._reschedule_next(alarm_id)

            for key in ("is_alarm_trigger", "notification_id", "notification_title", "notification_body"):
                try:
                    intent.removeExtra(key)
                except Exception:
                    pass
        except Exception as err:
            print(f"Intent check error: {err}")

    async def monitor_alarm_intents(self):
        while True:
            await self.check_for_alarm_intent()
            await asyncio.sleep(1)

    async def restore_alarms(self):
        """Roll stored lessons to their next occurrence and re-arm the alarms."""
        try:
            records = self._load_records()
            changed = False
            for r in records:
                class_time = self._next_future(r["class_time"])
                alarm = self._alarm_time(class_time, r["reminder_before"])
                if class_time != r["class_time"] or alarm != r["time"]:
                    r["class_time"], r["time"] = class_time, alarm
                    changed = True
                try:
                    self._schedule_alarm(r["id"], alarm, r["subject"], r["grade"])
                except Exception as err:
                    print(f"Alarm restore unavailable: {err}")
            if changed:
                self._save_records(records)
        except Exception as err:
            print(f"Error restoring alarms: {err}")

    # ---------------- Test + permissions ----------------
    async def test_notification(self):
        body = "If you see this, notifications are working!"
        try:
            sent = self._send_notification(
                self._build_notification(999, "Test Notification", body), body
            )
            self._toast(
                "Test sent! Check your notification tray."
                if sent
                else "Test failed. Check Android notification settings.",
                ok=sent,
            )
        except Exception as err:
            self._toast(f"Error sending test notification: {err}", ok=False)

    async def request_permission(self):
        ph = fph.PermissionHandler()
        exact_alarm = await ph.request(fph.Permission.SCHEDULE_EXACT_ALARM)
        notification = await ph.request(fph.Permission.NOTIFICATION)
        overlay = await ph.request(fph.Permission.SYSTEM_ALERT_WINDOW)
        await ph.request(fph.Permission.IGNORE_BATTERY_OPTIMIZATIONS)

        for result, text in (
            (exact_alarm, "Schedule exact alarm permission is required for timely class reminders."),
            (notification, "Notification permission is required for timely class reminders."),
            (overlay, "Overlay permission is required to show reminders on top of other apps."),
        ):
            if result.name == "DENIED":
                self._toast(text + " Please enable it in settings.", ok=False)


def main(page: ft.Page):
    page.title = "Smart Timetable Lesson Alert"
    SmartAlert(page)


ft.run(main, assets_dir="assets")
