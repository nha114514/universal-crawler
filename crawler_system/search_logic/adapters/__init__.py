"""Search adapters for Google, Bing, and Bilibili."""
from .bing import BingAdapter, decode_bing_redirect
from .bilibili import BilibiliAdapter
from .google import GoogleAdapter, decode_google_redirect

__all__ = [
    "BingAdapter",
    "BilibiliAdapter",
    "GoogleAdapter",
    "decode_bing_redirect",
    "decode_google_redirect"
]
