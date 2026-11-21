"""Constants for Family WiFi."""

DOMAIN = "family_wifi"
SIGNAL_UPDATE = "family_wifi_update"

CONF_SITE = "site"
CONF_VERIFY_SSL = "verify_ssl"

OPT_GROUPS = "groups"  # {"Kids": ["aa:bb:..", ...]}
OPT_NAMES = "names"  # {"aa:bb:..": "Alex iPad"}
OPT_SITE_LISTS = "site_lists"  # {"Games": ["roblox.com", ...]}
OPT_SPEEDS = "speeds"  # [{"name": ..., "down_kbps": ..., "up_kbps": ...}]

DEFAULT_SITE_LISTS = {
    "Games": ["roblox.com", "fortnite.com", "epicgames.com", "minecraft.net"],
    "Video": ["youtube.com", "googlevideo.com", "ytimg.com", "tiktok.com", "twitch.tv"],
    "Social": ["tiktok.com", "instagram.com", "snapchat.com", "discord.com"],
}
DEFAULT_SPEEDS = [
    {"name": "Slow (1 Mbps)", "down_kbps": 1000, "up_kbps": 500},
    {"name": "Very slow (256 kbps)", "down_kbps": 256, "up_kbps": 128},
    {"name": "Homework (5 Mbps)", "down_kbps": 5000, "up_kbps": 2000},
]
