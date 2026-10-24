import os
raiz = os.path.dirname(os.path.abspath(__file__))
for carpeta in ("img", "static", "icon"):
    p = os.path.join(raiz, carpeta)
    print("===", carpeta, "===")
    if not os.path.isdir(p):
        print("(no existe)")
        continue
    for dirpath, dirnames, filenames in os.walk(p):
        rel = os.path.relpath(dirpath, raiz)
        print(f"[{rel}]")
        for n in sorted(filenames):
            print(" ", n, os.path.getsize(os.path.join(dirpath, n)))
