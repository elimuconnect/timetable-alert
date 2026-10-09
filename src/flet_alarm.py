import datetime
import time
import traceback

from jnius import autoclass, cast


# ============================================================
# CONSTANTS
# ============================================================

RECEIVER_CLASS = "org.digielimu.classalert.AlarmReceiver"
ACTION_PREFIX = "com.zaimtech.CLASS_ALERT_ALARM_"

EXTRA_ALARM_ID = "alarm_id"
EXTRA_NOTIFICATION_ID = "notification_id"
EXTRA_NOTIFICATION_TITLE = "notification_title"
EXTRA_NOTIFICATION_BODY = "notification_body"
EXTRA_SPEECH_TEXT = "speech_text"
EXTRA_SCHEDULED_AT_MS = "scheduled_at_ms"
EXTRA_REPEAT_WEEKLY = "repeat_weekly"


# ============================================================
# LOGGING
# ============================================================

def _log(message):
    try:
        print(f"[FletAlarm] {message}", flush=True)
    except Exception:
        pass


def _log_exception(label, exc):
    _log(f"{label}: {type(exc).__name__}: {exc}")
    try:
        _log(traceback.format_exc())
    except Exception:
        pass


# ============================================================
# ANDROID APPLICATION CONTEXT
# ============================================================

def _get_application_context():
    """Obtain the Android application context."""
    ActivityThread = autoclass("android.app.ActivityThread")
    application = ActivityThread.currentApplication()

    if application is None:
        raise RuntimeError(
            "Android currentApplication() returned None."
        )

    context = application.getApplicationContext()

    if context is None:
        raise RuntimeError(
            "Android returned a null application context."
        )

    return cast("android.content.Context", context)


# ============================================================
# FLET ALARM
# ============================================================

class FletAlarm:
    def __init__(self):
        try:
            _log("Initializing Android alarm services...")

            # Load Build.VERSION as its own Java nested class.
            self.VERSION = autoclass("android.os.Build$VERSION")
            self.sdk = int(self.VERSION.SDK_INT)
            _log(f"Android SDK loaded: {self.sdk}")

            self.Context = autoclass("android.content.Context")
            self.Intent = autoclass("android.content.Intent")
            self.PendingIntent = autoclass("android.app.PendingIntent")
            self.AlarmManager = autoclass("android.app.AlarmManager")
            self.Settings = autoclass("android.provider.Settings")
            self.Uri = autoclass("android.net.Uri")
            self.NotificationManager = autoclass(
                "android.app.NotificationManager"
            )

            _log("Android framework classes loaded")

            self.context = _get_application_context()
            self.package_name = str(self.context.getPackageName())

            service = self.context.getSystemService(
                self.Context.ALARM_SERVICE
            )

            if service is None:
                raise RuntimeError(
                    "Android AlarmManager service is unavailable."
                )

            self.alarm_manager = cast(
                "android.app.AlarmManager",
                service,
            )

            _log(
                f"Initialized successfully; "
                f"package={self.package_name}; sdk={self.sdk}"
            )

        except Exception as exc:
            _log_exception("INITIALIZATION FAILED", exc)
            raise

    # ========================================================
    # EXACT ALARM PERMISSION
    # ========================================================

    def can_schedule_exact_alarms(self):
        if self.sdk < 31:
            return True

        try:
            return bool(
                self.alarm_manager.canScheduleExactAlarms()
            )
        except Exception as exc:
            _log_exception("Exact alarm permission check failed", exc)
            return False

    def open_exact_alarm_settings(self):
        if self.sdk < 31:
            return True

        try:
            intent = self.Intent(
                self.Settings.ACTION_REQUEST_SCHEDULE_EXACT_ALARM
            )
            intent.setData(
                self.Uri.parse(f"package:{self.package_name}")
            )
            intent.addFlags(
                int(self.Intent.FLAG_ACTIVITY_NEW_TASK)
            )
            self.context.startActivity(intent)

            _log("Opened exact alarm settings")
            return True

        except Exception as exc:
            _log_exception("Could not open exact alarm settings", exc)
            return False

    # ========================================================
    # PENDING INTENT FLAGS
    # ========================================================

    def _pending_intent_flags(self, update=False, no_create=False):
        flags = 0

        if self.sdk >= 23:
            flags |= int(self.PendingIntent.FLAG_IMMUTABLE)

        if update:
            flags |= int(self.PendingIntent.FLAG_UPDATE_CURRENT)

        if no_create:
            flags |= int(self.PendingIntent.FLAG_NO_CREATE)

        return flags

    # ========================================================
    # BUILD RECEIVER INTENT
    # ========================================================

    def _build_intent(
        self,
        alarm_id,
        title=None,
        message=None,
        speech_text=None,
        scheduled_at_ms=None,
        repeat_weekly=True,
    ):
        alarm_id = int(alarm_id)
        intent = self.Intent()

        intent.setClassName(self.context, RECEIVER_CLASS)
        intent.setAction(f"{ACTION_PREFIX}{alarm_id}")

        intent.putExtra(EXTRA_ALARM_ID, alarm_id)
        intent.putExtra(EXTRA_NOTIFICATION_ID, alarm_id)

        if title is not None:
            intent.putExtra(EXTRA_NOTIFICATION_TITLE, str(title))

        if message is not None:
            intent.putExtra(EXTRA_NOTIFICATION_BODY, str(message))

        if speech_text is not None:
            intent.putExtra(EXTRA_SPEECH_TEXT, str(speech_text))

        if scheduled_at_ms is not None:
            # Sent as a String: Pyjnius can mis-type large integers
            # (millisecond timestamps overflow a Java int). The Kotlin
            # receiver parses this back to a Long.
            intent.putExtra(
                EXTRA_SCHEDULED_AT_MS,
                str(int(scheduled_at_ms)),
            )

        intent.putExtra(EXTRA_REPEAT_WEEKLY, bool(repeat_weekly))
        return intent

    def _get_pending_intent(
        self,
        alarm_id,
        update=False,
        no_create=False,
        title=None,
        message=None,
        speech_text=None,
        scheduled_at_ms=None,
        repeat_weekly=True,
    ):
        intent = self._build_intent(
            alarm_id=alarm_id,
            title=title,
            message=message,
            speech_text=speech_text,
            scheduled_at_ms=scheduled_at_ms,
            repeat_weekly=repeat_weekly,
        )

        return self.PendingIntent.getBroadcast(
            self.context,
            int(alarm_id),
            intent,
            self._pending_intent_flags(
                update=update,
                no_create=no_create,
            ),
        )

    # ========================================================
    # SCHEDULE ALARM
    # ========================================================

    def set_alarm(
        self,
        scheduled_time,
        alarm_id,
        title="Class Reminder",
        message="Check your timetable.",
        speech_text="",
        repeat_weekly=True,
    ):
        if not isinstance(scheduled_time, datetime.datetime):
            raise TypeError(
                "scheduled_time must be datetime.datetime"
            )

        alarm_id = int(alarm_id)
        if alarm_id <= 0:
            raise ValueError("alarm_id must be greater than zero.")

        trigger_ms = int(scheduled_time.timestamp() * 1000)
        now_ms = int(time.time() * 1000)

        if trigger_ms <= now_ms:
            raise ValueError(
                f"Alarm time is in the past: {scheduled_time.isoformat()}"
            )

        if not self.can_schedule_exact_alarms():
            raise PermissionError(
                "Exact alarms are not permitted. Enable exact-alarm "
                "access in Android settings."
            )

        pending_intent = self._get_pending_intent(
            alarm_id=alarm_id,
            update=True,
            title=title,
            message=message,
            speech_text=speech_text,
            scheduled_at_ms=trigger_ms,
            repeat_weekly=repeat_weekly,
        )

        if pending_intent is None:
            raise RuntimeError(
                "Android failed to create the alarm PendingIntent."
            )

        try:
            self.alarm_manager.cancel(pending_intent)

            if self.sdk >= 23:
                self.alarm_manager.setExactAndAllowWhileIdle(
                    self.AlarmManager.RTC_WAKEUP,
                    trigger_ms,
                    pending_intent,
                )
            else:
                self.alarm_manager.setExact(
                    self.AlarmManager.RTC_WAKEUP,
                    trigger_ms,
                    pending_intent,
                )

        except Exception as exc:
            _log_exception(f"SET ALARM FAILED; id={alarm_id}", exc)
            raise

        _log(
            f"Scheduled id={alarm_id}; "
            f"time={scheduled_time.isoformat()}; "
            f"trigger_ms={trigger_ms}; "
            f"repeat_weekly={bool(repeat_weekly)}"
        )
        return True

    # ========================================================
    # CANCEL ONE ALARM
    # ========================================================

    def cancel_alarm(self, alarm_id):
        try:
            alarm_id = int(alarm_id)
            if alarm_id <= 0:
                raise ValueError("alarm_id must be greater than zero.")

            pending_intent = self._get_pending_intent(
                alarm_id=alarm_id,
                no_create=True,
            )

            if pending_intent is not None:
                self.alarm_manager.cancel(pending_intent)
                pending_intent.cancel()

            manager = self.context.getSystemService(
                self.Context.NOTIFICATION_SERVICE
            )

            if manager is not None:
                manager = cast(
                    "android.app.NotificationManager",
                    manager,
                )
                manager.cancel(alarm_id)

            _log(f"Cancelled alarm id={alarm_id}")
            return True

        except Exception as exc:
            _log_exception(f"CANCEL ALARM FAILED; id={alarm_id}", exc)
            return False

    # ========================================================
    # CANCEL MULTIPLE ALARMS
    # ========================================================

    def cancel_all(self, alarm_ids):
        success = True

        for alarm_id in alarm_ids:
            if not self.cancel_alarm(alarm_id):
                success = False

        return success
