import datetime
import os

from android_notify import Notification
from jnius import autoclass, cast

# Native Android imports are wrapped so this module stays safe on desktop.
try:
    Context = autoclass("android.content.Context")
    Intent = autoclass("android.content.Intent")
    PendingIntent = autoclass("android.app.PendingIntent")
    AlarmManager = autoclass("android.app.AlarmManager")

    def get_python_activity():
        activity_names = [
            os.getenv("MAIN_ACTIVITY_HOST_CLASS_NAME", "org.kivy.android.PythonActivity"),
            "org.kivy.android.PythonActivity",
            "org.renpy.android.PythonActivity",
            "org.flet.app.FletActivity",
        ]
        seen = set()
        for name in activity_names:
            if not name or name in seen:
                continue
            seen.add(name)
            try:
                return autoclass(name)
            except Exception:
                continue
        return None

    PythonActivity = get_python_activity()
    if PythonActivity is None:
        raise RuntimeError("No compatible Python activity class found.")
    IS_ANDROID = True
except (ImportError, Exception):
    Context = None
    Intent = None
    PendingIntent = None
    AlarmManager = None
    PythonActivity = None
    IS_ANDROID = False


class FletAlarm:
    def __init__(self):
        self.activity = None
        self.context = None
        self.alarm_manager = None

        if IS_ANDROID and PythonActivity is not None:
            try:
                raw_activity = PythonActivity.mActivity
                if raw_activity is None:
                    print("Android activity is not ready yet.")
                    return

                self.activity = cast("android.app.Activity", raw_activity)
                if self.activity is None:
                    print("Android activity is not ready yet.")
                    return

                self.context = self.activity
                if self.context is None:
                    print("Android application context is not available.")
                    return

                self.alarm_manager = cast(
                    "android.app.AlarmManager",
                    self.activity.getSystemService(Context.ALARM_SERVICE),
                )
            except Exception as e:
                print(f"Android Initialization Error: {e}")

    def _build_pending_intent_flags(self, include_no_create: bool = False):
        flags = PendingIntent.FLAG_MUTABLE

        if include_no_create:
            flags |= PendingIntent.FLAG_NO_CREATE
        else:
            flags |= PendingIntent.FLAG_UPDATE_CURRENT

        return flags

    def _apply_alarm_launch_flags(self, intent):
        """
        Configure the launch intent so Android can bring the app back and
        deliver fresh extras even when the activity already exists or the app
        process was previously stopped.
        """
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        intent.addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP)
        intent.addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP)
        if self.context is not None:
            intent.setPackage(self.context.getPackageName())
        return intent

    def set_alarm(
        self,
        schld_time: datetime.datetime,
        alarm_id: int,
        title: str = "Alarm",
        message: str = "Alarm triggered!",
        repeat_weekly: bool = True,
    ):
        """
        Schedule a system-level alarm.
        If repeat_weekly is True, it repeats every 7 days from the start time.
        """
        if not IS_ANDROID or not self.alarm_manager:
            print(f"DEBUG: Alarm {alarm_id} would be set for {schld_time}")
            return False

        if self.activity is None:
            print("CRITICAL: Android Activity not initialized yet!")
            return False

        if self.context is None:
            print("CRITICAL: Android Context not initialized yet!")
            return False

        intent = Intent(self.context, self.activity.getClass())
        self._apply_alarm_launch_flags(intent)
        intent.setAction(f"com.zaimtech.ALARM_{alarm_id}")
        trigger_at_ms = int(schld_time.timestamp() * 1000)
        extras = autoclass("android.os.Bundle")()
        extras.putInt("alarm_id", alarm_id)
        extras.putInt("notification_id", alarm_id)
        extras.putString("notification_title", title)
        extras.putString("notification_body", message)
        extras.putBoolean("is_alarm_trigger", True)
        extras.putLong("scheduled_at_ms", trigger_at_ms)
        intent.putExtras(extras)

        pending_intent = PendingIntent.getActivity(
            self.context,
            alarm_id,
            intent,
            self._build_pending_intent_flags(),
        )

        try:
            if repeat_weekly:
                one_week_ms = 604800000
                self.alarm_manager.setRepeating(
                    AlarmManager.RTC_WAKEUP,
                    trigger_at_ms,
                    one_week_ms,
                    pending_intent,
                )
                print(f"Weekly repeating alarm {alarm_id} set.")
            else:
                self.alarm_manager.setExactAndAllowWhileIdle(
                    AlarmManager.RTC_WAKEUP,
                    trigger_at_ms,
                    pending_intent,
                )
                print(f"One-time alarm {alarm_id} set.")
            return True
        except Exception as e:
            print(f"Error scheduling alarm: {e}")
            return False

    def cancel_alarm(self, alarm_id: int):
        """
        Stop a scheduled alarm and prevent future repeats.
        """
        if not IS_ANDROID or not self.alarm_manager:
            Notification(id=alarm_id).cancel(alarm_id)
            return False

        try:
            intent = Intent(self.context, self.activity.getClass())
            self._apply_alarm_launch_flags(intent)
            intent.setAction(f"com.zaimtech.ALARM_{alarm_id}")

            pending_intent = PendingIntent.getActivity(
                self.context,
                alarm_id,
                intent,
                self._build_pending_intent_flags(include_no_create=True),
            )

            if pending_intent:
                self.alarm_manager.cancel(pending_intent)
                pending_intent.cancel()
                print(f"Alarm {alarm_id} cancelled.")

            Notification(id=alarm_id).cancel(alarm_id)
            return True
        except Exception as e:
            print(f"Error cancelling alarm: {e}")
            return False
