import datetime
import os
import time

from jnius import autoclass, cast


# ============================================================
# ANDROID CLASSES
# ============================================================

try:
    Build = autoclass("android.os.Build")
    Context = autoclass("android.content.Context")
    Intent = autoclass("android.content.Intent")
    PendingIntent = autoclass("android.app.PendingIntent")
    AlarmManager = autoclass("android.app.AlarmManager")
    Settings = autoclass("android.provider.Settings")
    Uri = autoclass("android.net.Uri")
    NotificationManager = autoclass(
        "android.app.NotificationManager"
    )

    AlarmReceiver = autoclass(
        "org.digielimu.classalert.AlarmReceiver"
    )

    ANDROID_AVAILABLE = True

except Exception as exc:
    ANDROID_AVAILABLE = False
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
    Find the currently running Android Activity.

    The Activity is only required while scheduling/cancelling
    alarms from the Flet application.

    The native AlarmReceiver does NOT depend on the Activity
    when an alarm fires.
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

            activity = getattr(
                ActivityClass,
                "mActivity",
                None,
            )

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
                f"Android classes unavailable: "
                f"{_ANDROID_ERROR}"
            )

        self.activity = _get_activity()

        if self.activity is None:

            raise RuntimeError(
                "Could not find the Android Activity."
            )

        self.context = (
            self.activity.getApplicationContext()
        )

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

        self.sdk = int(
            Build.VERSION.SDK_INT
        )

        _log(
            f"initialized: "
            f"package={self.package_name}, "
            f"sdk={self.sdk}"
        )

    # ========================================================
    # EXACT ALARM PERMISSION
    # ========================================================

    def can_schedule_exact_alarms(self):
        """
        Android 12+ may require the
        SCHEDULE_EXACT_ALARM permission.
        """

        try:

            if self.sdk < 31:
                return True

            result = (
                self.alarm_manager.canScheduleExactAlarms()
            )

            _log(
                f"canScheduleExactAlarms={result}"
            )

            return bool(result)

        except Exception as exc:

            _log(
                f"exact alarm permission check failed: {exc}"
            )

            return False

    def open_exact_alarm_settings(self):

        try:

            intent = Intent(
                Settings.ACTION_REQUEST_SCHEDULE_EXACT_ALARM
            )

            intent.setData(
                Uri.parse(
                    f"package:{self.package_name}"
                )
            )

            self.activity.startActivity(intent)

            _log(
                "opened exact alarm settings"
            )

            return True

        except Exception as exc:

            _log(
                f"could not open exact alarm settings: {exc}"
            )

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

        # Android 12+
        try:

            flags |= PendingIntent.FLAG_IMMUTABLE

        except Exception:

            pass

        if update:

            flags |= PendingIntent.FLAG_UPDATE_CURRENT

        if no_create:

            flags |= PendingIntent.FLAG_NO_CREATE

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

        action = (
            f"{ACTION_PREFIX}{int(alarm_id)}"
        )

        intent = Intent(
            self.context,
            AlarmReceiver,
        )

        intent.setAction(action)

        # Explicit package restriction.
        intent.setPackage(
            self.package_name
        )

        # ----------------------------------------------------
        # Alarm identity
        # ----------------------------------------------------

        intent.putExtra(
            EXTRA_ALARM_ID,
            int(alarm_id),
        )

        # ----------------------------------------------------
        # Notification ID
        # ----------------------------------------------------

        intent.putExtra(
            EXTRA_NOTIFICATION_ID,
            int(alarm_id),
        )

        # ----------------------------------------------------
        # Notification title
        # ----------------------------------------------------

        if title is not None:

            intent.putExtra(
                EXTRA_NOTIFICATION_TITLE,
                str(title),
            )

        # ----------------------------------------------------
        # Notification body
        # ----------------------------------------------------

        if message is not None:

            intent.putExtra(
                EXTRA_NOTIFICATION_BODY,
                str(message),
            )

        # ----------------------------------------------------
        # Native TTS text
        # ----------------------------------------------------

        if speech_text is not None:

            intent.putExtra(
                EXTRA_SPEECH_TEXT,
                str(speech_text),
            )

        # ----------------------------------------------------
        # Original scheduled time
        # ----------------------------------------------------

        if scheduled_at_ms is not None:

            intent.putExtra(
                EXTRA_SCHEDULED_AT_MS,
                int(scheduled_at_ms),
            )

        # ----------------------------------------------------
        # Native weekly repeat
        #
        # IMPORTANT:
        # Kotlin AlarmReceiver owns weekly rescheduling.
        # ----------------------------------------------------

        intent.putExtra(
            EXTRA_REPEAT_WEEKLY,
            bool(repeat_weekly),
        )

        return intent

    # ========================================================
    # SCHEDULE
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
        Schedule one exact Android alarm.

        Weekly repetition is enabled by default.

        After the alarm fires, Kotlin AlarmReceiver is
        responsible for scheduling the same alarm again
        seven days later.

        Python does NOT need to receive the alarm.
        """

        try:

            # ------------------------------------------------
            # Validate datetime
            # ------------------------------------------------

            if not isinstance(
                scheduled_time,
                datetime.datetime,
            ):

                raise TypeError(
                    "scheduled_time must be "
                    "a datetime.datetime"
                )

            # ------------------------------------------------
            # Validate alarm ID
            # ------------------------------------------------

            alarm_id = int(alarm_id)

            if alarm_id <= 0:

                raise ValueError(
                    "alarm_id must be greater than zero"
                )

            # ------------------------------------------------
            # Convert datetime -> epoch milliseconds
            # ------------------------------------------------

            trigger_ms = int(
                scheduled_time.timestamp() * 1000
            )

            now_ms = int(
                time.time() * 1000
            )

            if trigger_ms <= now_ms:

                raise ValueError(
                    f"Alarm time is in the past: "
                    f"{scheduled_time}"
                )

            # ------------------------------------------------
            # Android 12+ exact alarm permission
            # ------------------------------------------------

            if not self.can_schedule_exact_alarms():

                raise PermissionError(
                    "SCHEDULE_EXACT_ALARM permission "
                    "is not available. "
                    "Please enable exact alarms for "
                    "this application."
                )

            # ------------------------------------------------
            # Build explicit receiver intent
            # ------------------------------------------------

            intent = self._build_intent(
                alarm_id=alarm_id,
                title=title,
                message=message,
                speech_text=speech_text,
                scheduled_at_ms=trigger_ms,
                repeat_weekly=repeat_weekly,
            )

            # ------------------------------------------------
            # PendingIntent
            # ------------------------------------------------

            flags = self._pending_intent_flags(
                update=True
            )

            pending_intent = (
                PendingIntent.getBroadcast(
                    self.context,
                    alarm_id,
                    intent,
                    flags,
                )
            )

            if pending_intent is None:

                raise RuntimeError(
                    "PendingIntent.getBroadcast() "
                    "returned None."
                )

            # ------------------------------------------------
            # Cancel previous alarm with same ID
            # ------------------------------------------------

            try:

                self.alarm_manager.cancel(
                    pending_intent
                )

            except Exception as exc:

                _log(
                    f"previous alarm cancellation "
                    f"warning: {exc}"
                )

            # ------------------------------------------------
            # Schedule exact alarm
            # ------------------------------------------------

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

            # ------------------------------------------------
            # Logging
            # ------------------------------------------------

            _log(
                f"scheduled alarm: "
                f"id={alarm_id}, "
                f"time={scheduled_time}, "
                f"epoch={trigger_ms}, "
                f"repeat_weekly={repeat_weekly}, "
                f"title={title}"
            )

            return True

        except Exception as exc:

            _log(
                f"SET ALARM FAILED: "
                f"id={alarm_id}, "
                f"error={exc}"
            )

            raise

    # ========================================================
    # CANCEL
    # ========================================================

    def cancel_alarm(
        self,
        alarm_id,
    ):

        alarm_id = int(alarm_id)

        try:

            # ------------------------------------------------
            # Recreate the same PendingIntent identity
            # ------------------------------------------------

            intent = self._build_intent(
                alarm_id=alarm_id
            )

            flags = self._pending_intent_flags(
                no_create=True
            )

            pending_intent = (
                PendingIntent.getBroadcast(
                    self.context,
                    alarm_id,
                    intent,
                    flags,
                )
            )

            if pending_intent is not None:

                self.alarm_manager.cancel(
                    pending_intent
                )

                try:

                    pending_intent.cancel()

                except Exception:

                    pass

                _log(
                    f"cancelled alarm id={alarm_id}"
                )

            else:

                _log(
                    f"no existing PendingIntent "
                    f"for alarm id={alarm_id}"
                )

            # ------------------------------------------------
            # Cancel native notification
            # ------------------------------------------------

            try:

                manager = cast(
                    "android.app.NotificationManager",
                    self.context.getSystemService(
                        Context.NOTIFICATION_SERVICE
                    ),
                )

                if manager is not None:

                    manager.cancel(
                        alarm_id
                    )

            except Exception as exc:

                _log(
                    f"notification cancellation failed: "
                    f"{exc}"
                )

            return True

        except Exception as exc:

            _log(
                f"CANCEL ALARM FAILED: "
                f"id={alarm_id}, "
                f"error={exc}"
            )

            return False

    # ========================================================
    # CANCEL ALL
    # ========================================================

    def cancel_all(
        self,
        alarm_ids,
    ):

        success = True

        for alarm_id in alarm_ids:

            if not self.cancel_alarm(
                alarm_id
            ):

                success = False

        return success
