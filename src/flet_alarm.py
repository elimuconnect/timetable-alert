
import datetime
import time

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


def _log(message):
    try:
        print(f"[FletAlarm] {message}", flush=True)
    except Exception:
        pass


# ============================================================
# FLET ALARM
# ============================================================

class FletAlarm:

    def __init__(self):
        """
        Obtain Android's application context without probing
        unknown Activity class names.

        This avoids the native process abort caused by trying
        to autoclass() unsupported Activity classes.
        """

        try:
            self.Build = autoclass("android.os.Build")
            self.Context = autoclass("android.content.Context")
            self.Intent = autoclass("android.content.Intent")
            self.PendingIntent = autoclass(
                "android.app.PendingIntent"
            )
            self.AlarmManager = autoclass(
                "android.app.AlarmManager"
            )
            self.Settings = autoclass(
                "android.provider.Settings"
            )
            self.Uri = autoclass("android.net.Uri")
            self.ActivityThread = autoclass(
                "android.app.ActivityThread"
            )
            self.NotificationManager = autoclass(
                "android.app.NotificationManager"
            )

            application = self.ActivityThread.currentApplication()

            if application is None:
                raise RuntimeError(
                    "Android application context is unavailable."
                )

            self.context = cast(
                "android.content.Context",
                application.getApplicationContext(),
            )

            if self.context is None:
                raise RuntimeError(
                    "Could not obtain Android application context."
                )

            self.package_name = str(self.context.getPackageName())
            self.sdk = int(self.Build.VERSION.SDK_INT)

            self.alarm_manager = cast(
                "android.app.AlarmManager",
                self.context.getSystemService(
                    self.Context.ALARM_SERVICE
                ),
            )

            if self.alarm_manager is None:
                raise RuntimeError(
                    "Android AlarmManager is unavailable."
                )

            _log(
                f"Initialized successfully: "
                f"package={self.package_name}, sdk={self.sdk}"
            )

        except Exception as exc:
            _log(f"INITIALIZATION FAILED: {exc}")
            raise RuntimeError(
                "Could not initialize Android alarm services. "
                "Check Pyjnius and Android application-context access."
            ) from exc

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
            _log(f"Exact alarm permission check failed: {exc}")
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

            # Application context requires this flag when starting
            # an Activity outside an existing Activity context.
            intent.addFlags(
                int(self.Intent.FLAG_ACTIVITY_NEW_TASK)
            )

            self.context.startActivity(intent)
            return True

        except Exception as exc:
            _log(f"Could not open exact alarm settings: {exc}")
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
    # BUILD EXPLICIT RECEIVER INTENT
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
            intent.putExtra(
                EXTRA_SCHEDULED_AT_MS,
                int(scheduled_at_ms),
            )

        intent.putExtra(
            EXTRA_REPEAT_WEEKLY,
            bool(repeat_weekly),
        )

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
        alarm_label = alarm_id

        try:
            if not isinstance(scheduled_time, datetime.datetime):
                raise TypeError(
                    "scheduled_time must be datetime.datetime"
                )

            alarm_id = int(alarm_id)
            alarm_label = alarm_id

            if alarm_id <= 0:
                raise ValueError(
                    "alarm_id must be greater than zero"
                )

            # Convert the supplied datetime to Unix milliseconds.
            trigger_ms = int(scheduled_time.timestamp() * 1000)

            if trigger_ms <= int(time.time() * 1000):
                raise ValueError(
                    f"Alarm time is in the past: {scheduled_time}"
                )

            if not self.can_schedule_exact_alarms():
                raise PermissionError(
                    "Exact alarms are disabled. Enable the app's "
                    "exact-alarm permission in Android settings."
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
                    "Android could not create the PendingIntent."
                )

            # Replace an existing alarm with the same ID.
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

            _log(
                f"Scheduled successfully: id={alarm_id}, "
                f"time={scheduled_time}, "
                f"repeat_weekly={repeat_weekly}"
            )

            return True

        except Exception as exc:
            _log(f"SET ALARM FAILED: id={alarm_label}: {exc}")
            raise

    # ========================================================
    # CANCEL ONE ALARM
    # ========================================================

    def cancel_alarm(self, alarm_id):
        try:
            alarm_id = int(alarm_id)

            if alarm_id <= 0:
                raise ValueError(
                    "alarm_id must be greater than zero"
                )

            pending_intent = self._get_pending_intent(
                alarm_id=alarm_id,
                no_create=True,
            )

            if pending_intent is not None:
                self.alarm_manager.cancel(pending_intent)
                pending_intent.cancel()

            manager = cast(
                "android.app.NotificationManager",
                self.context.getSystemService(
                    self.Context.NOTIFICATION_SERVICE
                ),
            )

            if manager is not None:
                manager.cancel(alarm_id)

            _log(f"Cancelled alarm id={alarm_id}")
            return True

        except Exception as exc:
            _log(f"CANCEL ALARM FAILED: id={alarm_id}: {exc}")
            return False

    # ========================================================
    # CANCEL ALL ALARMS
    # ========================================================

    def cancel_all(self, alarm_ids):
        success = True

        for alarm_id in alarm_ids:
            if not self.cancel_alarm(alarm_id):
                success = False

        return success
