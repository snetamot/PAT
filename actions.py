"""Mouse/keyboard action primitives."""

import time
import pyautogui

pyautogui.FAILSAFE = True   # slam mouse to a screen corner to abort a runaway script
pyautogui.PAUSE = 0.05


def move(x, y, duration=0.2):
    pyautogui.moveTo(x, y, duration=duration)


def click(x=None, y=None, button="left"):
    if x is not None and y is not None:
        pyautogui.moveTo(x, y, duration=0.15)
    pyautogui.click(button=button)


def drag(x1, y1, x2, y2, duration=0.4, click_first=False):
    pyautogui.moveTo(x1, y1, duration=0.15)
    if click_first:
        pyautogui.click()
    pyautogui.dragTo(x2, y2, duration=duration, button="left")


def type_text(text, interval=0.02):
    pyautogui.typewrite(text, interval=interval)


def press_key(key, count=1):
    pyautogui.press(key, presses=count, interval=0.05)


def wait(ms):
    time.sleep(ms / 1000.0)
