"""Write the standard EICAR antivirus test file (harmless, but every scanner
flags it on purpose). Split in two pieces so this script itself is not flagged.
Your computer's own antivirus may still delete the output file: that is normal.

    python make_eicar.py eicar-test.txt
"""
import sys

a = "X5O!P%@AP[4\\PZX54(P^)7CC)7}$"
b = "EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"
with open(sys.argv[1] if len(sys.argv) > 1 else "eicar-test.txt", "w") as f:
    f.write(a + b)
print("written")
