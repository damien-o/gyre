import os

debug_path = os.environ.get("SD_DEBUG_PATH", False)
if not debug_path:
    debug_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "/tests/out/")

# Logic copied from https://github.com/huggingface/huggingface_hub/blob/main/src/huggingface_hub/constants.py#L74
default_home = os.path.join(os.path.expanduser("~"), ".cache")
sd_cache_home = os.path.expanduser(
    os.getenv(
        "SD_HOME",
        os.path.join(os.getenv("XDG_CACHE_HOME", default_home), "gyre"),
    )
)

GYRE_BASE_PATH = os.path.dirname(os.path.abspath(__file__))
