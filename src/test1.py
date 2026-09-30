import flet as ft
from datetime import time, datetime, timedelta
from android_notify import Notification, NotificationHandler
#from flet_android_notifications import FletAndroidNotifications
import time as tm
import asyncio
import os
import json
import flet_permission_handler as fph
import flet_webview_all as fwa
import pathlib


class smart_alert:
    def __init__(self, page: ft.Page):

        self.page = page
        BASE_DIR = pathlib.Path(__file__).resolve().parent
        IILE = BASE_DIR / "index.html"

        with open(IILE, "r", encoding="utf-8") as file:
            self.html_read = file.read()
        self.webview = fwa.FletWebviewAll(
        html=self.html_read,
        expand=True,
    )
        self.webview.javascript_channels = ["FletBridge"]
        self.webview.on_javascript_message = self.on_message

        self.page.appbar = ft.AppBar(
            title=ft.Text(value="Smart Timetable Lesson Alert"),
            center_title=True,
            bgcolor=ft.Colors.BLUE
        )

        self.page.floating_action_button = ft.FloatingActionButton(
                    icon=ft.Icons.LIBRARY_BOOKS_SHARP,
                    
                    bgcolor=ft.Colors.GREEN_600,
                    foreground_color=ft.Colors.WHITE,
                )
        self.page.floating_action_button_location = ft.FloatingActionButtonLocation.END_FLOAT
        

        self.page.add(self.webview)

    def on_message(self, e):
        print(f"JavaScript [{e.channel_name}]: {e.message_body}")
        table_json = e.message_body   

        for lesson in table_json:
            day = lesson["Day"]
            Time = lesson["Time"].split("-")[0].strip() 
            Lesson = lesson["Lesson"]
        

class ClassAlert:
    ALARM_CHANNEL_ID = "class_alerts"
    ALARM_CHANNEL_NAME = "Class Reminders"
    THEME_PREF_KEY = "ui.theme_mode"
    SETTINGS_FILE = "settings.json"
    ALL_CLASSES_FILTER = "All Classes"

    def __init__(self, page: ft.Page):
        self.page = page
        self._last_handled_alarm_signature = None
        self.page.scroll = ft.ScrollMode.HIDDEN
        self.page.theme_mode = self._load_theme_mode()

        def handle_switch_change(e: ft.Event[ft.Switch]):
                self.page.theme_mode = ft.ThemeMode.DARK if e.control.value else ft.ThemeMode.LIGHT
                self._save_theme_mode(self.page.theme_mode)
                self.page.update()

        self.theme_switch = ft.Switch(
            value=self.page.theme_mode == ft.ThemeMode.DARK,
            on_change=handle_switch_change,
            height=30,
        )

        

        
        

        
        #self.notifications = FletAndroidNotifications()
        self.id_counter = 0

        def next_weekday(day_name, t):
            days = ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"]
            today = datetime.now()
            day_num = days.index(day_name)
            days_ahead = day_num - today.weekday()

            if days_ahead == 0:
                target_time = datetime.combine(today.date(), t)
                if target_time <= today:
                    days_ahead = 7
                else:
                    days_ahead = 0
            elif days_ahead < 0:
                days_ahead += 7

            return datetime.combine(today.date() + timedelta(days=days_ahead), t)

        async def add_func(e):
                date_str = self.date.value or "Monday"
                grade = self._get_class_name_input()
                subject = self._get_subject_input()

                if not subject or not grade or self.time_pick.value is None:
                    self.page.show_dialog(ft.SnackBar(
                        content=ft.Text("Please fill in class, subject, and time before adding."),
                        bgcolor=ft.Colors.RED_400,
                    ))
                    return

                self._refresh_id_counter()
                self.id_counter += 1
                nt_id = self.id_counter
                time_str = self.time_pick.value.strftime("%H:%M")
                dt_str = f"{date_str} {time_str}"
                class_time = next_weekday(date_str, self.time_pick.value)
                reminder_minutes = self._parse_reminder_minutes()
                schld_time = self._calculate_alarm_time(class_time, reminder_minutes)
                container = ft.Container(
                        padding=ft.Padding.all(10),
                        margin=ft.Margin.all(5),
                        gradient=ft.LinearGradient(
                            begin=ft.Alignment.TOP_LEFT,
                            end=ft.Alignment.BOTTOM_RIGHT,
                            colors=[ft.Colors.BLUE_900, ft.Colors.BLUE_300]
                        ),
                        border_radius=ft.BorderRadius.all(30),
                    )
                container.content = ft.Row(
                    controls=[
                        ft.Column(
                            controls=[
                                ft.Text(f"Class: {grade}", size=15, weight=ft.FontWeight.BOLD),
                                ft.Text(f"Subject: {subject}", size=15, weight=ft.FontWeight.NORMAL),
                                ft.Text(f"Date: {self.date.value}", size=15, weight=ft.FontWeight.NORMAL),
                                ft.Text(f"Time: {self.time_pick.value}", size=15, weight=ft.FontWeight.NORMAL),
                            ],
                            expand=True,
                        ),
                        ft.IconButton(
                            icon=ft.Icons.DELETE,
                            tooltip="Delete Entry",
                            icon_color=ft.Colors.RED_400,
                            on_click=lambda _, id=nt_id, cont=container: asyncio.create_task(self.cancel_notification(id, cont=cont))
                        )
                    ]
                )

                self.list_timetable.controls.append(
                    container
                )
                self.page.pop_dialog()
                self.page.update()
                await self.save_and_notify_full(
                    nt_id,
                    schld_time,
                    subject,
                    grade,
                    class_time=class_time,
                    reminder_before=reminder_minutes,
                )
                await self.readtimetable()
                
        
        

        self.subject_name = ft.Dropdown(
            label="Subject Name",
            width=200,
            hint_text="Type or select subject (e.g., Mathematics)",
            editable=True,
            enable_search=True,
            options=[],
        )
        self.date = ft.Dropdown(
            label="Day",
            options=[
                ft.dropdown.Option("Monday"),
                ft.dropdown.Option("Tuesday"),
                ft.dropdown.Option("Wednesday"),
                ft.dropdown.Option("Thursday"),
                ft.dropdown.Option("Friday"),
                ft.dropdown.Option("Saturday"),
                ft.dropdown.Option("Sunday"),
            ]
         )
        
        self.time_pick = ft.TimePicker(
            confirm_text="Set",
            value=time(1, 2),
            entry_mode=ft.TimePickerEntryMode.DIAL,
            help_text="Select the time for the class.",
            on_change=lambda _: setattr(self.time_text, "value", f"Select Time: {self.time_pick.value}")
            )
        self.class_name = ft.Dropdown(
            label="Class Name",
            width=200,
            hint_text="Type or select class (e.g., Grade 10A)",
            editable=True,
            enable_search=True,
            options=[],
        )

        self.btntime = ft.Button(
            content=ft.Text("Pick Time"),
            on_click=lambda _: self.page.show_dialog(self.time_pick),
        )

        self.reminder_before = ft.Dropdown(
            label="Reminder Before",
            value="5 minutes",
            options=[
                ft.dropdown.Option("0 minutes"),
                ft.dropdown.Option("2 minutes"),
                ft.dropdown.Option("5 minutes"),
                ft.dropdown.Option("10 minutes"),
                ft.dropdown.Option("15 minutes"),
                ft.dropdown.Option("30 minutes"),
                
            ]
        )

        self.time_text = ft.Text(f"Select Time: {self.time_pick.value}")
                    

        self.addcontrols = ft.ListView(
            controls=[
                self.class_name,
                self.subject_name,
                self.date,
                self.time_text,
                self.btntime,
                self.reminder_before
            ],
            spacing=20,
            scroll=ft.ScrollMode.HIDDEN,
            
        )


        self.add_dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text("Add Lessons", size=20, weight=ft.FontWeight.BOLD, text_align=ft.TextAlign.CENTER),
            content=ft.Container(
                content=self.addcontrols,
              ),
            actions=[
                ft.TextButton("Cancel", on_click=lambda _: self.page.pop_dialog()),
                ft.TextButton("Add", on_click=add_func),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
           
        
            

        self.container = ft.Container(
            padding=ft.Padding.only(left=20, top=25, right=20, bottom=20),
            margin=ft.Margin.all(5),
            expand=True,
            #alignment=ft.Alignment.CENTER,
            gradient=ft.LinearGradient(
                begin=ft.Alignment.TOP_LEFT,
                end=ft.Alignment.BOTTOM_RIGHT,
                colors=[ft.Colors.BLUE_900, ft.Colors.BLUE_500]
            ),
            border_radius=ft.BorderRadius.all(30),
            
            content=ft.Column(
                controls=[
                    ft.Container(
                        #margin=ft.Margin.only(left=25, top=25),
                        alignment=ft.Alignment.CENTER,
                        gradient=ft.LinearGradient(
                            begin=ft.Alignment.TOP_CENTER,
                            end=ft.Alignment.BOTTOM_CENTER,
                            colors=[ft.Colors.BLUE_700, ft.Colors.BLUE_400]
                        ),
                        border_radius=ft.BorderRadius.all(30),
                        expand=False,
                        width=250,
                        height=30,
                        content=ft.Text(
                            "Smart timetable reminder for teachers",
                            size=13,
                            weight=ft.FontWeight.W_600,
                            color=ft.Colors.WHITE,
                            expand=True,
                        )
                    ),
                    ft.Text(
                        value="Stay ahead of every class.",
                        size=40,
                        weight=ft.FontWeight.BOLD,
                        #margin=ft.Margin.only(top=5, left=25),
                        color=ft.Colors.WHITE,
                    ),
                    ft.Row(
                        controls=[
                            ft.Column(
                                controls=[
                                    ft.Text(
                                        value="Add lessons manually and get reminders before each class starts.",
                                        size=15,
                                        weight=ft.FontWeight.NORMAL,
                                        #margin=ft.Margin.only(top=5, left=25),
                                        color=ft.Colors.WHITE,
                                        expand=True,
                                    )
                                ],
                                expand=True,
                            )
                        ]
                        ),

                    ft.Row(
                        controls=[
                            ft.Button(
                                icon=ft.Icons.CALENDAR_MONTH,
                                content=ft.Text("Add Lessons"),
                                
                                on_click=lambda _: self.page.show_dialog(self.add_dialog),
                                expand=True,
                            )
                        ],
                        alignment=ft.MainAxisAlignment.CENTER,
                    )
                ]
            )
        )

        self.list_timetable = ft.ListView(
            controls=[
                ft.Text(
                    value="Your Timetable",
                    size=30,
                    weight=ft.FontWeight.BOLD,
                    color=ft.Colors.BLUE_900,
                    text_align=ft.TextAlign.CENTER,
                    theme_style=ft.TextThemeStyle.TITLE_MEDIUM
                )
            ],
            expand=True,
            scroll=ft.ScrollMode.HIDDEN
            
        )

        self.class_filter = ft.Dropdown(
            label="Filter by class",
            value=self.ALL_CLASSES_FILTER,
            options=[ft.dropdown.Option(self.ALL_CLASSES_FILTER)],
            on_select=lambda _: asyncio.create_task(self.readtimetable()),
            width=220,
        )
        
        asyncio.create_task(self.request_permission())
        asyncio.create_task(self.readtimetable())
        asyncio.create_task(self.check_for_alarm_intent())
        asyncio.create_task(self.monitor_alarm_intents())

        self.page.on_route_change = lambda _: asyncio.create_task(self.check_for_alarm_intent())
        self.page.on_resume = lambda _: asyncio.create_task(self.check_for_alarm_intent())



        self.page.add(
            ft.Row(
                controls=[self.container,],
                alignment=ft.MainAxisAlignment.CENTER,
            ),
            ft.Row(
                controls=[self.class_filter],
                alignment=ft.MainAxisAlignment.START,
            ),
            ft.Row(
                controls=[self.list_timetable,],
                alignment=ft.MainAxisAlignment.CENTER,
            )

        )

    def _refresh_id_counter(self, records=None):
        if records is None:
            records = self._load_alert_records()
        self.id_counter = max((record["id"] for record in records), default=0)

    def _load_theme_mode(self) -> ft.ThemeMode:
        settings = self._load_settings()
        saved = settings.get(self.THEME_PREF_KEY, "light")
        if str(saved).lower() == "dark":
            return ft.ThemeMode.DARK
        return ft.ThemeMode.LIGHT

    def _save_theme_mode(self, mode: ft.ThemeMode):
        settings = self._load_settings()
        settings[self.THEME_PREF_KEY] = "dark" if mode == ft.ThemeMode.DARK else "light"
        self._save_settings(settings)

    def _load_settings(self) -> dict:
        if not os.path.exists(self.SETTINGS_FILE):
            return {}

        try:
            with open(self.SETTINGS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, dict) else {}
        except Exception as e:
            print(f"Failed to load settings: {e}")
            return {}

    def _save_settings(self, settings: dict):
        try:
            with open(self.SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump(settings, f)
        except Exception as e:
            print(f"Failed to save settings: {e}")

    def _get_class_name_input(self) -> str:
        value = getattr(self.class_name, "value", None)
        if value:
            return str(value).strip()

        text = getattr(self.class_name, "text", None)
        if text:
            return str(text).strip()

        return ""

    def _get_subject_input(self) -> str:
        value = getattr(self.subject_name, "value", None)
        if value:
            return str(value).strip()

        text = getattr(self.subject_name, "text", None)
        if text:
            return str(text).strip()

        return ""

    def _selected_class_filter(self) -> str:
        return self.class_filter.value or self.ALL_CLASSES_FILTER

    def _passes_class_filter(self, grade: str) -> bool:
        selected = self._selected_class_filter()
        if selected == self.ALL_CLASSES_FILTER:
            return True
        return grade == selected

    def _update_class_filter_options(self, records: list[dict]):
        classes = self._get_class_names(records)
        options = [ft.dropdown.Option(self.ALL_CLASSES_FILTER)] + [
            ft.dropdown.Option(class_name) for class_name in classes
        ]
        self.class_filter.options = options

        selected = self._selected_class_filter()
        valid_values = {self.ALL_CLASSES_FILTER, *classes}
        if selected not in valid_values:
            self.class_filter.value = self.ALL_CLASSES_FILTER

    def _get_class_names(self, records: list[dict]) -> list[str]:
        return sorted({record["grade"] for record in records if record.get("grade")})

    def _update_class_name_options(self, records: list[dict]):
        classes = self._get_class_names(records)
        current_input = self._get_class_name_input()
        self.class_name.options = [ft.dropdown.Option(class_name) for class_name in classes]
        if current_input and current_input in classes:
            self.class_name.value = current_input

    def _get_subject_names(self, records: list[dict]) -> list[str]:
        return sorted({record["subject"] for record in records if record.get("subject")})

    def _update_subject_options(self, records: list[dict]):
        subjects = self._get_subject_names(records)
        current_input = self._get_subject_input()
        self.subject_name.options = [ft.dropdown.Option(subject_name) for subject_name in subjects]
        if current_input and current_input in subjects:
            self.subject_name.value = current_input

    def _load_alert_records(self):
        records = []
        if not os.path.exists("alerts.txt"):
            return records

        with open("alerts.txt", "r") as f:
            for line in f:
                raw_line = line.strip()
                if not raw_line:
                    continue

                try:
                    parts = raw_line.split("|")

                    if len(parts) == 4:
                        nt_id_str, schld_time_str, subject, grade = parts
                        class_time = datetime.fromisoformat(schld_time_str)
                        reminder_before = 0
                    elif len(parts) == 6:
                        nt_id_str, alarm_time_str, class_time_str, reminder_before_str, subject, grade = parts
                        schld_time_str = alarm_time_str
                        class_time = datetime.fromisoformat(class_time_str)
                        reminder_before = int(reminder_before_str)
                    else:
                        raise ValueError("Unexpected alert record format")

                    records.append(
                        {
                            "id": int(nt_id_str),
                            "time": datetime.fromisoformat(schld_time_str),
                            "class_time": class_time,
                            "reminder_before": reminder_before,
                            "subject": subject,
                            "grade": grade,
                        }
                    )
                except ValueError:
                    print(f"Skipping malformed alert entry: {raw_line}")

        return records
    
    

    def _save_alert_records(self, records):
        with open("alerts.txt", "w") as f:
            for record in records:
                f.write(
                    f"{record['id']}|{record['time'].isoformat()}|{record['class_time'].isoformat()}|{record['reminder_before']}|{record['subject']}|{record['grade']}\n"
                )

    def _parse_reminder_minutes(self) -> int:
        raw_value = self.reminder_before.value or "5 minutes"
        try:
            return int(str(raw_value).split()[0])
        except (ValueError, IndexError):
            return 5

    def _calculate_alarm_time(self, class_time: datetime, reminder_before: int) -> datetime:
        alarm_time = class_time - timedelta(minutes=reminder_before)
        if alarm_time <= datetime.now():
            return class_time
        return alarm_time

    def _next_future_occurrence(self, scheduled_time: datetime):
        next_time = scheduled_time
        now = datetime.now()

        while next_time <= now:
            next_time += timedelta(days=7)

        return next_time

    def _schedule_exact_alarm(self, nt_id: int, schld_time: datetime, subject: str, grade: str):
        from flet_alarm import FletAlarm

        FletAlarm().set_alarm(
            schld_time,
            nt_id,
            title=f"Class Starting: {subject}, grade {grade}",
            message=f"Your {subject} class for {grade} is ready.",
            repeat_weekly=False,
        )

    def _build_alarm_notification(self, notification_id: int, title: str, body: str, use_custom_sound: bool = False):
        notif = Notification(id=notification_id, title=title, message=body)
        notif.icon_name = "ic_lock_idle_alarm"

        if use_custom_sound:
            sound_path = os.path.join(os.path.dirname(__file__), "assets", "alarmsound.mp3")
            notif.setSound(sound_path=sound_path)
        return notif

    def _send_notification_with_fallback(self, notif: Notification, body: str):
        try:
            try:
                notif.setBigText(body)
            except Exception:
                pass
            notif.send(silent=False)
            return True
        except Exception as send_error:
            print(f"Notification send error: {send_error}")
            return False

    def _show_class_dialog(self, day_name: str, time_str: str, subject: str, grade: str):
        class_dialog = ft.AlertDialog(
            title=ft.Text("Time for Class!"),
            content=ft.Text(
                f"It's {day_name} {time_str}.\nSubject: {subject}\nGrade: {grade}"
            ),
            actions=[
                ft.TextButton("Dismiss", on_click=lambda _: self.close_dialog())
             ],
        )
        self.page.show_dialog(class_dialog)

    def _reschedule_next_alarm_occurrence(self, alarm_id: int):
        records = self._load_alert_records()
        updated = False

        for record in records:
            if record["id"] != alarm_id:
                continue

            next_class_time = self._next_future_occurrence(record["class_time"] + timedelta(days=7))
            record["class_time"] = next_class_time
            record["time"] = self._calculate_alarm_time(next_class_time, record["reminder_before"])
            self._schedule_exact_alarm(
                record["id"],
                record["time"],
                record["subject"],
                record["grade"],
            )
            updated = True
            break

        if updated:
            self._save_alert_records(records)

    def _find_triggered_record(self, now: datetime, fallback_alarm_id: int | None = None):
        records = self._load_alert_records()
        current_weekday = now.strftime("%A")
        current_time_str = now.strftime("%H:%M")

        for record in records:
            record_day = record["time"].strftime("%A")
            record_time = record["time"].strftime("%H:%M")

            if record_day == current_weekday and record_time == current_time_str:
                return record

        if fallback_alarm_id is not None:
            for record in records:
                if record["id"] == fallback_alarm_id:
                    return record

        return None

    async def check_for_alarm_intent(self):
        try:
            from flet_alarm import PythonActivity, cast

            if not PythonActivity:
                return

            raw_activity = PythonActivity.mActivity
            if raw_activity is None:
                return

            activity = cast("android.app.Activity", raw_activity)
            intent = activity.getIntent()
            if intent is None or not intent.getBooleanExtra("is_alarm_trigger", False):
                return

            notification_id = intent.getIntExtra("notification_id", 0)
            alarm_id = intent.getIntExtra("alarm_id", notification_id)
            scheduled_at_ms = intent.getLongExtra("scheduled_at_ms", 0)
            alarm_signature = (alarm_id, scheduled_at_ms)

            if self._last_handled_alarm_signature == alarm_signature:
                return

            self._last_handled_alarm_signature = alarm_signature

            target_record = self._find_triggered_record(datetime.now(), fallback_alarm_id=alarm_id)
            if target_record is not None:
                title = f"Class Starting: {target_record['subject']}"
                body = f"Grade: {target_record['grade']} is waiting for you."
                notification_id = target_record["id"]
            else:
                title = intent.getStringExtra("notification_title") or "Class Reminder"
                body = intent.getStringExtra("notification_body") or "Check your timetable."

            notif = self._build_alarm_notification(notification_id, title, body, use_custom_sound=True)
            self._send_notification_with_fallback(notif, body)

            if target_record is not None:
                class_time = target_record.get("class_time", datetime.now())
                self._show_class_dialog(
                    class_time.strftime("%A"),
                    class_time.strftime("%H:%M"),
                    target_record["subject"],
                    target_record["grade"],
                )

            self._reschedule_next_alarm_occurrence(alarm_id)

            try:
                intent.removeExtra("is_alarm_trigger")
                intent.removeExtra("notification_id")
                intent.removeExtra("notification_title")
                intent.removeExtra("notification_body")
            except Exception:
                pass
        except Exception as e:
            print(f"Intent check error: {e}")

    async def monitor_alarm_intents(self):
        while True:
            await self.check_for_alarm_intent()
            await asyncio.sleep(1)
    
    async def save_and_notify_full(
        self,
        nt_id: int,
        schld_time: datetime,
        subject: str,
        grade: str,
        class_time: datetime | None = None,
        reminder_before: int = 5,
    ):
         try:
              class_time = class_time or schld_time
              records = self._load_alert_records()
              records.append(
                   {
                        "id": nt_id,
                        "time": schld_time,
                        "class_time": class_time,
                        "reminder_before": reminder_before,
                        "subject": subject,
                        "grade": grade,
                   }
              )
              self._save_alert_records(records)
              self._refresh_id_counter(records)

              noti = self._build_alarm_notification(
                    notification_id=nt_id,
                    title=f"Adding Class: {grade} - {subject}",
                    body=f"Your {subject} class for {grade} starts at {class_time.strftime('%H:%M on %A')}. Get ready!",
                    use_custom_sound=False,
              )
              self._send_notification_with_fallback(
                    noti,
                    f"Your {subject} class for {grade} starts at {class_time.strftime('%H:%M on %A')}. Get ready!"
              )

              try:
                   self._schedule_exact_alarm(nt_id, schld_time, subject, grade)
              except Exception as alarm_error:
                   print(f"Alarm scheduling unavailable: {alarm_error}")

         except Exception as e:
              print(f"Error requesting permissions: {e}")
              return
    
    async def test_notification(self):
        try:
            test = self._build_alarm_notification(
                notification_id=999,
                title="Test Notification",
                body="If you see this, notifications are working!",
                use_custom_sound=False,
            )
            sent = self._send_notification_with_fallback(
                test,
                "If you see this, notifications are working!",
            )

            #self.TextToSpeech("This is a test notification. If you see this, notifications are working!")
            self.page.show_dialog(ft.SnackBar(
                content=ft.Text(
                    "Test sent! Check your notification tray." if sent else "Test failed. Check Android notification settings."
                ),
                bgcolor=ft.Colors.GREEN_400 if sent else ft.Colors.RED_400,
            ))
            print(f"Test notification sent: {sent}")
        except Exception as e:
            
            #self.TextToSpeech("Error scheduling test notification")
            self.page.show_dialog(ft.SnackBar(
                content=ft.Text(f"Error scheduling test notification: {e}"),
                bgcolor=ft.Colors.RED_400,
            ))
            print(f"Error scheduling test notification: {e}")

    
    async def request_permission(self):
        ph = fph.PermissionHandler()
        
        status = await ph.request(fph.Permission.SCHEDULE_EXACT_ALARM)
        statu = await ph.request(fph.Permission.NOTIFICATION)
        stat = await ph.request(fph.Permission.SYSTEM_ALERT_WINDOW)
        sta = await ph.request(fph.Permission.IGNORE_BATTERY_OPTIMIZATIONS)
        

        if status.name == "DENIED":
            self.page.show_dialog(ft.SnackBar(
                content=ft.Text("Schedule exact alarm permission is required for timely class reminders. Please enable it in settings."),
                bgcolor=ft.Colors.RED_400,
            ))

        if statu.name == "DENIED":
            self.page.show_dialog(ft.SnackBar(
                content=ft.Text("Notification permission is required for timely class reminders. Please enable it in settings."),
                bgcolor=ft.Colors.RED_400,
            ))

        elif stat.name == "DENIED":
                self.page.show_dialog(ft.SnackBar(
                    content=ft.Text("Overlay permission is required to show class reminders on top of other apps. Please enable it in settings."),
                    bgcolor=ft.Colors.RED_400,
                ))

        

        

    async def cancel_notification(self, nt_id: int, cont: ft.Container):
        try:
            records = [record for record in self._load_alert_records() if record["id"] != nt_id]
            self._save_alert_records(records)
            self._refresh_id_counter(records)

            self.list_timetable.controls.remove(cont)
            self._update_class_filter_options(records)
            self._update_class_name_options(records)
            self._update_subject_options(records)

            try:
                from flet_alarm import FletAlarm
                FletAlarm().cancel_alarm(nt_id)
            except Exception as alarm_error:
                print(f"Alarm cancellation unavailable: {alarm_error}")

            Notification(id=nt_id).cancel(nt_id)
            print(f"Notification with ID {nt_id} cancelled.")
            self.page.update()
               
        except Exception as e:
            print(f"Error cancelling notification: {e}") 

    def close_dialog(self):
        self.page.pop_dialog()
        self.page.update()

    async def readtimetable(self):
            header = self.list_timetable.controls[:1]
            self.list_timetable.controls.clear()
            self.list_timetable.controls.extend(header)

            records = self._load_alert_records()
            self._refresh_id_counter(records)
            self._update_class_filter_options(records)
            self._update_class_name_options(records)
            self._update_subject_options(records)

            if records:
                normalized = False
                now = datetime.now()
                current_day_name = now.strftime("%A")
                current_time_str = now.strftime("%H:%M")

                for record in records:
                    nt_id = record["id"]
                    subject = record["subject"]
                    grade = record["grade"]
                    class_time = self._next_future_occurrence(record.get("class_time", record["time"]))
                    schld_time = self._calculate_alarm_time(class_time, record.get("reminder_before", 0))

                    if schld_time != record["time"] or class_time != record.get("class_time", record["time"]):
                        record["time"] = schld_time
                        record["class_time"] = class_time
                        normalized = True

                    day_name = class_time.strftime("%A")

                    if day_name == current_day_name and schld_time.strftime("%H:%M") == current_time_str:
                        notif = self._build_alarm_notification(
                            nt_id,
                            f"Class Starting: {subject}",
                            f"Grade: {grade}",
                            use_custom_sound=True
                        )
                        self._send_notification_with_fallback(notif, f"Grade: {grade}")
                        self._show_class_dialog(day_name, current_time_str, subject, grade)
                        

                    try:
                        self._schedule_exact_alarm(nt_id, schld_time, subject, grade)
                    except Exception as alarm_error:
                        print(f"Alarm restore unavailable: {alarm_error}")

                    if not self._passes_class_filter(grade):
                        continue

                    container = ft.Container(
                        padding=ft.Padding.all(10),
                        margin=ft.Margin.all(5),
                        gradient=ft.LinearGradient(
                            begin=ft.Alignment.TOP_LEFT,
                            end=ft.Alignment.BOTTOM_RIGHT,
                            colors=[ft.Colors.BLUE_900, ft.Colors.BLUE_300]
                        ),
                        border_radius=ft.BorderRadius.all(30),
                    )
                    container.content = ft.Row(
                        controls=[
                            ft.Column(
                                controls=[
                                    ft.Text(f"Class: {grade}", size=18, weight=ft.FontWeight.BOLD),
                                    ft.Text(f"Subject: {subject}", size=15, weight=ft.FontWeight.NORMAL),
                                    ft.Text(f"Day: {day_name}", size=15, weight=ft.FontWeight.NORMAL),
                                    ft.Text(
                                        f"Time: {class_time.strftime('%I:%M %p')}",
                                        size=15,
                                        weight=ft.FontWeight.NORMAL,
                                    ),
                                ],
                                expand=True,
                            ),
                            ft.IconButton(
                                icon=ft.Icons.DELETE,
                                tooltip="Delete Entry",
                                icon_color=ft.Colors.RED_400,
                                on_click=lambda e, id=nt_id, cont=container: asyncio.create_task(
                                    self.cancel_notification(id, cont=cont)
                                )
                            )
                        ]
                    )

                    self.list_timetable.controls.append(container)

                if normalized:
                    self._save_alert_records(records)

            self.page.update()
                   

                
                    

def main(page: ft.Page):
    page.title = "Class Alert"
    #ClassAlert(page)
    smart_alert(page)

ft.run(main, assets_dir="assets")
