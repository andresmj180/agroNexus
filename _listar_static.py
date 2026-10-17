import os
root = os.path.dirname(os.path.abspath(__file__))
static = os.path.join(root, "static")
print("STATIC:", static)
print("EXISTS:", os.path.isdir(static))
if os.path.isdir(static):
    for dirpath, dirnames, filenames in os.walk(static):
        for f in filenames:
            p = os.path.join(dirpath, f)
            print(os.path.relpath(p, root), os.path.getsize(p))
