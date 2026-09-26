"""Compatibility shortcut for local Core simulation."""
import sys
from dzmm_bot.manage import main

if __name__ == "__main__":
    sys.argv.insert(1, "simulate")
    main()
