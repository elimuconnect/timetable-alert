"""
Inject the native AlarmReceiver into the Flutter project that `flet build`
generates, so it is compiled into the APK.

Usage:
    python tools/inject_native.py build/flutter native/AlarmReceiver.kt

Why this is needed: `flet build` generates its own Android project under
build/flutter and ignores any android/ folder in your repo. The Kotlin file
and the <receiver> manifest entry must be added to that generated project.
"""

import pathlib
import re
import shutil
import sys

PACKAGE = "org.digielimu.classalert"

RECEIVER_XML = (
    "\n        <receiver\n"
    f'            android:name="{PACKAGE}.AlarmReceiver"\n'
    '            android:exported="false" />\n    '
)

TTS_INTENT_XML = (
    "\n        <intent>\n"
    '            <action android:name="android.intent.action.TTS_SERVICE" />\n'
    "        </intent>\n    "
)

QUERIES_BLOCK = (
    "    <queries>"
    f"{TTS_INTENT_XML}"
    "</queries>\n\n"
)


def fail(message):
    print(f"ERROR: {message}")
    sys.exit(1)


def main():
    if len(sys.argv) != 3:
        fail("usage: inject_native.py <flutter_project_dir> <AlarmReceiver.kt>")

    project = pathlib.Path(sys.argv[1]).resolve()
    kotlin_src = pathlib.Path(sys.argv[2]).resolve()

    if not kotlin_src.is_file():
        fail(f"Kotlin source not found: {kotlin_src}")

    main_dir = project / "android" / "app" / "src" / "main"
    manifest_path = main_dir / "AndroidManifest.xml"

    if not manifest_path.is_file():
        fail(f"AndroidManifest.xml not found at {manifest_path}")

    # ---- 1. Copy the Kotlin file into the app's package folder ----
    kotlin_dir = main_dir / "kotlin" / pathlib.Path(*PACKAGE.split("."))
    kotlin_dir.mkdir(parents=True, exist_ok=True)
    destination = kotlin_dir / kotlin_src.name
    shutil.copyfile(kotlin_src, destination)
    print(f"Copied {kotlin_src.name} -> {destination}")

    # ---- 2. Patch the manifest ----
    text = manifest_path.read_text(encoding="utf-8")

    if f"{PACKAGE}.AlarmReceiver" not in text:
        end_app = text.rfind("</application>")
        if end_app == -1:
            fail("</application> not found in manifest")
        text = text[:end_app] + RECEIVER_XML + text[end_app:]
        print("Added <receiver> to manifest")
    else:
        print("<receiver> already present")

    if "android.intent.action.TTS_SERVICE" not in text:
        if "</queries>" in text:
            text = text.replace("</queries>", TTS_INTENT_XML + "</queries>", 1)
            print("Added TTS_SERVICE to existing <queries>")
        else:
            app_start = re.search(r"<application\b", text)
            if app_start is None:
                fail("<application> not found in manifest")
            index = app_start.start()
            text = text[:index] + QUERIES_BLOCK.lstrip(" ") + "    " + text[index:]
            print("Added new <queries> block with TTS_SERVICE")
    else:
        print("TTS_SERVICE query already present")

    manifest_path.write_text(text, encoding="utf-8")

    # ---- 3. Diagnostics: confirm the app's package/namespace ----
    for name in ("build.gradle", "build.gradle.kts"):
        gradle = project / "android" / "app" / name
        if gradle.is_file():
            for line in gradle.read_text(encoding="utf-8").splitlines():
                if "namespace" in line or "applicationId" in line:
                    print(f"{name}: {line.strip()}")

    print("Native injection complete.")


if __name__ == "__main__":
    main()
