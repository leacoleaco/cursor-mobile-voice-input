#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Compile .po files to .mo for gettext. Run before packaging or when updating translations."""
import os
import sys

def main():
    try:
        from pythongettext.msgfmt import Msgfmt
    except ImportError:
        print("Install python-gettext: pip install python-gettext")
        sys.exit(1)

    base = os.path.join(os.path.dirname(os.path.abspath(__file__)), "locales")
    for lang in ["zh_CN", "en"]:
        po = os.path.join(base, lang, "LC_MESSAGES", "messages.po")
        mo = os.path.join(base, lang, "LC_MESSAGES", "messages.mo")
        if not os.path.exists(po):
            print(f"Skip {lang}: {po} not found")
            continue
        try:
            m = Msgfmt(po)
            with open(mo, "wb") as f:
                f.write(m.get())
            print(f"Compiled: {lang}")
        except Exception as e:
            print(f"Error compiling {lang}: {e}")
            sys.exit(1)

if __name__ == "__main__":
    main()
