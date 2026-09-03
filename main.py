import tkinter as tk
from tkinter import messagebox

from single_instance import acquire

if __name__ == "__main__":
    if not acquire():
        # Create a hidden root solely so the user gets a clear GUI message
        # when launching the windowed executable a second time.
        notice_root = tk.Tk()
        notice_root.withdraw()
        messagebox.showinfo(
            "File Lister is already running",
            "File Lister is already open. Only one copy can run at a time.",
            parent=notice_root,
        )
        notice_root.destroy()
        raise SystemExit(0)

    from app import FileListerApp

    root = tk.Tk()
    root.title("File Lister Database Manager")
    root.geometry("1700x820")
    root.minsize(1500, 700)

    app = FileListerApp(root)
    root.mainloop()
