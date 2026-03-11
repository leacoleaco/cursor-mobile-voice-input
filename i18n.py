# -*- coding: utf-8 -*-
"""
Internationalization (i18n) support using gettext.
Locale is read from config_store.LOCALE; fallback to system locale or 'zh_CN'.
"""
import gettext
import os
from typing import Optional

from paths import resource_path

_CURRENT_LOCALE: Optional[str] = None
_TRANSLATION: Optional[gettext.NullTranslations] = None


def get_locale() -> str:
    """Return current locale (e.g. 'zh_CN', 'en')."""
    try:
        from config_store import LOCALE
        return (LOCALE or "").strip() or "zh_CN"
    except Exception:
        return "zh_CN"


def set_locale(locale: str) -> None:
    """Set locale and reload translations. Call config_store.save_config() after."""
    global _CURRENT_LOCALE, _TRANSLATION
    locale = (locale or "zh_CN").strip() or "zh_CN"
    _CURRENT_LOCALE = locale
    _TRANSLATION = None
    _load_translation()


def _load_translation() -> None:
    global _TRANSLATION
    locale = _CURRENT_LOCALE or get_locale()
    locales_dir = resource_path("locales")
    try:
        trans = gettext.translation(
            "messages",
            localedir=locales_dir,
            languages=[locale],
            fallback=True,
        )
        _TRANSLATION = trans
    except Exception:
        _TRANSLATION = gettext.NullTranslations()


def _(msg: str) -> str:
    """Translate message. Uses current locale from config_store."""
    global _TRANSLATION
    if _TRANSLATION is None:
        _load_translation()
    return _TRANSLATION.gettext(msg)


def _n(singular: str, plural: str, n: int) -> str:
    """Translate with plural form."""
    global _TRANSLATION
    if _TRANSLATION is None:
        _load_translation()
    return _TRANSLATION.ngettext(singular, plural, n)
