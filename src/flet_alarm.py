import datetime
import os

from android_notify import Notification
from jnius import autoclass, cast


# ============================================================
# ANDROID CLASSES
# ============================================================

try:

    Context = autoclass(
        "android.content.Context"
    )

    Intent = autoclass(
        "android.content.Intent"
    )

    PendingIntent = autoclass(
        "android.app.PendingIntent"
    )

    AlarmManager = autoclass(
        "android.app.AlarmManager"
    )

    Build = autoclass(
        "android.os.Build"
    )

    AlarmReceiver = autoclass(
        "org.digielimu.classalert.AlarmReceiver"
    )


    def get_python_activity():
        """
        Find the Android Activity hosting the Flet application.
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

                activity_class = autoclass(
                    name
                )

                print(
                    "FletAlarm: Android activity found: "
                    f"{name}"
                )

                return activity_class

            except Exception as e:

                print(
                    "FletAlarm: Activity not available: "
                    f"{name} -> {e}"
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
        "FletAlarm: Android initialization failed: "
        f"{e}"
    )

    Context = None
    Intent = None
    PendingIntent = None
    AlarmManager = None
    Build = None
    AlarmReceiver = None
    PythonActivity = None

    IS_ANDROID = False


# ============================================================
# FLET ALARM
# ============================================================

class FletAlarm:

    ACTION_PREFIX = (
        "com.zaimtech.CLASS_ALERT_ALARM_"
    )

    EXTRA_ALARM_ID = (
        "alarm_id"
    )

    EXTRA_NOTIFICATION_ID = (
        "notification_id"
    )

    EXTRA_NOTIFICATION_TITLE = (
        "notification_title"
    )

    EXTRA_NOTIFICATION_BODY = (
        "notification_body"
    )

    EXTRA_SPEECH_TEXT = (
        "speech_text"
    )

    EXTRA_SCHEDULED_AT_MS = (
        "scheduled_at_ms"
    )

    EXTRA_REPEAT_WEEKLY = (
        "repeat_weekly"
    )


    def __init__(self):

        self.activity = None
        self.context = None
        self.alarm_manager = None


        if not IS_ANDROID:
            return


        if PythonActivity is None:

            print(
                "FletAlarm: PythonActivity unavailable."
            )

            return


        if AlarmReceiver is None:

            print(
                "FletAlarm: AlarmReceiver unavailable."
            )

            return


        try:

            # ------------------------------------------------
            # CURRENT ACTIVITY
            # ------------------------------------------------

            raw_activity = (
                PythonActivity.mActivity
            )


            if raw_activity is None:

                print(
                    "FletAlarm: Android Activity "
                    "is not ready."
                )

                return


            self.activity = cast(
                "android.app.Activity",
                raw_activity,
            )


            # ------------------------------------------------
            # APPLICATION CONTEXT
            # ------------------------------------------------

            self.context = (
                self.activity
                .getApplicationContext()
            )


            # ------------------------------------------------
            # ALARM MANAGER
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
                "FletAlarm: Receiver = "
                "org.digielimu.classalert.AlarmReceiver"
            )


        except Exception as e:

            print(
                "FletAlarm: Initialization error: "
                f"{e}"
            )

            self.activity = None
            self.context = None
            self.alarm_manager = None


    # ========================================================
    # PENDING INTENT FLAGS
    # ========================================================

    def _pending_intent_flags(
        self,
        no_create=False,
    ):

        flags = (
            PendingIntent.FLAG_IMMUTABLE
        )

        if no_create:

            flags |= (
                PendingIntent.FLAG_NO_CREATE
            )

        else:

            flags |= (
                PendingIntent.FLAG_UPDATE_CURRENT
            )

        return flags


    # ========================================================
    # BUILD RECEIVER INTENT
    # ========================================================

    def _build_alarm_intent(
        self,
        alarm_id,
        title="Alarm",
        message="Alarm triggered!",
        speech_text="",
        trigger_at_ms=0,
        repeat_weekly=False,
    ):
        """
        Build the EXACT Intent consumed by AlarmReceiver.kt.
        """

        intent = Intent(
            self.context,
            AlarmReceiver,
        )


        # ----------------------------------------------------
        # UNIQUE ACTION
        # ----------------------------------------------------

        action = (
            self.ACTION_PREFIX
            + str(int(alarm_id))
        )

        intent.setAction(
            action
        )


        # ----------------------------------------------------
        # PACKAGE
        # ----------------------------------------------------

        try:

            intent.setPackage(
                self.context.getPackageName()
            )

        except Exception as e:

            print(
                "FletAlarm: setPackage failed: "
                f"{e}"
            )


        # ----------------------------------------------------
        # EXTRAS
        # ----------------------------------------------------

        intent.putExtra(
            self.EXTRA_ALARM_ID,
            int(alarm_id),
        )

        intent.putExtra(
            self.EXTRA_NOTIFICATION_ID,
            int(alarm_id),
        )

        intent.putExtra(
            self.EXTRA_NOTIFICATION_TITLE,
            str(title),
        )

        intent.putExtra(
            self.EXTRA_NOTIFICATION_BODY,
            str(message),
        )

        intent.putExtra(
            self.EXTRA_SPEECH_TEXT,
            str(
                speech_text
                or message
            ),
        )

        intent.putExtra(
            self.EXTRA_SCHEDULED_AT_MS,
            int(trigger_at_ms),
        )

        intent.putExtra(
            self.EXTRA_REPEAT_WEEKLY,
            bool(repeat_weekly),
        )


        return intent


    # ========================================================
    # CHECK EXACT ALARM SUPPORT
    # ========================================================

    def _can_schedule_exact_alarms(self):

        if self.alarm_manager is None:
            return False


        try:

            sdk = int(
                Build.VERSION.SDK_INT
            )


            if sdk >= 31:

                allowed = (
                    self.alarm_manager
                    .canScheduleExactAlarms()
                )

                print(
                    "FletAlarm: exact alarm permission = "
                    f"{allowed}"
                )

                return bool(allowed)


            return True


        except Exception as e:

            print(
                "FletAlarm: exact alarm check failed: "
                f"{e}"
            )

            # Don't block older Android versions.
            return True


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

        # ----------------------------------------------------
        # DESKTOP
        # ----------------------------------------------------

        if not IS_ANDROID:

            print(
                "DEBUG: Android unavailable. "
                f"Alarm {alarm_id} would be scheduled "
                f"for {schld_time}"
            )

            return False


        # ----------------------------------------------------
        # VALIDATION
        # ----------------------------------------------------

        if self.context is None:

            print(
                "CRITICAL: Android context unavailable."
            )

            return False


        if self.alarm_manager is None:

            print(
                "CRITICAL: AlarmManager unavailable."
            )

            return False


        if AlarmReceiver is None:

            print(
                "CRITICAL: AlarmReceiver unavailable."
            )

            return False


        # ----------------------------------------------------
        # TIMESTAMP
        # ----------------------------------------------------

        try:

            trigger_at_ms = int(
                schld_time.timestamp()
                * 1000
            )

        except Exception as e:

            print(
                "FletAlarm: Invalid alarm time: "
                f"{e}"
            )

            return False


        now_ms = int(
            datetime.datetime.now().timestamp()
            * 1000
        )


        # ----------------------------------------------------
        # DON'T SCHEDULE OLD ALARMS
        # ----------------------------------------------------

        if trigger_at_ms <= now_ms:

            print(
                "FletAlarm: Refusing to schedule "
                f"alarm {alarm_id} in the past."
            )

            print(
                f"FletAlarm: requested={schld_time}"
            )

            return False


        # ----------------------------------------------------
        # EXACT ALARM PERMISSION
        # ----------------------------------------------------

        if not self._can_schedule_exact_alarms():

            print(
                "CRITICAL: Exact alarm permission "
                "is not granted."
            )

            print(
                "Open Android settings and allow "
                "Alarms & reminders for this app."
            )

            return False


        try:

            # ------------------------------------------------
            # BUILD INTENT
            # ------------------------------------------------

            intent = self._build_alarm_intent(

                alarm_id=alarm_id,

                title=title,

                message=message,

                speech_text=(
                    speech_text
                    or message
                ),

                trigger_at_ms=trigger_at_ms,

                repeat_weekly=repeat_weekly,
            )


            # ------------------------------------------------
            # PENDING INTENT
            # ------------------------------------------------

            pending_intent = (
                PendingIntent.getBroadcast(

                    self.context,

                    int(alarm_id),

                    intent,

                    self._pending_intent_flags(),
                )
            )


            if pending_intent is None:

                print(
                    "CRITICAL: Could not create "
                    f"Broadcast PendingIntent "
                    f"for alarm {alarm_id}."
                )

                return False


            # ------------------------------------------------
            # LOG EVERYTHING
            # ------------------------------------------------

            print(
                "================================================"
            )

            print(
                "FletAlarm: NATIVE BROADCAST ALARM"
            )

            print(
                f"ID              = {alarm_id}"
            )

            print(
                f"Time            = {schld_time}"
            )

            print(
                f"Timestamp       = {trigger_at_ms}"
            )

            print(
                f"Action          = "
                f"{self.ACTION_PREFIX}{alarm_id}"
            )

            print(
                f"Title           = {title}"
            )

            print(
                f"Message         = {message}"
            )

            print(
                f"Speech          = "
                f"{speech_text or message}"
            )

            print(
                f"Repeat weekly   = {repeat_weekly}"
            )

            print(
                "Target          = AlarmReceiver"
            )

            print(
                "PendingIntent   = getBroadcast()"
            )

            print(
                "Alarm type      = RTC_WAKEUP"
            )

            print(
                "Exact           = TRUE"
            )

            print(
                "Allow idle      = TRUE"
            )

            print(
                "================================================"
            )


            # ------------------------------------------------
            # SCHEDULE
            # ------------------------------------------------
            #
            # NEVER use setRepeating().
            #
            # AlarmReceiver schedules the next occurrence
            # itself when repeat_weekly=True.
            # ------------------------------------------------

            if (
                hasattr(
                    self.alarm_manager,
                    "setExactAndAllowWhileIdle"
                )
            ):

                self.alarm_manager.setExactAndAllowWhileIdle(

                    AlarmManager.RTC_WAKEUP,

                    trigger_at_ms,

                    pending_intent,
                )

            else:

                self.alarm_manager.setExact(

                    AlarmManager.RTC_WAKEUP,

                    trigger_at_ms,

                    pending_intent,
                )


            print(
                f"FletAlarm: Alarm {alarm_id} "
                "scheduled SUCCESSFULLY."
            )


            return True


        except Exception as e:

            print(
                "FletAlarm: ERROR scheduling "
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

        # ----------------------------------------------------
        # DESKTOP
        # ----------------------------------------------------

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


        if self.context is None:
            return False


        if self.alarm_manager is None:
            return False


        if AlarmReceiver is None:
            return False


        try:

            # ------------------------------------------------
            # SAME INTENT IDENTITY
            # ------------------------------------------------

            intent = self._build_alarm_intent(
                alarm_id=alarm_id
            )


            # ------------------------------------------------
            # GET EXISTING PENDING INTENT
            # ------------------------------------------------

            pending_intent = (
                PendingIntent.getBroadcast(

                    self.context,

                    int(alarm_id),

                    intent,

                    self._pending_intent_flags(
                        no_create=True
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
                    f"FletAlarm: No PendingIntent "
                    f"found for alarm {alarm_id}."
                )


            # ------------------------------------------------
            # CANCEL NOTIFICATION
            # ------------------------------------------------

            try:

                Notification(
                    id=alarm_id
                ).cancel(
                    alarm_id
                )

            except Exception as e:

                print(
                    "FletAlarm: Notification cancellation "
                    f"failed: {e}"
                )


            return True


        except Exception as e:

            print(
                "FletAlarm: Error cancelling "
                f"alarm {alarm_id}: {e}"
            )

            return False
