from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from .credential_store import get_deepseek_api_key, save_deepseek_api_key


def prompt_for_deepseek_api_key(*, force: bool = False) -> str | None:
    existing = get_deepseek_api_key()
    if existing and not force:
        return existing

    result: dict[str, str | None] = {"key": None}
    root = tk.Tk()
    root.title("Sportsbeams Payroll Agent - LLM 设置")
    root.resizable(False, False)
    root.geometry("520x210")
    frame = ttk.Frame(root, padding=20)
    frame.pack(fill="both", expand=True)
    ttk.Label(frame, text="首次使用需要配置 DeepSeek API Key", font=("Microsoft YaHei UI", 12, "bold")).pack(anchor="w")
    ttk.Label(frame, text="Key 将由 Windows 当前用户加密保存，不会写入项目或安装包。", wraplength=470).pack(anchor="w", pady=(8, 12))
    value = tk.StringVar()
    entry = ttk.Entry(frame, textvariable=value, show="●", width=62)
    entry.pack(fill="x")
    entry.focus_set()

    def save() -> None:
        api_key = value.get().strip()
        if not api_key.startswith("sk-") or len(api_key) < 20:
            messagebox.showerror("格式错误", "请输入有效的 DeepSeek API Key。", parent=root)
            return
        try:
            save_deepseek_api_key(api_key)
        except Exception as error:
            messagebox.showerror("保存失败", str(error), parent=root)
            return
        result["key"] = api_key
        messagebox.showinfo("保存成功", "API Key 已加密保存到当前 Windows 用户。", parent=root)
        root.destroy()

    buttons = ttk.Frame(frame)
    buttons.pack(fill="x", pady=(16, 0))
    ttk.Button(buttons, text="取消", command=root.destroy).pack(side="right")
    ttk.Button(buttons, text="保存", command=save).pack(side="right", padx=(0, 8))
    root.protocol("WM_DELETE_WINDOW", root.destroy)
    root.mainloop()
    return result["key"]


def main() -> int:
    return 0 if prompt_for_deepseek_api_key(force=True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
