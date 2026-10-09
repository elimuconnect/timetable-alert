import datetime
import time

from jnius import autoclass, cast


# ============================================================
# ANDROID CLASSES
# ============================================================

ANDROID_AVAILABLE = False
_ANDROID_ERROR = None

try:
    Build = autoclass("android.os.Build")
    Context = autoclass("android.content.Context")
    Intent = autoclass("android.content.Intent")
    PendingIntent = autoclass("android.app.PendingIntent")
    AlarmManager = autoclass("android.app.AlarmManager")
    Settings = autoclass("android.provider.Settings")
    Uri = autoclass("android.net.Uri")

    AlarmReceiver = autoclass(
        "org.digielimu.classalert.AlarmReceiver"
    )

    ANDROID_AVAILABLE = True

except Exception as exc:
    _ANDROID_ERROR = exc


# ============================================================
# CONSTANTS
# ============================================================

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
        print(f"[FletAlarm] {message}")
    except Exception:
        pass


# ============================================================
# ACTIVITY DISCOVERY
# ============================================================

def _get_activity():
    """
    Locate the current Android Activity.

    The Activity is required for scheduling alarms and opening
    Android settings. AlarmReceiver handles alarms natively
    without needing the Python Activity to remain open.
    """

    candidates = (
        "org.flet.android.PythonActivity",
        "org.flet.app.FletActivity",
        "org.kivy.android.PythonActivity",
        "org.renpy.android.PythonActivity",
    )

    for class_name in candidates:
        try:
            ActivityClass = autoclass(class_name)
            activity = getattr(ActivityClass, "mActivity", None)

            if activity is not None:
                return activity

        except Exception:
            continue

    return None


# ============================================================
# FLET ALARM
# ============================================================

class FletAlarm:

    def __init__(self):
        if not ANDROID_AVAILABLE:
            raise RuntimeError(
                f"Android classes unavailable: {_ANDROID_ERROR}"
            )

        self.activity = _get_activity()

        if self.activity is None:
            raise RuntimeError(
                "Could not find the Android Activity. "
                "Make sure this code runs inside the Flet Android app."
            )

        self.context = self.activity.getApplicationContext()

        self.alarm_manager = cast(
            "android.app.AlarmManager",
            self.context.getSystemService(
                Context.ALARM_SERVICE
            ),
        )

        if self.alarm_manager is None:
            raise RuntimeError(
                "Android AlarmManager is unavailable."
            )

        self.package_name = str(
            self.context.getPackageName()
        )

        self.sdk = int(Build.VERSION.SDK_INT)

        _log(
            f"Initialized: package={self.package_name}, "
            f"sdk={self.sdk}"
        )

    # ========================================================
    # EXACT ALARM PERMISSION
    # ========================================================

    def can_schedule_exact_alarms(self):
        """
        Check whether Android permits exact alarms.

        Android versions below 12 do not require the
        SCHEDULE_EXACT_ALARM special access check.
        """

        if self.sdk < 31:
            return True

        try:
            result = self.alarm_manager.canScheduleExactAlarms()
            allowed = bool(result)

            _log(f"canScheduleExactAlarms={allowed}")
            return allowed

        except Exception as exc:
            _log(f"Exact alarm permission check failed: {exc}")
            return False

    def open_exact_alarm_settings(self):
        """
        Open the Android exact-alarm settings page.

        Returns True if the settings activity was launched.
        """

        if self.sdk < 31:
            return True

        try:
            intent = Intent(
                Settings.ACTION_REQUEST_SCHEDULE_EXACT_ALARM
            )

            intent.setData(
                Uri.parse(f"package:{self.package_name}")
            )

            self.activity.startActivity(intent)

            _log("Opened exact alarm settings")
            return True

        except Exception as exc:
            _log(f"Could not open exact alarm settings: {exc}")
            return False

    # ========================================================
    # PENDING INTENT FLAGS
    # ========================================================

    @staticmethod
    def _pending_intent_flags(
        update=False,
        no_create=False,
    ):
        flags = 0

        try:
            flags |= int(PendingIntent.FLAG_IMMUTABLE)
        except Exception:
            pass

        if update:
            try:
                flags |= int(PendingIntent.FLAG_UPDATE_CURRENT)
            except Exception:
                pass

        if no_create:
            try:
                flags |= int(PendingIntent.FLAG_NO_CREATE)
            except Exception:
                pass

        return flags

    # ========================================================
    # BUILD BROADCAST INTENT
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

        intent = Intent(
            self.context,
            AlarmReceiver,
        )

        intent.setAction(
            f"{ACTION_PREFIX}{alarm_id}"
        )

        intent.setPackage(self.package_name)

        intent.putExtra(
            EXTRA_ALARM_ID,
            alarm_id,
        )

        intent.putExtra(
            EXTRA_NOTIFICATION_ID,
            alarm_id,
        )

        if title is not None:
            intent.putExtra(
                EXTRA_NOTIFICATION_TITLE,
                str(title),
            )

        if message is not None:
            intent.putExtra(
                EXTRA_NOTIFICATION_BODY,
                str(message),
            )

        if speech_text is not None:
            intent.putExtra(
                EXTRA_SPEECH_TEXT,
                str(speech_text),
            )

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

    # ========================================================
    # CREATE PENDING INTENT
    # ========================================================

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

        flags = self._pending_intent_flags(
            update=update,
            no_create=no_create,
        )

        return PendingIntent.getBroadcast(
            self.context,
            int(alarm_id),
            intent,
            flags,
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
        """
        Schedule an Android alarm.

        scheduled_time must be a datetime.datetime instance.

        The native Kotlin AlarmReceiver handles notification
        delivery, speech, and weekly rescheduling.

        Python does not need to run continuously in the
        background for an alarm to fire.
        """

        try:
            # Validate datetime.
            if not isinstance(
                scheduled_time,
                datetime.datetime,
            ):
                raise TypeError(
                    "scheduled_time must be a datetime.datetime instance."
                )

            # Validate alarm ID.
            alarm_id = int(alarm_id)

            if alarm_id <= 0:
                raise ValueError(
                    "alarm_id must be greater than zero."
                )

            # Convert the supplied datetime to epoch milliseconds.
            trigger_ms = int(
                scheduled_time.timestamp() * 1000
            )

            now_ms = int(time.time() * 1000)

            if trigger_ms <= now_ms:
                raise ValueError(
                    f"Alarm time is in the past: {scheduled_time}"
                )

            # Check exact alarm permission on Android 12+.
            if not self.can_schedule_exact_alarms():
                raise PermissionError(
                    "Exact alarms are not enabled for this app. "
                    "Open exact alarm settings and enable permission."
                )

            # Create the PendingIntent.
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
                    "PendingIntent.getBroadcast() returned None."
                )

            # Replace any previously scheduled alarm with this ID.
            self.alarm_manager.cancel(pending_intent)

            # Schedule the exact alarm.
            if self.sdk >= 23:
                self.alarm_manager.setExactAndAllowWhileIdle(
                    AlarmManager.RTC_WAKEUP,
                    trigger_ms,
                    pending_intent,
                )
            else:
                self.alarm_manager.setExact(
                    AlarmManager.RTC_WAKEUP,
                    trigger_ms,
                    pending_intent,
                )

            _log(
                f"Alarm scheduled: id={alarm_id}, "
                f"time={scheduled_time}, "
                f"epoch={trigger_ms}, "
                f"repeat_weekly={repeat_weekly}, "
                f"title={title}"
            )

            return True

        except Exception as exc:
            _log(
                f"SET ALARM FAILED: id={alarm_id}, error={exc}"
            )
            raise

    # ========================================================
    # CANCEL ONE ALARM
    # ========================================================

    def cancel_alarm(self, alarm_id):
        """
        Cancel a scheduled alarm and its notification.

        Returns True if cancellation completes without an
        exception. A missing PendingIntent is not an error.
        """

        try:
            alarm_id = int(alarm_id)

            if alarm_id <= 0:
                raise ValueError(
                    "alarm_id must be greater than zero."
                )

            pending_intent = self._get_pending_intent(
                alarm_id=alarm_id,
                no_create=True,
            )

            if pending_intent is not None:
                self.alarm_manager.cancel(pending_intent)

                try:
                    pending_intent.cancel()
                except Exception as exc:
                    _log(
                        f"PendingIntent cleanup warning: {exc}"
                    )

                _log(f"Cancelled alarm id={alarm_id}")

            else:
                _log(
                    f"No existing PendingIntent for alarm id={alarm_id}"
                )

            # Cancel the corresponding notification.
            try:
                manager = cast(
                    "android.app.NotificationManager",
                    self.context.getSystemService(
                        Context.NOTIFICATION_SERVICE
                    ),
                )

                if manager is not None:
                    manager.cancel(alarm_id)

            except Exception as exc:
                _log(
                    f"Notification cancellation warning: {exc}"
                )

            return True

        except Exception as exc:
            _log(
                f"CANCEL ALARM FAILED: id={alarm_id}, error={exc}"
            )
            return False

    # ========================================================
    # CANCEL ALL ALARMS
    # ========================================================

    def cancel_all(self, alarm_ids):
        """
        Cancel all alarm IDs supplied by the calling app.
        """

        success = True

        for alarm_id in alarm_ids:
            if not self.cancel_alarm(alarm_id):
                success = False

        return success
