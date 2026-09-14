"""python 01_ingest_polymarket.py --trades ..."""
import sys
from xvi.__main__ import main

if __name__ == "__main__":
    sys.argv.insert(1,"ingest")
    main()
