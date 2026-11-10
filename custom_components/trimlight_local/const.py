from datetime import timedelta

from .effects import CUSTOM_MODES_V2
from .trimlight import Repeat

DOMAIN = "trimlight_local"

DEFAULT_SPEED = 100
DEFAULT_PIXEL_LEN = 30
DEFAULT_PATTERN_DOTS = 5

SCAN_INTERVAL = timedelta(seconds=30)
# When sun-following schedule times are rewritten each day: after morning windows end and before evening ones start.
SUN_UPDATE_HOUR = 12

# The app's music effects, by the number the controller uses [pp 0x249f8].
MUSIC_EFFECTS = (
    "Full Color Rhythm Spectrum",
    "Single Color Rhythm Spectrum",
    "Full Color Rhythm Twinkle",
    "Single Color Rhythm Twinkle",
    "Gradient Energy",
    "Single Color Energy",
    "Gradient Pulse",
    "Single Color Pulse",
    "Full Color Ejection Forward",
    "Single Color Ejection Forward",
    "Full Color Ejection Backward",
    "Single Color Ejection Backward",
    "Full Color VuMeter",
    "Single Color VuMeter",
    "Independence Day",
    "Christmas",
    "HeatBeat",
    "Party",
)

# Pattern modes as used in actions, e.g. "chase_forward", mapped to the controller's mode number.
PATTERN_MODES = {name.lower().replace(" ", "_"): index for index, name in enumerate(CUSTOM_MODES_V2)}

REPEATS = {"today_only": Repeat.TODAY_ONLY, "every_day": Repeat.EVERY_DAY, "weekdays": Repeat.WEEKDAYS, "weekend": Repeat.WEEKEND}

PANEL_URL_PATH = "trimlight"
# The house drawings the Trimlight page can show its preview on; the page holds the shapes.
ROOFLINES = ("house", "two_gables")

# Shown for the combined effect wherever a saved effect can be picked for a schedule.
COMBINED_EFFECT_NAME = "Combined effect"

SERVICE_SHOW_SAVED_EFFECT = "show_saved_effect"
SERVICE_SHOW_PATTERN = "show_pattern"
SERVICE_SAVE_EFFECT = "save_effect"
SERVICE_RENAME_SAVED_EFFECT = "rename_saved_effect"
SERVICE_DELETE_SAVED_EFFECT = "delete_saved_effect"
SERVICE_SET_OVERLAY = "set_overlay"
SERVICE_SET_COMBINED_EFFECT = "set_combined_effect"
SERVICE_SET_SCHEDULE = "set_schedule"
SERVICE_DELETE_SCHEDULE = "delete_schedule"
SERVICE_SET_DAILY_SCHEDULE = "set_daily_schedule"
SERVICE_SHOW_EFFECT = "show_effect"
SERVICE_START_MUSIC = "start_music"
ATTR_EFFECT_ID = "effect_id"
ATTR_MODE = "mode"
ATTR_COLORS = "colors"
ATTR_DOTS = "dots"
ATTR_SPEED = "speed"
ATTR_NAME = "name"
ATTR_NEW_NAME = "new_name"
ATTR_OVERLAY = "overlay"
ATTR_EFFECTS = "effects"
ATTR_INTERVAL = "interval"
ATTR_SCHEDULE_ID = "schedule_id"
ATTR_START_DATE = "start_date"
ATTR_HOLIDAY = "holiday"
ATTR_HOLIDAY_START_OFFSET = "holiday_start_offset"
ATTR_HOLIDAY_END_OFFSET = "holiday_end_offset"
ATTR_WAITING_ID = "waiting_id"
ATTR_END_DATE = "end_date"
ATTR_ON_TIME = "on_time"
ATTR_OFF_TIME = "off_time"
ATTR_ON_SUN = "on_sun"
ATTR_ON_OFFSET = "on_offset"
ATTR_OFF_SUN = "off_sun"
ATTR_OFF_OFFSET = "off_offset"
ATTR_SCHEDULE = "schedule"
ATTR_ENABLED = "enabled"
ATTR_REPEAT = "repeat"
ATTR_LENGTH = "length"
ATTR_REVERSE = "reverse"
ATTR_COLOR = "color"
ATTR_SENSITIVITY = "sensitivity"
