"""Constants for the Kramer integration."""

DOMAIN = "kramer"

CONF_HOST = "ip_address"  # key kept from earlier versions
CONF_PORT = "port"
CONF_MACHINE = "machine"
CONF_MODEL = "model"
CONF_MODEL_NAME = "model_name"
CONF_INPUTS = "inputs"
CONF_OUTPUTS = "outputs"
CONF_MODES = "supports_modes"

CONF_PRESET_SLOTS = "preset_slots"
CONF_PRESET_DESCRIPTIONS = "preset_descriptions"

CONF_INPUT_NAMES = "input_names"
CONF_AUDIO_INPUT_NAMES = "audio_input_names"
CONF_OUTPUT_NAMES = "output_names"
CONF_AUDIO_OUTPUT_NAMES = "audio_output_names"

CONF_CUSTOM_REFRESH = "custom_refresh"
CONF_REFRESH_SECONDS = "refresh_seconds"
DEFAULT_REFRESH = 60
MIN_REFRESH = 5
MAX_REFRESH = 1800

DEFAULT_PORT = 9001
DEFAULT_MACHINE = 1

SOURCE_OFF = "Off"
SOURCE_MIXED = "Mixed"
