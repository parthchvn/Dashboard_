"""python 02_build_market_levels.py MARKET_ID --level all"""
import sys
from xvi.__main__ import main

if __name__ == "__main__":
    sys.argv.insert(1,"build")
    main()
