import datetime
import os

from android_notify import Notification
from jnius import autoclass, cast


# ============================================================
# ANDROID CLASSES
# ============================================================

try:
    Context = autoclass("android.content.Context")
    Intent = autoclass("android.content.Intent")
    PendingIntent = autoclass("android.app.PendingIntent")
    AlarmManager = autoclass("android.app.AlarmManager")
    PowerManager = autoclass("android.os.PowerManager")

    def get_python_activity():
        """
        Find the Android Activity used by the Flet application.
        """

        activity_names = [
            os.getenv(
                "MAIN_ACTIVITY_HOST_CLASS_NAME",
                "",
            ),

            "org.flet.app.FletActivity",

            "org.kivy.android.PythonActivity",

            "org.renpy.android.PythonActivity",
        ]

        seen = set()

        for name in activity_names:

            if not name:
                continue

            if name in seen:
                continue

            seen.add(name)

            try:
                activity_class = autoclass(name)

                print(
                    f"FletAlarm: Found Android activity: {name}"
                )

                return activity_class

            except Exception as e:
                print(
                    f"FletAlarm: Could not load {name}: {e}"
                )

        return None


    PythonActivity = get_python_activity()

    if PythonActivity is None:
        raise RuntimeError(
            "No compatible Android Activity class found."
        )

    IS_ANDROID = True

    print(
        "FletAlarm: Android alarm support enabled."
    )

except Exception as e:

    print(
        f"FletAlarm: Android initialization failed: {e}"
    )

    Context = None
    Intent = None
    PendingIntent = None
    AlarmManager = None
    PowerManager = None
    PythonActivity = None
    IS_ANDROID = False


# ============================================================
# FLET ALARM
# ============================================================

class FletAlarm:

    def __init__(self):

        self.activity = None
        self.context = None
        self.alarm_manager = None

        if not IS_ANDROID:
            return

        if PythonActivity is None:
            print(
                "FletAlarm: PythonActivity is unavailable."
            )
            return

        try:

            raw_activity = PythonActivity.mActivity

            if raw_activity is None:
                print(
                    "FletAlarm: Android Activity is not ready."
                )
                return

            self.activity = cast(
                "android.app.Activity",
                raw_activity,
            )

            self.context = self.activity

            self.alarm_manager = cast(
                "android.app.AlarmManager",
                self.activity.getSystemService(
                    Context.ALARM_SERVICE
                ),
            )

            if self.alarm_manager is None:
                print(
                    "FletAlarm: AlarmManager unavailable."
                )
                return

            print(
                "FletAlarm: AlarmManager initialized."
            )

        except Exception as e:

            print(
                f"FletAlarm: Initialization error: {e}"
            )

            self.activity = None
            self.context = None
            self.alarm_manager = None


    # ========================================================
    # PENDING INTENT FLAGS
    # ========================================================

    def _build_pending_intent_flags(
        self,
        include_no_create=False,
    ):

        # Android 12+ requires explicit mutability.
        #
        # The alarm Intent does not need to be modified by
        # Android after the PendingIntent is created, so
        # IMMUTABLE is appropriate.

        flags = PendingIntent.FLAG_IMMUTABLE

        if include_no_create:

            flags |= PendingIntent.FLAG_NO_CREATE

        else:

            flags |= PendingIntent.FLAG_UPDATE_CURRENT

        return flags


    # ========================================================
    # INTENT CONFIGURATION
    # ========================================================

    def _prepare_alarm_intent(
        self,
        intent,
    ):
        """
        Prepare the Flet Activity Intent used by AlarmManager.

        The alarm should bring the existing Flet Activity to
        the foreground instead of creating unnecessary Activity
        instances.
        """

        intent.addFlags(
            Intent.FLAG_ACTIVITY_NEW_TASK
            | Intent.FLAG_ACTIVITY_CLEAR_TOP
            | Intent.FLAG_ACTIVITY_SINGLE_TOP
            | Intent.FLAG_ACTIVITY_REORDER_TO_FRONT
        )

        if self.context is not None:

            try:

                intent.setPackage(
                    self.context.getPackageName()
                )

            except Exception as e:

                print(
                    f"FletAlarm: Could not set package: {e}"
                )

        return intent


    # ========================================================
    # SET ALARM
    # ========================================================

    def set_alarm(
        self,
        schld_time: datetime.datetime,
        alarm_id: int,
        title: str = "Alarm",
        message: str = "Alarm triggered!",
        speech_text: str = "",
        repeat_weekly: bool = False,
    ):

        if not IS_ANDROID:

            print(
                f"DEBUG: Android unavailable. "
                f"Alarm {alarm_id} would be set for "
                f"{schld_time}"
            )

            return False


        if self.alarm_manager is None:

            print(
                f"CRITICAL: AlarmManager unavailable "
                f"for alarm {alarm_id}."
            )

            return False


        if self.activity is None:

            print(
                f"CRITICAL: Activity unavailable "
                f"for alarm {alarm_id}."
            )

            return False


        if self.context is None:

            print(
                f"CRITICAL: Context unavailable "
                f"for alarm {alarm_id}."
            )

            return False


        try:

            # ------------------------------------------------
            # CREATE INTENT
            # ------------------------------------------------

            intent = Intent(
                self.context,
                self.activity.getClass(),
            )

            self._prepare_alarm_intent(
                intent
            )


            # ------------------------------------------------
            # UNIQUE ACTION
            # ------------------------------------------------

            action = (
                f"com.zaimtech.CLASS_ALERT_ALARM_{alarm_id}"
            )

            intent.setAction(
                action
            )


            # ------------------------------------------------
            # ALARM TIME
            # ------------------------------------------------

            trigger_at_ms = int(
                schld_time.timestamp() * 1000
            )


            # ------------------------------------------------
            # ALARM DATA
            # ------------------------------------------------

            extras = autoclass(
                "android.os.Bundle"
            )()


            extras.putInt(
                "alarm_id",
                alarm_id,
            )

            extras.putInt(
                "notification_id",
                alarm_id,
            )

            extras.putString(
                "notification_title",
                title,
            )

            extras.putString(
                "notification_body",
                message,
            )

            extras.putString(
                "speech_text",
                speech_text or message,
            )

            extras.putBoolean(
                "is_alarm_trigger",
                True,
            )

            extras.putLong(
                "scheduled_at_ms",
                trigger_at_ms,
            )

            extras.putBoolean(
                "wake_for_alarm",
                True,
            )

            intent.putExtras(
                extras
            )


            # ------------------------------------------------
            # PENDING INTENT
            # ------------------------------------------------

            pending_intent = (
                PendingIntent.getActivity(
                    self.context,
                    alarm_id,
                    intent,
                    self._build_pending_intent_flags(),
                )
            )


            if pending_intent is None:

                print(
                    f"CRITICAL: Could not create "
                    f"PendingIntent for alarm {alarm_id}."
                )

                return False


            # ------------------------------------------------
            # DEBUG
            # ------------------------------------------------

            print(
                "================================================"
            )

            print(
                f"FletAlarm: Scheduling alarm {alarm_id}"
            )

            print(
                f"FletAlarm: Time = {schld_time}"
            )

            print(
                f"FletAlarm: Timestamp = {trigger_at_ms}"
            )

            print(
                f"FletAlarm: Action = {action}"
            )

            print(
                f"FletAlarm: Speech = "
                f"{speech_text or message}"
            )

            print(
                "FletAlarm: Android will wake the "
                "application Activity at the alarm time."
            )

            print(
                "FletAlarm: Activity flags = "
                "NEW_TASK | CLEAR_TOP | SINGLE_TOP | "
                "REORDER_TO_FRONT"
            )

            print(
                "================================================"
            )


            # ------------------------------------------------
            # SCHEDULE
            # ------------------------------------------------

            if repeat_weekly:

                one_week_ms = (
                    7 * 24 * 60 * 60 * 1000
                )

                self.alarm_manager.setRepeating(
                    AlarmManager.RTC_WAKEUP,
                    trigger_at_ms,
                    one_week_ms,
                    pending_intent,
                )

                print(
                    f"FletAlarm: Weekly alarm "
                    f"{alarm_id} scheduled."
                )

            else:

                # Exact + wake from Doze.

                self.alarm_manager.setExactAndAllowWhileIdle(
                    AlarmManager.RTC_WAKEUP,
                    trigger_at_ms,
                    pending_intent,
                )

                print(
                    f"FletAlarm: Exact wake-up alarm "
                    f"{alarm_id} scheduled."
                )


            return True


        except Exception as e:

            print(
                f"FletAlarm: Error scheduling "
                f"alarm {alarm_id}: {e}"
            )

            return False


    # ========================================================
    # CANCEL ALARM
    # ========================================================

    def cancel_alarm(
        self,
        alarm_id: int,
    ):

        if not IS_ANDROID:

            try:

                Notification(
                    id=alarm_id
                ).cancel(
                    alarm_id
                )

            except Exception:
                pass

            return False


        if self.alarm_manager is None:

            return False


        try:

            # Create the exact same Intent identity used
            # when the alarm was scheduled.

            intent = Intent(
                self.context,
                self.activity.getClass(),
            )

            self._prepare_alarm_intent(
                intent
            )


            action = (
                f"com.zaimtech.CLASS_ALERT_ALARM_{alarm_id}"
            )

            intent.setAction(
                action
            )


            pending_intent = (
                PendingIntent.getActivity(
                    self.context,
                    alarm_id,
                    intent,
                    self._build_pending_intent_flags(
                        include_no_create=True
                    ),
                )
            )


            if pending_intent is not None:

                self.alarm_manager.cancel(
                    pending_intent
                )

                pending_intent.cancel()

                print(
                    f"FletAlarm: Alarm "
                    f"{alarm_id} cancelled."
                )


            # Cancel associated notification as well.

            try:

                Notification(
                    id=alarm_id
                ).cancel(
                    alarm_id
                )

            except Exception:
                pass


            return True


        except Exception as e:

            print(
                f"FletAlarm: Error cancelling "
                f"alarm {alarm_id}: {e}"
            )

            return False
