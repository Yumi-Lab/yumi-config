"""Add the error screen's Autoconfig entry to KlipperScreen.conf (install.sh), above the
"#~# --- Do not edit below" block that KlipperScreen rewrites (an entry appended at the end was
dropped, 15/11). No-op when the entry is already there."""
import sys

SECTION = "[menu __splashscreen autoconfig]"
BLOCK = SECTION + "\nname: Autoconfig\nicon: refresh\npanel: autoconfig\n\n"
MARKER = "#~# --- Do not edit below"


def with_entry(text):
    if SECTION in text:
        return text
    at = text.find(MARKER)
    return text[:at] + BLOCK + text[at:] if at >= 0 else text.rstrip("\n") + "\n\n" + BLOCK


if __name__ == "__main__":
    path = sys.argv[1]
    with open(path) as f:
        text = f.read()
    new = with_entry(text)
    if new != text:
        with open(path, "w") as f:
            f.write(new)
        print("Added Autoconfig to the error screen menu")
