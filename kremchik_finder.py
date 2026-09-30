#!/usr/bin/env python3
"""
Kremchik Finder (GUI): ищет продавца на kremchik.ua, у которого есть сразу
все нужные ароматы, чтобы платить за одну доставку.

Зависимости:  pip install requests beautifulsoup4
Запуск:       python kremchik_gui.py
Сборка exe:   pyinstaller --onefile --windowed --name KremchikFinder kremchik_gui.py
"""
import queue
import re
import threading
import time
import tkinter as tk
import webbrowser
from collections import defaultdict
from tkinter import messagebox, ttk

import requests
from bs4 import BeautifulSoup, Tag

BASE = "https://kremchik.ua"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; personal-perfume-finder)"}
DELAY = 1.5  # пауза между запросами к сайту
SHOP_RE = re.compile(r"/customshop-(\d+)/")

# ───────────────────────── переводы ─────────────────────────

STRINGS = {
    "ru": {
        "lang_btn": "Українська",  # кнопка показывает язык, на который переключит
        "title": "Kremchik Finder — поиск продавца с нужными ароматами",
        "top_label": "ID или ссылка на страницу аромата (kremchik.ua/item-XXXXX/):",
        "row_label": "Аромат {i}:",
        "add": "+ Добавить аромат",
        "min_ml": "   Мин. объём каждого, мл:",
        "search": "Найти продавца",
        "ready": "Готово к поиску.",
        "loading": "Загружаю аромат {i} из {n} (ID {id})…",
        "done": "Готово.",
        "error": "Ошибка.",
        "empty_title": "Пусто",
        "empty_msg": "Введи хотя бы один аромат.",
        "input_err_title": "Ошибка ввода",
        "input_err": "Не понял, что это за аромат: «{arg}»",
        "one_title": "Один аромат",
        "one_msg": "Указан только один аромат. Всё равно искать?",
        "searching": "Ищу…",
        "err_net": "Не удалось загрузить страницу: {e}",
        "err_generic": "Ошибка: {e}",
        "offers_line": "• {title}: офферов {n}\n",
        "nothing": "\nНичего не найдено. Если офферы у ароматов точно есть — "
                   "возможно, парсер не подошёл к разметке страницы.\n",
        "full_head": "\nПродавцы со ВСЕМИ ароматами:\n",
        "partial_head": "\nПолного совпадения нет. Лучшие частичные варианты:\n",
        "shop_sum": "  {c}/{n}, сумма ≈ {total} ₴\n",
        "avail": ", доступно {v} мл",
        "price_unknown": "цена ?",
        "absent": "нет",
        "kind_rest": "остаток",
        "kind_split": "распив",
        "price_note": "\nЦена указана за единицу (за 1 мл при распиве, за флакон при остатке).\n",
    },
    "uk": {
        "lang_btn": "Русский",
        "title": "Kremchik Finder — пошук продавця з потрібними ароматами",
        "top_label": "ID або посилання на сторінку аромату (kremchik.ua/item-XXXXX/):",
        "row_label": "Аромат {i}:",
        "add": "+ Додати аромат",
        "min_ml": "   Мін. об'єм кожного, мл:",
        "search": "Знайти продавця",
        "ready": "Готово до пошуку.",
        "loading": "Завантажую аромат {i} з {n} (ID {id})…",
        "done": "Готово.",
        "error": "Помилка.",
        "empty_title": "Порожньо",
        "empty_msg": "Введи хоча б один аромат.",
        "input_err_title": "Помилка вводу",
        "input_err": "Не зрозумів, що це за аромат: «{arg}»",
        "one_title": "Один аромат",
        "one_msg": "Вказано лише один аромат. Все одно шукати?",
        "searching": "Шукаю…",
        "err_net": "Не вдалося завантажити сторінку: {e}",
        "err_generic": "Помилка: {e}",
        "offers_line": "• {title}: пропозицій {n}\n",
        "nothing": "\nНічого не знайдено. Якщо пропозиції в ароматів точно є — "
                   "можливо, парсер не підійшов до розмітки сторінки.\n",
        "full_head": "\nПродавці з УСІМА ароматами:\n",
        "partial_head": "\nПовного збігу немає. Найкращі часткові варіанти:\n",
        "shop_sum": "  {c}/{n}, сума ≈ {total} ₴\n",
        "avail": ", доступно {v} мл",
        "price_unknown": "ціна ?",
        "absent": "немає",
        "kind_rest": "залишок",
        "kind_split": "розпив",
        "price_note": "\nЦіну вказано за одиницю (за 1 мл при розпиві, за флакон при залишку).\n",
    },
}


# ───────────────────────── логика поиска ─────────────────────────

def parse_item_id(arg: str) -> int:
    m = re.search(r"item-(\d+)", arg) or re.fullmatch(r"\s*(\d+)\s*", arg)
    if not m:
        raise ValueError(arg)  # текст ошибки формирует интерфейс (зависит от языка)
    return int(m.group(1))


def offer_text(a: Tag) -> str:
    """Текст оффера = то, что стоит в разметке перед ссылкой 'купить на витрине'."""
    parts = []
    for sib in a.previous_siblings:
        if isinstance(sib, Tag) and (
            sib.name == "br"
            or sib.find("a", href=SHOP_RE)
            or (sib.name == "a" and SHOP_RE.search(sib.get("href", "")))
        ):
            break
        parts.append(sib.get_text(" ", strip=True) if isinstance(sib, Tag) else str(sib))
    text = " ".join(reversed(parts)).strip()
    return text or a.parent.get_text(" ", strip=True)


def fetch_offers(item_id: int):
    r = requests.get(f"{BASE}/item-{item_id}/", headers=HEADERS, timeout=20)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    h1 = soup.find("h1")
    title = h1.get_text(strip=True) if h1 else f"item-{item_id}"

    offers = []
    for a in soup.find_all("a", href=SHOP_RE):
        if "купить на витрине" not in a.get_text(" ", strip=True).lower():
            continue
        shop_id = int(SHOP_RE.search(a["href"]).group(1))
        shop_name = re.sub(r"(?i)купить на витрине", "", a.get_text(" ", strip=True)).strip()
        text = offer_text(a)

        price_m = re.search(r"(\d[\d\s]*)\s*₴", text)
        if "остаток во флаконе" in text.lower():
            kind = "rest"
            vol_m = re.search(r"остаток во флаконе\s+(\d+)", text, re.I)
        else:
            kind = "split"
            vol_m = re.search(r"свободно\s+(\d+)\s*мл", text, re.I)

        offers.append({
            "shop_id": shop_id,
            "shop": shop_name,
            "kind": kind,
            "price": int(re.sub(r"\s", "", price_m.group(1))) if price_m else None,
            "volume": int(vol_m.group(1)) if vol_m else None,
        })
    return title, offers


def find_sellers(ids, min_ml, progress=lambda i, n, iid: None):
    titles = {}
    shops = defaultdict(lambda: {"name": "", "items": {}})
    for i, iid in enumerate(ids):
        if i:
            time.sleep(DELAY)
        progress(i + 1, len(ids), iid)
        title, offers = fetch_offers(iid)
        titles[iid] = (title, len(offers))
        for o in offers:
            if o["volume"] is not None and o["volume"] < min_ml:
                continue
            s = shops[o["shop_id"]]
            s["name"] = o["shop"]
            best = s["items"].get(iid)
            if best is None or (o["price"] or 10**9) < (best["price"] or 10**9):
                s["items"][iid] = o
    ranked = sorted(
        shops.items(),
        key=lambda kv: (-len(kv[1]["items"]),
                        sum(o["price"] or 0 for o in kv[1]["items"].values())),
    )
    return titles, ranked


# ───────────────────────── интерфейс ─────────────────────────

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.lang = "ru"
        self.geometry("720x640")
        self.minsize(560, 480)
        self.q = queue.Queue()
        self.rows = []  # [(frame, label, entry)]
        # что сейчас показано — чтобы перерисовать при смене языка
        self.status_state = ("ready", {})
        self.view = None  # None | ("searching",) | ("error", key, detail) | ("results", ids, min_ml, res)

        top = ttk.Frame(self, padding=10)
        top.pack(fill="x")

        header = ttk.Frame(top)
        header.pack(fill="x")
        self.top_label = ttk.Label(header)
        self.top_label.pack(side="left", anchor="w")
        self.lang_btn = ttk.Button(header, width=12, command=self.toggle_lang)
        self.lang_btn.pack(side="right")

        self.rows_frame = ttk.Frame(top)
        self.rows_frame.pack(fill="x", pady=(6, 0))

        ctl = ttk.Frame(top)
        ctl.pack(fill="x", pady=8)
        self.add_btn = ttk.Button(ctl, command=self.add_row)
        self.add_btn.pack(side="left")
        self.ml_label = ttk.Label(ctl)
        self.ml_label.pack(side="left")
        self.ml_var = tk.StringVar(value="1")
        ttk.Spinbox(ctl, from_=1, to=500, width=5, textvariable=self.ml_var).pack(side="left", padx=4)
        self.search_btn = ttk.Button(ctl, command=self.on_search)
        self.search_btn.pack(side="right")

        self.status = ttk.Label(self, padding=(10, 0))
        self.status.pack(fill="x")

        res = ttk.Frame(self, padding=10)
        res.pack(fill="both", expand=True)
        self.text = tk.Text(res, wrap="word", state="disabled", font=("Segoe UI", 10))
        sb = ttk.Scrollbar(res, command=self.text.yview)
        self.text.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.text.pack(side="left", fill="both", expand=True)
        self.text.tag_configure("head", font=("Segoe UI", 11, "bold"), spacing1=8)
        self.text.tag_configure("ok", foreground="#1a7f37")
        self.text.tag_configure("no", foreground="#b42318")
        self.text.tag_configure("dim", foreground="#666666")

        self.add_row()
        self.add_row()
        self.apply_language()
        self.after(100, self.poll)

    # --- язык ---
    def t(self, key, **kw):
        s = STRINGS[self.lang][key]
        return s.format(**kw) if kw else s

    def toggle_lang(self):
        self.lang = "uk" if self.lang == "ru" else "ru"
        self.apply_language()

    def apply_language(self):
        self.title(self.t("title"))
        self.top_label.config(text=self.t("top_label"))
        self.lang_btn.config(text=self.t("lang_btn"))
        self.add_btn.config(text=self.t("add"))
        self.ml_label.config(text=self.t("min_ml"))
        self.search_btn.config(text=self.t("search"))
        self.renumber()
        self.set_status(*self.status_state[:1], **self.status_state[1])
        self.render()

    def set_status(self, key, **kw):
        self.status_state = (key, kw)
        self.status.config(text=self.t(key, **kw))

    # --- поля ввода ---
    def add_row(self):
        frame = ttk.Frame(self.rows_frame)
        frame.pack(fill="x", pady=2)
        label = ttk.Label(frame, width=10)
        label.pack(side="left")
        entry = ttk.Entry(frame)
        entry.pack(side="left", fill="x", expand=True)
        entry.bind("<Return>", lambda e: self.on_search())
        # Ctrl+V / Ctrl+C / Ctrl+A работают и на русской раскладке
        entry.bind("<Control-KeyPress>", self._ctrl_keys)
        btn = ttk.Button(frame, text="✕", width=3, command=lambda f=frame: self.remove_row(f))
        btn.pack(side="left", padx=(6, 0))
        self.rows.append((frame, label, entry))
        self.renumber()
        entry.focus_set()

    def remove_row(self, frame):
        if len(self.rows) <= 1:
            self.rows[0][2].delete(0, "end")
            return
        self.rows = [r for r in self.rows if r[0] is not frame]
        frame.destroy()
        self.renumber()

    def renumber(self):
        for i, (_, label, _) in enumerate(self.rows, 1):
            label.config(text=self.t("row_label", i=i))

    @staticmethod
    def _ctrl_keys(e):
        w = e.widget
        actions = {86: "<<Paste>>", 67: "<<Copy>>", 88: "<<Cut>>"}  # V, C, X по коду клавиши
        if e.keycode in actions:
            w.event_generate(actions[e.keycode])
            return "break"
        if e.keycode == 65:  # A
            w.select_range(0, "end")
            w.icursor("end")
            return "break"

    # --- поиск ---
    def on_search(self):
        raw = [r[2].get().strip() for r in self.rows if r[2].get().strip()]
        if not raw:
            messagebox.showwarning(self.t("empty_title"), self.t("empty_msg"))
            return
        try:
            ids = list(dict.fromkeys(parse_item_id(x) for x in raw))  # без дублей
        except ValueError as e:
            messagebox.showerror(self.t("input_err_title"), self.t("input_err", arg=e.args[0]))
            return
        if len(ids) < 2:
            if not messagebox.askyesno(self.t("one_title"), self.t("one_msg")):
                return
        try:
            min_ml = max(1, int(self.ml_var.get()))
        except ValueError:
            min_ml = 1

        self.search_btn.config(state="disabled")
        self.view = ("searching",)
        self.render()
        threading.Thread(target=self.worker, args=(ids, min_ml), daemon=True).start()

    def worker(self, ids, min_ml):
        try:
            res = find_sellers(
                ids, min_ml,
                progress=lambda i, n, iid: self.q.put(("progress", (i, n, iid))),
            )
            self.q.put(("done", (ids, min_ml, res)))
        except requests.RequestException as e:
            self.q.put(("error", ("err_net", str(e))))
        except Exception as e:  # noqa: BLE001
            self.q.put(("error", ("err_generic", str(e))))

    def poll(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "progress":
                    i, n, iid = payload
                    self.set_status("loading", i=i, n=n, id=iid)
                elif kind == "error":
                    key, detail = payload
                    self.set_status("error")
                    self.search_btn.config(state="normal")
                    self.view = ("error", key, detail)
                    self.render()
                elif kind == "done":
                    self.search_btn.config(state="normal")
                    self.set_status("done")
                    self.view = ("results", *payload)
                    self.render()
        except queue.Empty:
            pass
        self.after(100, self.poll)

    # --- вывод ---
    def set_text(self, chunks):
        self.text.config(state="normal")
        self.text.delete("1.0", "end")
        for s, tag in chunks:
            self.text.insert("end", s, tag)
        self.text.config(state="disabled")

    def render(self):
        v = self.view
        if v is None:
            self.set_text([])
        elif v[0] == "searching":
            self.set_text([(self.t("searching"), "dim")])
        elif v[0] == "error":
            self.set_text([(self.t(v[1], e=v[2]), "no")])
        elif v[0] == "results":
            self.show_results(*v[1:])

    def show_results(self, ids, min_ml, res):
        titles, ranked = res
        self.text.config(state="normal")
        self.text.delete("1.0", "end")

        for iid in ids:
            title, n = titles[iid]
            self.text.insert("end", self.t("offers_line", title=title, n=n), "dim")

        full = [kv for kv in ranked if len(kv[1]["items"]) == len(ids)]
        show = full if full else ranked[:5]
        if not show:
            self.text.insert("end", self.t("nothing"), "no")
        else:
            self.text.insert("end", self.t("full_head" if full else "partial_head"), "head")
        for n, (shop_id, s) in enumerate(show):
            total = sum(o["price"] or 0 for o in s["items"].values())
            url = f"{BASE}/customshop-{shop_id}/"
            tag = f"link{n}"
            self.text.insert("end", f"\n{s['name']}", "head")
            self.text.insert("end", self.t("shop_sum", c=len(s["items"]), n=len(ids), total=total), "dim")
            self.text.insert("end", url + "\n", (tag,))
            self.text.tag_configure(tag, foreground="#0b57d0", underline=True)
            self.text.tag_bind(tag, "<Button-1>", lambda e, u=url: webbrowser.open(u))
            self.text.tag_bind(tag, "<Enter>", lambda e: self.text.config(cursor="hand2"))
            self.text.tag_bind(tag, "<Leave>", lambda e: self.text.config(cursor=""))
            for iid in ids:
                o = s["items"].get(iid)
                name = titles[iid][0]
                if o:
                    vol = self.t("avail", v=o["volume"]) if o["volume"] is not None else ""
                    price = f"{o['price']} ₴" if o["price"] is not None else self.t("price_unknown")
                    kind = self.t("kind_" + o["kind"])
                    self.text.insert("end", f"   ✓ {name}: {kind}, {price}{vol}\n", "ok")
                else:
                    self.text.insert("end", f"   ✗ {name}: {self.t('absent')}\n", "no")
        self.text.insert("end", self.t("price_note"), "dim")
        self.text.config(state="disabled")


if __name__ == "__main__":
    App().mainloop()
