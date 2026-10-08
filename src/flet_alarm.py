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

    # Native receiver created in:
    # android/app/src/main/kotlin/org/digielimu/classalert/AlarmReceiver.kt
    AlarmReceiver = autoclass(
        "org.digielimu.classalert.AlarmReceiver"
    )

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

    print(
        "FletAlarm: Native AlarmReceiver loaded."
    )

except Exception as e:

    print(
        f"FletAlarm: Android initialization failed: {e}"
    )

    Context = None
    Intent = None
    PendingIntent = None
    AlarmManager = None
    AlarmReceiver = None
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

        if AlarmReceiver is None:

            print(
                "FletAlarm: AlarmReceiver is unavailable."
            )

            return

        try:

            # ------------------------------------------------
            # GET CURRENT FLET ACTIVITY
            # ------------------------------------------------

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

            # Activity is used only to obtain the Android
            # application context. The alarm itself does NOT
            # target the Activity anymore.
            self.context = (
                self.activity.getApplicationContext()
            )

            # ------------------------------------------------
            # GET ALARM MANAGER
            # ------------------------------------------------

            self.alarm_manager = cast(
                "android.app.AlarmManager",
                self.context.getSystemService(
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

            print(
                "FletAlarm: Using native AlarmReceiver."
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
        # Android after the PendingIntent is created.

        flags = PendingIntent.FLAG_IMMUTABLE

        if include_no_create:

            flags |= PendingIntent.FLAG_NO_CREATE

        else:

            flags |= PendingIntent.FLAG_UPDATE_CURRENT

        return flags


    # ========================================================
    # PREPARE BROADCAST INTENT
    # ========================================================

    def _prepare_alarm_intent(
        self,
        intent,
    ):
        """
        Prepare the explicit BroadcastReceiver Intent.

        IMPORTANT:
        This is no longer an Activity Intent.

        Android AlarmManager will deliver this Intent
        directly to AlarmReceiver.kt.
        """

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


        if self.context is None:

            print(
                f"CRITICAL: Android context unavailable "
                f"for alarm {alarm_id}."
            )

            return False


        if AlarmReceiver is None:

            print(
                f"CRITICAL: AlarmReceiver unavailable "
                f"for alarm {alarm_id}."
            )

            return False


        try:

            # ------------------------------------------------
            # CREATE NATIVE BROADCAST INTENT
            # ------------------------------------------------

            intent = Intent(
                self.context,
                AlarmReceiver,
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
                int(alarm_id),
            )

            extras.putInt(
                "notification_id",
                int(alarm_id),
            )

            extras.putString(
                "notification_title",
                str(title),
            )

            extras.putString(
                "notification_body",
                str(message),
            )

            extras.putString(
                "speech_text",
                str(
                    speech_text
                    or message
                ),
            )

            extras.putLong(
                "scheduled_at_ms",
                int(trigger_at_ms),
            )

            # Native AlarmReceiver.kt uses this to determine
            # whether it should schedule the same alarm for
            # the following week.
            extras.putBoolean(
                "repeat_weekly",
                bool(repeat_weekly),
            )

            intent.putExtras(
                extras
            )


            # ------------------------------------------------
            # PENDING INTENT
            # ------------------------------------------------

            pending_intent = (
                PendingIntent.getBroadcast(
                    self.context,
                    int(alarm_id),
                    intent,
                    self._build_pending_intent_flags(),
                )
            )


            if pending_intent is None:

                print(
                    f"CRITICAL: Could not create "
                    f"Broadcast PendingIntent for "
                    f"alarm {alarm_id}."
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
                f"FletAlarm: Repeat weekly = "
                f"{repeat_weekly}"
            )

            print(
                "FletAlarm: Target = "
                "org.digielimu.classalert.AlarmReceiver"
            )

            print(
                "FletAlarm: PendingIntent = "
                "getBroadcast()"
            )

            print(
                "FletAlarm: Alarm type = "
                "RTC_WAKEUP"
            )

            print(
                "FletAlarm: Exact + AllowWhileIdle = TRUE"
            )

            print(
                "================================================"
            )


            # ------------------------------------------------
            # SCHEDULE EXACT ALARM
            # ------------------------------------------------
            #
            # We intentionally DO NOT use setRepeating().
            #
            # AlarmReceiver.kt handles the next-week
            # scheduling when repeat_weekly=True.
            #

            self.alarm_manager.setExactAndAllowWhileIdle(
                AlarmManager.RTC_WAKEUP,
                trigger_at_ms,
                pending_intent,
            )

            print(
                f"FletAlarm: Exact native alarm "
                f"{alarm_id} scheduled successfully."
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


        if self.context is None:

            return False


        if AlarmReceiver is None:

            return False


        try:

            # ------------------------------------------------
            # CREATE THE EXACT SAME BROADCAST INTENT
            # ------------------------------------------------
            #
            # This is important because Android identifies
            # PendingIntents by their Intent identity.
            #

            intent = Intent(
                self.context,
                AlarmReceiver,
            )

            self._prepare_alarm_intent(
                intent
            )


            # ------------------------------------------------
            # SAME ACTION
            # ------------------------------------------------

            action = (
                f"com.zaimtech.CLASS_ALERT_ALARM_{alarm_id}"
            )

            intent.setAction(
                action
            )


            # ------------------------------------------------
            # FIND EXISTING BROADCAST PENDING INTENT
            # ------------------------------------------------

            pending_intent = (
                PendingIntent.getBroadcast(
                    self.context,
                    int(alarm_id),
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

            else:

                print(
                    f"FletAlarm: No existing "
                    f"PendingIntent found for "
                    f"alarm {alarm_id}."
                )


            # ------------------------------------------------
            # CANCEL ASSOCIATED NOTIFICATION
            # ------------------------------------------------

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
