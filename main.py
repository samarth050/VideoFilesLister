import tkinter as tk
from app import FileListerApp

if __name__ == "__main__":
    root = tk.Tk()
    root.title("File Lister Database Manager")
    root.geometry("1700x820")
    root.minsize(1500, 700)

    app = FileListerApp(root)
    root.mainloop()
